from datetime import timedelta

from licita.conversacion import bienvenida, es_saludo, responder
from licita.db import Calce, Empresa, Licitacion, Suscripcion, ahora, hora_chile


def _base(s, *, plan="pro", estado="prueba"):
    cierre = hora_chile(ahora()).replace(tzinfo=None) + timedelta(days=4)
    s.add_all([
        Licitacion(codigo="1-1-LE26", nombre="Artículos de aseo para CESFAM", estado_codigo=5,
                   organismo="Municipalidad de Chillán", region="Región del Ñuble", fecha_cierre=cierre),
        Licitacion(codigo="2-2-LE26", nombre="Artículos de aseo para liceo", estado_codigo=5,
                   organismo="Slep Santiago", region="Región Metropolitana de Santiago", fecha_cierre=cierre),
        Licitacion(codigo="3-3-COT26", tipo="COT", nombre="Bolsas de basura y artículos de aseo", estado_codigo=5,
                   organismo="Hospital de San Carlos", region="Región del Ñuble", fecha_cierre=cierre),
        Licitacion(codigo="4-4-LE26", nombre="Notebooks", estado_codigo=5, region="Región del Ñuble", fecha_cierre=cierre),
    ])
    e = Empresa(nombre="Aseo Sur", descripcion="Insumos de aseo", regiones=["Ñuble"], palabras_clave=["artículos de aseo"],
                whatsapp="56989299524", plan=plan)
    s.add(e)
    s.flush()
    s.add(Suscripcion(empresa_id=e.id, plan="pro", estado=estado, vigente_hasta=ahora() + timedelta(days=7)))
    s.commit()
    return e


def test_saludos():
    assert all(es_saludo(t) for t in ("hola", "hola!", "¡hola!", "buenas tardes", "buenos días", "hola 👋"))
    assert not any(es_saludo(t) for t in ("precios", "1", "¿piden boleta de garantía?", "holanda " * 10))


def test_primer_saludo_muestra_lo_abierto_hoy_para_la_empresa(Sesion):
    with Sesion() as s:
        e = _base(s)
        [texto] = responder(s, e, "texto", "Hola", url_publica="https://calza.cl")
    assert texto.startswith("¡Hola, Aseo Sur! 👋") and "el próximo día hábil" in texto
    assert "1. *Artículos de aseo para CESFAM*" in texto and "https://calza.cl/licitacion/1-1-LE26" in texto
    assert "⚡ Compra Ágil: Bolsas de basura" in texto  # plan Pro: incluye Compras Ágiles
    assert "liceo" not in texto and "Notebooks" not in texto  # otra región y otro rubro
    assert "cierra en 4 días" in texto and "Marca 👍 o 👎" in texto


def test_sin_tarjeta_recuerda_activar_la_prueba_y_sin_compras_agiles(Sesion):
    with Sesion() as s:
        e = _base(s, plan="gratis", estado="pendiente")
        texto = bienvenida(s, e)
    assert "Te falta un paso para empezar tu prueba gratis" in texto and "*CUENTA*" in texto
    assert "Compra Ágil" not in texto and "CESFAM" in texto


def test_despues_del_primer_resumen_el_saludo_muestra_la_ayuda(Sesion):
    with Sesion() as s:
        e = _base(s)
        s.add(Calce(empresa_id=e.id, licitacion_codigo="1-1-LE26", puntaje=90, notificado=True,
                    notificado_en=ahora(), posicion_resumen=1))
        s.commit()
        [texto] = responder(s, e, "texto", "hola")
    assert "Soy el asistente de Calza 🤖" in texto


def _registrada_sin_activar(Sesion):
    from licita.suscripciones import DatosRegistro, registrar_empresa

    datos = DatosRegistro(nombre="Aseo Sur", rut="76.123.456-0", razon_social="Aseo Sur SpA", giro="Venta de aseo",
                          direccion="Av. Prat 123", comuna="Chillán", email="hola@aseosur.cl", whatsapp="+56 9 8929 9524",
                          descripcion="Vendemos artículos de aseo a municipios y hospitales.", regiones=["Ñuble"],
                          plan="pro", periodicidad="mensual", acepta_whatsapp=True, acepta_terminos=True)
    with Sesion() as s:
        s.add(Licitacion(codigo="1-1-LE26", nombre="Artículos de aseo para CESFAM", estado_codigo=5,
                         organismo="Municipalidad de Chillán", region="Región del Ñuble",
                         fecha_cierre=hora_chile(ahora()).replace(tzinfo=None) + timedelta(days=4)))
        s.commit()
        empresa, _ = registrar_empresa(s, datos, confirmar_whatsapp=True)
        return empresa.id


class WAFalso:
    def __init__(self):
        self.enviados = []

    def enviar_texto(self, telefono, texto):
        self.enviados.append(texto)
        return f"wamid.{len(self.enviados)}"


def _mensaje(texto, wamid):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": "56989299524", "id": wamid, "type": "text", "text": {"body": texto}}]}}]}]}


def test_la_prueba_empieza_al_escribir_desde_su_whatsapp(Sesion):
    from licita.conversacion import procesar_webhook
    from licita.notificaciones import enviar_resumenes
    from licita.suscripciones import suscripcion_de

    empresa_id = _registrada_sin_activar(Sesion)
    with Sesion() as s:
        e = s.get(Empresa, empresa_id)
        sus = suscripcion_de(s, e)
        assert (sus.estado, sus.pendiente_de, e.plan) == ("pendiente", "whatsapp", "gratis")
        assert enviar_resumenes(s, None).plantillas == 0  # sin activar no recibe nada ni gasta IA

        wa = WAFalso()
        procesar_webhook(s, wa, _mensaje("ok gracias", "w1"), url_publica="https://calza.cl")  # cualquier mensaje activa
        sus = suscripcion_de(s, e)
        assert (sus.estado, sus.pendiente_de, e.plan) == ("prueba", "", "pro")
        assert timedelta(days=6, hours=23) < sus.vigente_hasta - ahora() <= timedelta(days=7)
        [texto] = wa.enviados
        assert "✅ *Empezó tu prueba gratis.*" in texto and "sin tarjeta" in texto and "CESFAM" in texto

        procesar_webhook(s, wa, _mensaje("hola", "w2"))  # el segundo mensaje ya no reactiva ni repite el aviso
        assert "Empezó tu prueba" not in wa.enviados[-1]


def test_plan_gratis_no_analiza_bases(Sesion):
    from licita.conversacion import procesar_documento

    with Sesion() as s:
        e = _base(s, plan="gratis", estado="vencida")
        enviados = []
        procesar_documento(s, None, e, {"document": {}}, analisis=object(), momento=ahora(), enviar=enviados.append)
    assert "planes Pyme y Pro" in enviados[0] and "*CUENTA*" in enviados[0]
