import json
import re
from datetime import date, datetime

import httpx
import pytest

from licita.db import Calce, Empresa
from licita.notificaciones import enviar_resumenes
from licita.plantillas_whatsapp import PLANTILLAS
from licita.sync import sincronizar_licitaciones
from licita.whatsapp import ClienteWhatsApp, ErrorWhatsApp


@pytest.mark.parametrize("plantilla", PLANTILLAS.values(), ids=lambda p: p.nombre)
def test_cumple_las_reglas_de_meta(plantilla):
    assert re.fullmatch(r"[a-z0-9_]{1,512}", plantilla.nombre)
    variables = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", plantilla.cuerpo)]
    assert variables == list(range(1, plantilla.variables + 1)), "variables correlativas, una vez cada una"
    assert not plantilla.cuerpo.startswith("{{") and not plantilla.cuerpo.rstrip().endswith("}}")
    assert len(plantilla.cuerpo) <= 1024
    palabras_fijas = len(re.sub(r"\{\{\d+\}\}", "", plantilla.cuerpo).split())
    assert palabras_fijas >= 3 * plantilla.variables, "suficiente texto alrededor de las variables"
    assert all(1 <= len(b) <= 20 for b in plantilla.botones) and len(plantilla.botones) <= 3
    assert all(e.strip() and "\n" not in e for e in plantilla.ejemplos)
    definicion = plantilla.definicion_meta()
    assert definicion["category"] == "UTILITY" and definicion["language"] == "es"


def test_enviar_plantilla_a_revision():
    capturado = {}

    def handler(request):
        capturado["url"], capturado["json"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"id": "123", "status": "PENDING", "category": "UTILITY"})

    wa = ClienteWhatsApp("T", "PHONE", http=httpx.Client(transport=httpx.MockTransport(handler)))
    r = wa.crear_plantilla("WABA9", PLANTILLAS["cobro_rechazado"].definicion_meta())
    assert r["status"] == "PENDING"
    assert capturado["url"] == "https://graph.facebook.com/v23.0/WABA9/message_templates"
    assert capturado["json"]["components"][1]["buttons"] == [{"type": "QUICK_REPLY", "text": "Ver mi cuenta"}]


def test_rechazo_de_meta_muestra_el_motivo():
    wa = ClienteWhatsApp("T", "P", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        400, json={"error": {"message": "Invalid parameter", "error_user_msg": "El cuerpo tiene demasiadas variables"}}))))
    with pytest.raises(ErrorWhatsApp, match="demasiadas variables"):
        wa.crear_plantilla("W", PLANTILLAS["resumen_diario_licitaciones"].definicion_meta())


def test_resumen_con_la_plantilla_de_respaldo(Sesion, cliente_mp):
    class WA:
        enviados = []

        def enviar_plantilla(self, tel, nombre, *, idioma="es", parametros=None, payloads_botones=None):
            self.enviados.append((nombre, parametros, payloads_botones))
            return "w"

    with Sesion() as s:
        sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
        e = Empresa(nombre="Aseo Sur", descripcion="", regiones=[], palabras_clave=[], whatsapp="569111")
        s.add(e)
        s.commit()
        s.add(Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=90, razon="Calza."))
        s.commit()
        wa = WA()
        enviar_resumenes(s, wa, plantilla="resumen_diario_simple", momento=datetime(2026, 10, 7, 8))
        assert wa.enviados == [("resumen_diario_simple", ["Aseo Sur", "1 licitación nueva"], ["VER_TODAS"])]
