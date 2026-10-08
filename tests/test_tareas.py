from datetime import date

from licita.db import Calce, Empresa, Licitacion
from licita.ia import AsistenteIA, ClasificacionLicitacion, EvaluacionCalce, EvaluacionesCalce
from licita.tareas import ejecutar_ciclo
from test_calce_y_resumen import ClaudeFalso


def _ia():
    clasif = ClasificacionLicitacion(resumen="Insumos de aseo.", tipo_compra="bienes", rubros=["aseo"],
                                     palabras_clave=["detergente", "guantes"], requisitos_destacados=[])

    def evaluar(kw):
        codigos = [c for c in ("1234-56-LE26", "2345-12-L126") if c in kw["messages"][0]["content"]]
        return EvaluacionesCalce(evaluaciones=[EvaluacionCalce(codigo=c, puntaje=80, razon="Calza.") for c in codigos])

    return AsistenteIA(ClaudeFalso({ClasificacionLicitacion: clasif, EvaluacionesCalce: evaluar}))


def test_ciclo_completo(Sesion, cliente_mp):
    with Sesion() as s:
        s.add_all([
            Empresa(nombre="Activa", descripcion="", regiones=[], palabras_clave=["detergente", "guantes"], plan="pyme", whatsapp="569111"),
            Empresa(nombre="Gratis", descripcion="", regiones=[], palabras_clave=["detergente"], plan="gratis", whatsapp="569222"),
        ])
        s.commit()
        r = ejecutar_ciclo(s, cliente_mp, _ia(), hoy=date(2026, 10, 7))
        assert r.errores == []
        assert r.licitaciones_nuevas == 3
        assert r.clasificadas == 2  # solo las publicadas
        assert s.get(Licitacion, "1234-56-LE26").clasificacion["rubros"] == ["aseo"]
        assert {c.empresa_id for c in s.query(Calce)} == {1}  # la de plan gratis no recibe calces


def test_ciclo_sigue_aunque_falte_el_ticket(Sesion):
    with Sesion() as s:
        r = ejecutar_ciclo(s, None, _ia(), hoy=date(2026, 10, 7))
        assert r.errores == ["sync: falta el ticket de Mercado Público"]
