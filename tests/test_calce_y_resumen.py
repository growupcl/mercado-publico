from datetime import date, datetime
from types import SimpleNamespace

from licita.calce import buscar_calces, candidatas, puntaje_prefiltro
from licita.db import Calce, Empresa
from licita.ia import AsistenteIA, ClasificacionLicitacion, EvaluacionCalce, EvaluacionesCalce, clasificar_pendientes
from licita.resumen import formato_pesos, resumen_diario
from licita.sync import sincronizar_licitaciones

MOMENTO = datetime(2026, 10, 7, 9, 0)


class ClaudeFalso:
    """Imita client.messages.parse devolviendo respuestas preparadas por tipo de salida."""

    def __init__(self, respuestas, stop_reason="end_turn"):
        self.respuestas = respuestas
        self.stop_reason = stop_reason
        self.llamadas = []
        self.messages = self

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        salida = self.respuestas[kwargs["output_format"]]
        salida = salida(kwargs) if callable(salida) else salida
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=salida)


def _empresa_aseo(s):
    e = Empresa(
        nombre="Aseo Sur",
        descripcion="Vendemos insumos de aseo y limpieza en el Biobío",
        regiones=["Biobío", "Ñuble"],
        monto_max=30_000_000,
        palabras_clave=["detergente", "guantes", "bolsas de basura", "artículos de aseo", "limpieza"],
    )
    s.add(e)
    s.commit()
    return e


def _cargar(Sesion, cliente_mp):
    s = Sesion()
    sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
    return s


def test_prefiltro_prioriza_rubro_y_respeta_region(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = _empresa_aseo(s)
    sel = candidatas(s, e, momento=MOMENTO)
    # Solo pasa la de aseo: áreas verdes es de otra región y la sede social ya está adjudicada.
    assert [lic.codigo for lic, _ in sel] == ["1234-56-LE26"]
    assert sel[0][1] > 0.5


def test_prefiltro_descarta_por_monto(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = _empresa_aseo(s)
    e.monto_max = 1_000_000
    assert candidatas(s, e, momento=MOMENTO) == []


def test_prefiltro_descarta_licitaciones_cerradas(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = _empresa_aseo(s)
    assert candidatas(s, e, momento=datetime(2026, 10, 21)) == []


def test_buscar_calces_guarda_resultado_y_no_repite(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = _empresa_aseo(s)
    falso = ClaudeFalso({
        EvaluacionesCalce: EvaluacionesCalce(evaluaciones=[
            EvaluacionCalce(codigo="1234-56-LE26", puntaje=130, razon=" Piden detergente, guantes y bolsas: es tu rubro. "),
            EvaluacionCalce(codigo="CODIGO-INVENTADO", puntaje=90, razon="x"),
        ])
    })
    calces = buscar_calces(s, e, AsistenteIA(falso), momento=MOMENTO)
    assert [(c.licitacion_codigo, c.puntaje) for c in calces] == [("1234-56-LE26", 100)]
    assert calces[0].razon == "Piden detergente, guantes y bolsas: es tu rubro."
    assert "<perfil_empresa>" in falso.llamadas[0]["messages"][0]["content"]
    # Una segunda búsqueda no vuelve a evaluar lo mismo (no gasta IA).
    assert buscar_calces(s, e, AsistenteIA(falso), momento=MOMENTO) == []
    assert len(falso.llamadas) == 1


def test_resumen_diario_formato_whatsapp(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = _empresa_aseo(s)
    s.add_all([
        Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=92, razon="Es tu rubro."),
        Calce(empresa_id=e.id, licitacion_codigo="2345-12-L126", puntaje=40, razon="No corresponde."),
    ])
    s.commit()
    texto = resumen_diario(s, e, momento=MOMENTO, marcar_notificado=True)
    assert "Hoy encontramos 1 licitación para ti" in texto
    assert "*Adquisición de insumos de aseo para CESFAM* (92% de calce)" in texto
    assert "Monto estimado: $8.500.000 · Cierra: 20-10-2026 15:00" in texto
    assert "idLicitacion=1234-56-LE26" in texto
    assert "Mantención" not in texto
    # Ya notificado: el día siguiente no se repite.
    assert resumen_diario(s, e, momento=MOMENTO) is None


def test_clasificar_pendientes_solo_publicadas(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    clasif = ClasificacionLicitacion(
        resumen="Compra de insumos de aseo.", tipo_compra="bienes",
        rubros=["artículos de aseo"], palabras_clave=["detergente"], requisitos_destacados=[],
    )
    falso = ClaudeFalso({ClasificacionLicitacion: clasif})
    assert clasificar_pendientes(s, AsistenteIA(falso)) == (2, 0)
    assert len(falso.llamadas) == 2
    assert all(ll["model"] == "claude-haiku-4-5" for ll in falso.llamadas)


def test_rechazo_del_modelo_cuenta_como_error(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    falso = ClaudeFalso({ClasificacionLicitacion: None}, stop_reason="refusal")
    assert clasificar_pendientes(s, AsistenteIA(falso)) == (0, 2)


def test_formato_pesos():
    assert formato_pesos(1234567.4) == "$1.234.567"
    assert formato_pesos(None) == "no informado"


def test_puntaje_prefiltro_ignora_tildes_y_plurales(Sesion, cliente_mp):
    s = _cargar(Sesion, cliente_mp)
    e = Empresa(nombre="X", descripcion="", regiones=[], palabras_clave=["guante", "LIMPIEZA"])
    from licita.db import Licitacion
    assert puntaje_prefiltro(e, s.get(Licitacion, "1234-56-LE26")) == 1.0
