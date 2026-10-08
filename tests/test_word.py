import io
import json
import zipfile
from datetime import datetime

import anthropic
import httpx2
import pytest

from licita.analisis import AnalizadorBases, DocumentoInvalido, validar_documento
from licita.conversacion import procesar_webhook, tipo_de_archivo
from licita.word import es_docx, texto_de_docx
from test_analisis import RESULTADO, WhatsAppConArchivos, _pdf, base, servicio  # noqa: F401 (fixtures)

MOMENTO = datetime(2026, 10, 7, 9, 0)
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(texto: str) -> str:
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{texto}</w:t></w:r></w:p>"


def docx(cuerpo: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {NS}><w:body>{cuerpo}</w:body></w:document>')
    return buf.getvalue()


RELLENO = _p("Las presentes bases regulan el proceso de compra de insumos de aseo para el CESFAM, " * 4)
BASES = docx(
    _p("BASES ADMINISTRATIVAS")
    + "<w:p><w:r><w:t>Plazo:</w:t></w:r><w:r><w:tab/><w:t>12 meses</w:t></w:r></w:p>"
    + "<w:tbl>"
    + "<w:tr><w:tc>" + _p("Criterio") + "</w:tc><w:tc>" + _p("Ponderación") + "</w:tc></w:tr>"
    + "<w:tr><w:tc>" + _p("Precio") + "</w:tc><w:tc>" + _p("60%") + "</w:tc></w:tr>"
    + "</w:tbl>"
    + _p("Garantía de fiel cumplimiento: 5% del contrato.")
    + RELLENO
)


def test_extrae_parrafos_y_tablas_en_orden():
    texto = texto_de_docx(BASES)
    assert texto.startswith("BASES ADMINISTRATIVAS\n\nPlazo:\t12 meses\n\n| Criterio | Ponderación |\n| Precio | 60% |")
    assert texto.index("| Precio | 60% |") < texto.index("Garantía de fiel cumplimiento")


def test_reconoce_y_valida_formatos():
    assert es_docx(BASES) and not es_docx(b"%PDF-1.7") and not es_docx(b"PK\x03\x04roto")
    validar_documento(BASES)
    validar_documento(b"%PDF-1.7 bases")
    with pytest.raises(DocumentoInvalido, match="Word antiguo"):
        validar_documento(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    with pytest.raises(DocumentoInvalido, match="casi no tiene texto"):
        validar_documento(docx(_p("Anexo N°1")))
    with pytest.raises(DocumentoInvalido, match="PDF o Word"):
        validar_documento(b"hola")


def test_tipo_de_archivo_por_mime_o_extension():
    assert tipo_de_archivo("application/pdf", "") == "pdf"
    assert tipo_de_archivo("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "x") == "docx"
    assert tipo_de_archivo("application/octet-stream", "Bases Técnicas.DOCX") == "docx"
    assert tipo_de_archivo("application/msword", "") == "doc"
    assert tipo_de_archivo("image/jpeg", "foto.jpg") is None


def test_word_va_a_claude_como_documento_de_texto():
    capturado = {}

    def handler(request):
        capturado["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "text", "text": RESULTADO.model_dump_json()}],
        })

    cliente = anthropic.Anthropic(api_key="x", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    AnalizadorBases(cliente).analizar(BASES)
    documento, _ = capturado["body"]["messages"][0]["content"]
    assert documento["source"]["type"] == "text" and documento["source"]["media_type"] == "text/plain"
    assert "| Precio | 60% |" in documento["source"]["data"]
    assert documento["cache_control"] == {"type": "ephemeral"}


def test_word_por_whatsapp_se_analiza_y_se_guarda_como_docx(base, servicio, tmp_path):  # noqa: F811
    s, e = base
    srv, claude = servicio
    wa = WhatsAppConArchivos(archivo=BASES)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    procesar_webhook(s, wa, _pdf("Bases 1234-56-LE26.docx", mime=mime), analisis=srv, momento=MOMENTO)
    assert wa.enviados[0].startswith("Recibí las bases") and wa.enviados[1].startswith("📄 *Análisis de bases")
    llamada = next(kw for tipo, kw in claude.llamadas if tipo == "parse")
    assert llamada["messages"][0]["content"][0]["source"]["type"] == "text"
    assert list(tmp_path.glob("*.docx"))

    # Las preguntas posteriores también leen el Word.
    wa.enviados.clear()
    procesar_webhook(s, wa, {"entry": [{"changes": [{"value": {"messages": [
        {"from": e.whatsapp, "id": "wamid.word-q", "type": "text", "text": {"body": "¿Cuánto pondera el precio?"}}]}}]}]},
        analisis=srv, momento=MOMENTO)
    assert claude.llamadas[-1][0] == "create"
    assert claude.llamadas[-1][1]["messages"][0]["content"][0]["source"]["type"] == "text"
