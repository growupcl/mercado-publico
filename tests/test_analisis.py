import itertools
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from licita.analisis import (
    AlmacenDocumentos, AnalizadorBases, DocumentoInvalido, LimiteAlcanzado, ResultadoAnalisis, ServicioAnalisis,
    detectar_codigo, textos_analisis,
)
from licita.conversacion import procesar_webhook
from licita.db import AnalisisBases, Calce, Empresa, SolicitudAnalisis
from licita.notificaciones import enviar_resumenes
from licita.sync import sincronizar_licitaciones

MOMENTO = datetime(2026, 10, 7, 9, 0)
TEL = "56989299524"
PDF = b"%PDF-1.7\n bases de prueba \n%%EOF"
PDF_2 = b"%PDF-1.7\n otras bases \n%%EOF"

RESULTADO = ResultadoAnalisis(
    resumen="El CESFAM compra insumos de aseo por 12 meses.",
    objeto="Suministro de detergente, guantes y bolsas de basura.",
    presupuesto="$8.500.000 IVA incluido (p. 3)",
    duracion_contrato="12 meses (p. 4)",
    requisitos_admisibilidad=[{"requisito": "Inscripción en Registro de Proveedores", "detalle": "Hábil al momento de ofertar (p. 5)"}],
    documentos_a_presentar=["Anexo 1: Declaración jurada", "Anexo 3: Oferta económica"],
    garantias=[{"tipo": "Fiel cumplimiento", "monto": "5% del contrato", "vigencia": "contrato + 60 días"}],
    criterios_evaluacion=[{"criterio": "Precio", "ponderacion": "60%", "como_se_evalua": "Menor precio / precio ofertado × 100"}],
    plazos=[{"hito": "Cierre de ofertas", "fecha": "20-10-2026 15:00"}],
    multas_y_riesgos=["Multa de 1% diario por atraso en la entrega"],
    puntos_de_atencion=["Hay que ofertar todos los ítems"],
    preguntas_para_el_foro=["¿Se aceptan productos equivalentes a la marca indicada?"],
)


class ClaudeFalso:
    """Imita client.beta.messages.parse/create."""

    def __init__(self, stop_reason="end_turn"):
        self.llamadas = []
        self.stop_reason = stop_reason
        self.beta = self
        self.messages = self

    def parse(self, **kw):
        self.llamadas.append(("parse", kw))
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=RESULTADO,
                               usage=SimpleNamespace(input_tokens=60000, output_tokens=2500))

    def create(self, **kw):
        self.llamadas.append(("create", kw))
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text="Sí: garantía de fiel cumplimiento del 5% (p. 9).")])


@pytest.fixture
def servicio(tmp_path):
    claude = ClaudeFalso()
    return ServicioAnalisis(AlmacenDocumentos(tmp_path), AnalizadorBases(claude), limite_mensual=2), claude


@pytest.fixture
def base(Sesion, cliente_mp):
    s = Sesion()
    sincronizar_licitaciones(s, cliente_mp, date(2026, 10, 6))
    e = Empresa(nombre="Aseo Sur", descripcion="Insumos de aseo", regiones=[], palabras_clave=["aseo"], whatsapp=TEL)
    s.add(e)
    s.commit()
    return s, e


# --- Llamada real al SDK (con red simulada) ---

def test_solicitud_a_claude_lleva_pdf_en_cache_esfuerzo_y_respaldo():
    capturado = {}

    def handler(request):
        capturado["body"] = json.loads(request.content)
        capturado["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "text", "text": RESULTADO.model_dump_json()}],
        })

    cliente = anthropic.Anthropic(api_key="x", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    resultado, entrada, salida = AnalizadorBases(cliente).analizar(PDF, "Código: 1234-56-LE26")
    assert resultado.garantias[0].monto == "5% del contrato"
    assert (entrada, salida) == (100, 50)
    cuerpo = capturado["body"]
    assert cuerpo["model"] == "claude-sonnet-5-5"
    assert cuerpo["output_config"]["effort"] == "medium" and "format" in cuerpo["output_config"]
    assert cuerpo["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in capturado["beta"]
    documento, texto = cuerpo["messages"][0]["content"]
    assert documento["type"] == "document" and documento["source"]["media_type"] == "application/pdf"
    assert documento["cache_control"] == {"type": "ephemeral"}
    assert "Código: 1234-56-LE26" in texto["text"]


# --- Servicio: reutilización, límites y validaciones ---

def test_mismo_pdf_se_analiza_una_sola_vez(base, servicio):
    s, e = base
    srv, claude = servicio
    otra = Empresa(nombre="Limpia Ya", descripcion="", regiones=[], palabras_clave=[], whatsapp="56900000001")
    s.add(otra)
    s.commit()
    a1, reutilizado1 = srv.analizar(s, PDF, empresa=e, codigo="1234-56-LE26", momento=MOMENTO)
    a2, reutilizado2 = srv.analizar(s, PDF, empresa=otra, momento=MOMENTO)
    assert (reutilizado1, reutilizado2) == (False, True)
    assert a1.id == a2.id
    assert len([c for c in claude.llamadas if c[0] == "parse"]) == 1
    assert "Nombre: Adquisición de insumos de aseo para CESFAM" in claude.llamadas[0][1]["messages"][0]["content"][1]["text"]


def test_limite_mensual_por_empresa(base, servicio):
    s, e = base
    srv, _ = servicio
    srv.analizar(s, PDF, empresa=e, momento=MOMENTO)
    srv.analizar(s, PDF_2, empresa=e, momento=MOMENTO)
    # Pedir de nuevo uno ya entregado no descuenta.
    srv.analizar(s, PDF, empresa=e, momento=MOMENTO)
    with pytest.raises(LimiteAlcanzado, match="2 análisis"):
        srv.analizar(s, b"%PDF-1.7 tercero", empresa=e, momento=MOMENTO)
    # El mes siguiente se reinicia.
    srv.analizar(s, b"%PDF-1.7 tercero", empresa=e, momento=datetime(2026, 11, 2))
    assert s.query(SolicitudAnalisis).count() == 3


def test_rechaza_archivos_que_no_son_pdf(base, servicio):
    s, e = base
    srv, _ = servicio
    with pytest.raises(DocumentoInvalido):
        srv.analizar(s, b"no soy un pdf", empresa=e)


def test_detectar_codigo():
    assert detectar_codigo("Bases licitación 1234-56-le26.pdf") == "1234-56-LE26"
    assert detectar_codigo(None, "bases_finales.pdf") is None


def test_textos_analisis_formato_y_division():
    textos = textos_analisis(RESULTADO.model_dump(), titulo="Insumos de aseo")
    junto = "\n".join(textos)
    assert junto.startswith("📄 *Análisis de bases: Insumos de aseo*")
    assert "☐ Anexo 1: Declaración jurada" in junto
    assert "• Precio (60%): Menor precio / precio ofertado × 100" in junto
    assert all(len(t) <= 4000 for t in textos)
    largo = RESULTADO.model_copy(update={"puntos_de_atencion": ["x" * 300] * 30}).model_dump()
    partes = textos_analisis(largo)
    assert len(partes) > 1 and all(len(t) <= 4000 for t in partes)


# --- WhatsApp: el usuario envía el PDF y luego pregunta ---

class WhatsAppConArchivos:
    def __init__(self, archivo=PDF):
        self.enviados = []
        self.archivo = archivo

    def enviar_texto(self, telefono, texto):
        self.enviados.append(texto)
        return f"wamid.out{len(self.enviados)}"

    def enviar_plantilla(self, *a, **k):
        self.enviados.append("<plantilla>")
        return "wamid.p"

    def descargar_media(self, media_id, **k):
        assert media_id == "MEDIA1"
        return self.archivo, "application/pdf"


_ids = itertools.count(1)


def _webhook(msg):
    msg = {"from": TEL, "id": f"wamid.in{next(_ids)}", **msg}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def _pdf(nombre="bases.pdf", mime="application/pdf"):
    return _webhook({"type": "document", "document": {"id": "MEDIA1", "mime_type": mime, "filename": nombre}})


def test_flujo_completo_detalle_pdf_y_pregunta(base, servicio):
    s, e = base
    srv, claude = servicio
    s.add(Calce(empresa_id=e.id, licitacion_codigo="1234-56-LE26", puntaje=92, razon="Es tu rubro."))
    s.commit()
    enviar_resumenes(s, WhatsAppConArchivos(), momento=MOMENTO)
    wa = WhatsAppConArchivos()

    procesar_webhook(s, wa, _webhook({"type": "text", "text": {"body": "1"}}), analisis=srv, momento=MOMENTO)
    assert "envíamelo aquí" in wa.enviados[-1]
    assert e.licitacion_activa == "1234-56-LE26"

    wa.enviados.clear()
    procesar_webhook(s, wa, _pdf(), analisis=srv, momento=MOMENTO + timedelta(minutes=2))
    assert wa.enviados[0].startswith("Recibí las bases")
    assert wa.enviados[1].startswith("📄 *Análisis de bases: Adquisición de insumos de aseo para CESFAM*")
    assert s.get(AnalisisBases, e.analisis_activo_id).licitacion_codigo == "1234-56-LE26"

    wa.enviados.clear()
    procesar_webhook(s, wa, _webhook({"type": "text", "text": {"body": "¿Piden boleta de garantía?"}}),
                     analisis=srv, momento=MOMENTO + timedelta(minutes=5))
    assert wa.enviados == ["Sí: garantía de fiel cumplimiento del 5% (p. 9)."]
    pregunta = claude.llamadas[-1][1]["messages"][0]["content"][1]["text"]
    assert "¿Piden boleta de garantía?" in pregunta

    # Pasadas 24 h, el texto libre ya no se interpreta como pregunta sobre esas bases.
    wa.enviados.clear()
    procesar_webhook(s, wa, _webhook({"type": "text", "text": {"body": "¿y el plazo?"}}),
                     analisis=srv, momento=MOMENTO + timedelta(days=2))
    assert "Soy el asistente" in wa.enviados[0]


def test_pdf_repetido_no_avisa_espera_ni_gasta_ia(base, servicio):
    s, e = base
    srv, claude = servicio
    srv.analizar(s, PDF, momento=MOMENTO)
    wa = WhatsAppConArchivos()
    procesar_webhook(s, wa, _pdf(), analisis=srv, momento=MOMENTO)
    assert wa.enviados[0].startswith("📄 *Análisis de bases")
    assert len([c for c in claude.llamadas if c[0] == "parse"]) == 1


def test_archivo_que_no_es_pdf(base, servicio):
    s, _ = base
    srv, _ = servicio
    wa = WhatsAppConArchivos()
    procesar_webhook(s, wa, _pdf("bases.docx", mime="application/msword"), analisis=srv, momento=MOMENTO)
    assert "Solo puedo analizar bases en *PDF*" in wa.enviados[0]


def test_limite_alcanzado_por_whatsapp(base, tmp_path):
    s, e = base
    srv = ServicioAnalisis(AlmacenDocumentos(tmp_path), AnalizadorBases(ClaudeFalso()), limite_mensual=0)
    wa = WhatsAppConArchivos()
    procesar_webhook(s, wa, _pdf(), analisis=srv, momento=MOMENTO)
    assert "análisis de bases de tu plan" in wa.enviados[-1]


def test_rechazo_del_modelo_avisa_al_usuario(base, tmp_path):
    s, _ = base
    srv = ServicioAnalisis(AlmacenDocumentos(tmp_path), AnalizadorBases(ClaudeFalso(stop_reason="refusal")))
    wa = WhatsAppConArchivos()
    procesar_webhook(s, wa, _pdf(), analisis=srv, momento=MOMENTO)
    assert wa.enviados[-1].startswith("No pude analizar las bases en este momento")
    assert s.query(AnalisisBases).count() == 0
