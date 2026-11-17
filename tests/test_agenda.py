from datetime import datetime, timedelta

from licita.agenda import avisos_pendientes, ejecutar_agenda, hitos, texto_resultado
from licita.conversacion import procesar_webhook
from licita.db import Calce, Empresa, Licitacion, Postulacion, Suscripcion

# Noviembre: Chile está en UTC-3, así que 11:40 UTC son las 8:40 en Chile.
CL = datetime(2026, 11, 18, 8, 40)
UTC = CL + timedelta(hours=3)
TEL = "56989299524"


def _lic(codigo="1-1-LE26", estado=5, **kw):
    fechas = {"FechaFinal": "2026-11-19T12:00:00", "FechaPubRespuestas": "2026-11-21T18:00:00",
              "FechaVisitaTerreno": None, "FechaCierre": "2026-11-25T15:00:00"}
    fechas.update(kw.pop("fechas", {}))
    return Licitacion(codigo=codigo, nombre=kw.pop("nombre", "Artículos de aseo para CESFAM"), estado_codigo=estado,
                      descripcion="", tipo="", region="", comuna="", moneda="CLP",
                      organismo="Municipalidad de Chillán", monto_estimado=kw.pop("monto", 5_000_000),
                      fecha_cierre=datetime.fromisoformat(fechas["FechaCierre"]), raw={"Fechas": fechas, **kw.pop("raw", {})},
                      items=kw.pop("items", []), **kw)


def test_hitos_y_avisos_en_su_momento():
    lic = _lic(fechas={"FechaVisitaTerreno": "2026-11-20T10:00:00"}, raw={"DireccionVisita": "Av. Prat 123"})
    assert [h.clave for h in hitos(lic)] == ["preguntas", "visita", "respuestas", "cierre"]
    post = Postulacion(avisos=[])
    [(clave, texto)] = avisos_pendientes(post, lic, CL)  # el día antes del fin de preguntas
    assert clave == "preguntas" and "mañana a las 12:00" in texto
    post.avisos = ["preguntas"]
    assert avisos_pendientes(post, lic, CL) == []  # no se repite
    [(clave, texto)] = avisos_pendientes(post, lic, datetime(2026, 11, 19, 10, 0))
    assert clave == "visita" and "Av. Prat 123" in texto
    claves = [c for c, _ in avisos_pendientes(post, lic, datetime(2026, 11, 23, 8, 40))]
    assert claves == ["visita", "respuestas", "cierre_2d"] or claves == ["respuestas", "cierre_2d"]
    post.avisos = ["preguntas", "visita", "respuestas", "cierre_2d"]
    [(clave, texto)] = avisos_pendientes(post, lic, datetime(2026, 11, 25, 8, 40))
    assert clave == "cierre_hoy" and "Hoy a las 15:00 cierra" in texto
    assert avisos_pendientes(Postulacion(avisos=[]), lic, datetime(2026, 11, 25, 16, 0)) == []  # ya cerró


def test_textos_de_resultado():
    empresa = Empresa(nombre="Aseo Sur", descripcion="", rut="761234560")
    items = [{"producto": "Guantes", "cantidad": 100,
              "adjudicacion": {"precio_unitario": 4500, "cantidad": 100, "proveedor_nombre": "Limpia SpA", "proveedor_rut": "77.777.777-7"}}]
    texto = texto_resultado(_lic(estado=8, items=items), empresa, "https://calza.cl")
    assert "Se *adjudicó* a Limpia SpA por $450.000 (el presupuesto era $5.000.000)" in texto
    assert "https://calza.cl/licitacion/1-1-LE26" in texto
    items[0]["adjudicacion"]["proveedor_rut"] = "76.123.456-0"
    assert "¡Ganaste!" in texto_resultado(_lic(estado=8, items=items), empresa)
    assert "desierta" in texto_resultado(_lic(estado=7), empresa)
    assert texto_resultado(_lic(estado=6), empresa) is None  # cerrada: aún sin resultado


class WAFalso:
    def __init__(self):
        self.textos, self.plantillas, self.botones = [], [], []

    def enviar_texto(self, telefono, texto):
        self.textos.append(texto)
        return "wamid.t"

    def enviar_botones(self, telefono, texto, botones):
        self.botones.append((texto, botones))
        return "wamid.b"

    def enviar_plantilla(self, telefono, nombre, *, idioma="es", parametros=None, payloads_botones=None):
        self.plantillas.append((nombre, parametros, payloads_botones))
        return "wamid.p"


class MPFalso:
    def __init__(self, raw):
        self.raw, self.consultas = raw, 0

    def licitacion(self, codigo):
        self.consultas += 1
        return self.raw


def _base(s, *, plan="pro"):
    lic = _lic()
    e = Empresa(nombre="Aseo Sur", descripcion="", whatsapp=TEL, plan=plan, rut="761234560")
    s.add_all([lic, e])
    s.flush()
    s.add(Calce(empresa_id=e.id, licitacion_codigo=lic.codigo, puntaje=90, razon="Es tu rubro."))
    s.add(Suscripcion(empresa_id=e.id, plan="pro", estado="prueba", vigente_hasta=UTC + timedelta(days=7)))
    s.commit()
    return e, lic


def _boton(payload, wamid):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": TEL, "id": wamid, "type": "button", "button": {"payload": payload, "text": "x"}}]}}]}]}


def _texto(texto, wamid):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": TEL, "id": wamid, "type": "text", "text": {"body": texto}}]}}]}]}


def test_voy_a_postular_agenda_y_quitar(Sesion):
    with Sesion() as s:
        e, lic = _base(s)
        wa = WAFalso()
        procesar_webhook(s, wa, _boton("POSTULO:1-1-LE26", "w1"), momento=UTC)
        texto = wa.textos[-1]
        assert "📌 Anotado: vas a postular a *Artículos de aseo para CESFAM*" in texto
        assert "• Último día de preguntas al foro: 19-11 a las 12:00" in texto and "• Cierre de ofertas: 25-11 a las 15:00" in texto
        assert s.query(Postulacion).one().activa and s.query(Calce).one().valoracion == 1  # si postula, le sirve
        procesar_webhook(s, wa, _texto("agenda", "w2"), momento=UTC)
        assert "1. *Artículos de aseo para CESFAM*" in wa.textos[-1] and "mañana a las 12:00" in wa.textos[-1]
        procesar_webhook(s, wa, _texto("quitar 1", "w3"), momento=UTC)
        assert "saqué" in wa.textos[-1] and not s.query(Postulacion).one().activa
        procesar_webhook(s, wa, _texto("agenda", "w4"), momento=UTC)
        assert "Tu agenda está vacía" in wa.textos[-1]


def test_tarea_diaria_avisa_plazos_y_resultado(Sesion):
    with Sesion() as s:
        e, lic = _base(s)
        s.add(Postulacion(empresa_id=e.id, licitacion_codigo=lic.codigo, avisos=[]))
        s.commit()
        wa = WAFalso()
        # Sin ventana abierta: va la plantilla con el botón al detalle.
        r = ejecutar_agenda(s, wa=wa, momento=UTC)
        assert r.avisos == 1 and wa.textos == []
        [(nombre, parametros, botones)] = wa.plantillas
        assert nombre == "recordatorio_postulacion" and botones == ["DETALLE:1-1-LE26"]
        assert parametros[0] == "Aseo Sur" and "preguntas en el foro cierra mañana a las 12:00" in parametros[2]
        assert ejecutar_agenda(s, wa=wa, momento=UTC).avisos == 0  # no se repite

        # Tras el cierre, consulta el resultado (una vez al día) y avisa quién ganó.
        adjudicada = {"CodigoExterno": lic.codigo, "Nombre": lic.nombre, "CodigoEstado": 8,
                      "Fechas": lic.raw["Fechas"], "Comprador": {"NombreOrganismo": "Municipalidad de Chillán"},
                      "MontoEstimado": 5_000_000,
                      "Items": {"Listado": [{"NombreProducto": "Guantes", "Cantidad": 100, "CodigoProducto": "46181504",
                                             "Adjudicacion": {"NombreProveedor": "Limpia SpA", "RutProveedor": "77.777.777-7",
                                                              "Cantidad": 100, "MontoUnitario": 4500}}]}}
        cliente = MPFalso(adjudicada)
        despues = datetime(2026, 12, 10, 11, 40)
        e.ultimo_mensaje_entrante = despues - timedelta(hours=1)  # ventana abierta: va como texto
        s.commit()
        r = ejecutar_agenda(s, wa=wa, cliente=cliente, url_publica="https://calza.cl", momento=despues)
        assert r.consultadas == 1 and r.resultados == 1
        assert "Se *adjudicó* a Limpia SpA" in wa.textos[-1]
        assert not s.query(Postulacion).one().activa
        assert ejecutar_agenda(s, wa=wa, cliente=cliente, momento=despues).resultados == 0
        assert cliente.consultas == 1


def test_sin_whatsapp_va_por_correo_y_plan_gratis_no_recibe(Sesion):
    class CorreoFalso:
        def __init__(self):
            self.enviados = []

        def enviar(self, para, asunto, texto, html=None):
            self.enviados.append((para, asunto, texto))

    with Sesion() as s:
        e, lic = _base(s)
        e.whatsapp_activo, e.email = False, "hola@aseosur.cl"
        s.add(Postulacion(empresa_id=e.id, licitacion_codigo=lic.codigo, avisos=[]))
        s.commit()
        correo = CorreoFalso()
        assert ejecutar_agenda(s, wa=WAFalso(), correo=correo, momento=UTC).avisos == 1
        assert correo.enviados[0][1] == "Tu postulación: Artículos de aseo para CESFAM"
        e.plan = "gratis"
        s.query(Postulacion).one().avisos = []
        s.commit()
        assert ejecutar_agenda(s, wa=WAFalso(), correo=correo, momento=UTC).avisos == 0
