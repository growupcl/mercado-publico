"""Análisis de bases de licitación (PDF) con Claude.

- Usa Claude Sonnet 5.5 (según el modelo de costos), con respaldo automático si el modelo rechaza.
- Cada PDF se identifica por su hash y se analiza UNA vez: el resultado se reutiliza para
  todos los usuarios que envíen el mismo archivo.
- El PDF va con caché de prompts, así las preguntas de seguimiento sobre las mismas bases
  cuestan cerca de un 10% de releer el documento.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anthropic
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import AnalisisBases, Documento, Empresa, Licitacion, SolicitudAnalisis, ahora
from .ia import ErrorIA

MODELO_ANALISIS = "claude-sonnet-5-5"
# Límite de la API para PDFs enviados en la solicitud.
TAMANO_MAXIMO = 32 * 1024 * 1024
# El mismo esfuerzo en el análisis y en las preguntas: cambiarlo invalida la caché del PDF.
ESFUERZO = "medium"
# Ej.: 1056854-11-LE26, 1511-62-L126, 1002772-108-LP26, 2345-12-O126 y Compra Ágil 1057539-228-COT26
PATRON_CODIGO = re.compile(r"\b\d{1,8}-\d{1,5}-(?:COT\d{2}|[A-Z][A-Z0-9]\d{2})\b", re.IGNORECASE)


class Requisito(BaseModel):
    requisito: str
    detalle: str


class Garantia(BaseModel):
    tipo: str
    monto: str
    vigencia: str


class Criterio(BaseModel):
    criterio: str
    ponderacion: str
    como_se_evalua: str


class Hito(BaseModel):
    hito: str
    fecha: str


class ResultadoAnalisis(BaseModel):
    resumen: str
    objeto: str
    presupuesto: str
    duracion_contrato: str
    requisitos_admisibilidad: list[Requisito]
    documentos_a_presentar: list[str]
    garantias: list[Garantia]
    criterios_evaluacion: list[Criterio]
    plazos: list[Hito]
    multas_y_riesgos: list[str]
    puntos_de_atencion: list[str]
    preguntas_para_el_foro: list[str]


SISTEMA_BASES = """Eres un analista experto en compras públicas de Chile (Ley 19.886 de Compras Públicas, su reglamento y las modificaciones de la Ley 21.634). Ayudas a dueños de pymes, que no son abogados, a entender bases de licitación de Mercado Público.
Reglas:
- Usa solo la información del documento. Si un dato no aparece, escribe "No se indica en las bases". No inventes montos, fechas ni porcentajes.
- Cuando sea útil, indica la página entre paréntesis, por ejemplo "(p. 12)".
- Escribe en español de Chile, claro y directo, sin jerga legal innecesaria."""

INSTRUCCIONES_ANALISIS = """Analiza estas bases de licitación para una pyme que evalúa si postular.
- resumen: 2 o 3 frases: qué se compra, quién compra y lo más importante para decidir.
- objeto: qué se contrata exactamente.
- presupuesto: monto disponible o estimado, con moneda e impuestos si se indican.
- duracion_contrato: plazo del contrato o de entrega.
- requisitos_admisibilidad: requisitos cuyo incumplimiento deja la oferta fuera (inscripción en registros, experiencia mínima, certificaciones, etc.).
- documentos_a_presentar: anexos y documentos que hay que subir, por nombre.
- garantias: garantía de seriedad de la oferta, de fiel cumplimiento u otras, con monto o porcentaje y vigencia.
- criterios_evaluacion: cada criterio con su ponderación y cómo se calcula el puntaje.
- plazos: preguntas, respuestas, cierre de ofertas, apertura, adjudicación, entrega u otros hitos con fecha.
- multas_y_riesgos: multas, causales de término anticipado y obligaciones costosas.
- puntos_de_atencion: detalles fáciles de pasar por alto que suelen dejar ofertas fuera o hacer perder puntos.
- preguntas_para_el_foro: 0 a 3 preguntas que convendría hacer en el foro de la licitación por ambigüedades reales del documento."""

INSTRUCCIONES_PREGUNTA = """Responde la pregunta del usuario sobre estas bases. La respuesta se envía por WhatsApp:
- Máximo 6 líneas, directo al punto, con la página entre paréntesis cuando corresponda.
- Si la respuesta no está en las bases, dilo y sugiere consultarlo en el foro de la licitación."""


class AnalizadorBases:
    def __init__(self, cliente: anthropic.Anthropic | None = None, *, modelo: str = MODELO_ANALISIS) -> None:
        self._cliente = cliente or anthropic.Anthropic()
        self.modelo = modelo

    def _contenido(self, pdf: bytes, texto: str) -> list[dict]:
        return [{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf).decode()},
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": texto},
            ],
        }]

    def _parametros_comunes(self) -> dict:
        return {
            "model": self.modelo,
            "system": SISTEMA_BASES,
            "output_config": {"effort": ESFUERZO},
            # Si el modelo rechaza por un falso positivo, la API reintenta con otro modelo.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }

    def analizar(self, pdf: bytes, contexto: str = "") -> tuple[ResultadoAnalisis, int, int]:
        texto = INSTRUCCIONES_ANALISIS + (f"\n\nDatos de la licitación según Mercado Público:\n{contexto}" if contexto else "")
        respuesta = self._cliente.beta.messages.parse(
            max_tokens=16000, messages=self._contenido(pdf, texto), output_format=ResultadoAnalisis,
            **self._parametros_comunes(),
        )
        _revisar(respuesta)
        if respuesta.parsed_output is None:
            raise ErrorIA("El modelo no devolvió el análisis en el formato esperado.")
        uso = respuesta.usage
        return respuesta.parsed_output, uso.input_tokens, uso.output_tokens

    def preguntar(self, pdf: bytes, pregunta: str) -> str:
        texto = f"{INSTRUCCIONES_PREGUNTA}\n\n<pregunta>\n{pregunta}\n</pregunta>"
        respuesta = self._cliente.beta.messages.create(
            max_tokens=2000, messages=self._contenido(pdf, texto), **self._parametros_comunes(),
        )
        _revisar(respuesta)
        texto_respuesta = "\n".join(b.text for b in respuesta.content if b.type == "text").strip()
        if not texto_respuesta:
            raise ErrorIA("El modelo no devolvió una respuesta.")
        return texto_respuesta


def _revisar(respuesta) -> None:
    if respuesta.stop_reason == "refusal":
        raise ErrorIA("El modelo no pudo analizar este documento.")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorIA("El análisis quedó incompleto (documento demasiado extenso).")


class AlmacenDocumentos:
    """Guarda los PDF en disco, nombrados por su hash."""

    def __init__(self, directorio: str | Path) -> None:
        self._dir = Path(directorio)

    def guardar(self, contenido: bytes) -> tuple[str, str]:
        sha = hashlib.sha256(contenido).hexdigest()
        self._dir.mkdir(parents=True, exist_ok=True)
        ruta = self._dir / f"{sha}.pdf"
        if not ruta.exists():
            ruta.write_bytes(contenido)
        return sha, str(ruta)

    def leer(self, ruta: str) -> bytes:
        return Path(ruta).read_bytes()


class LimiteAlcanzado(Exception):
    pass


class DocumentoInvalido(Exception):
    pass


@dataclass
class ServicioAnalisis:
    almacen: AlmacenDocumentos
    analizador: AnalizadorBases
    limite_mensual: int = 15

    def usados_en_el_mes(self, session: Session, empresa: Empresa, momento: datetime) -> int:
        inicio = momento.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return session.scalar(
            select(func.count()).select_from(SolicitudAnalisis)
            .where(SolicitudAnalisis.empresa_id == empresa.id, SolicitudAnalisis.creado_en >= inicio)
        ) or 0

    def buscar_existente(self, session: Session, contenido: bytes) -> AnalisisBases | None:
        sha = hashlib.sha256(contenido).hexdigest()
        return session.scalar(
            select(AnalisisBases).join(Documento, Documento.id == AnalisisBases.documento_id).where(Documento.sha256 == sha)
        )

    def analizar(
        self,
        session: Session,
        contenido: bytes,
        *,
        empresa: Empresa | None = None,
        nombre_archivo: str = "",
        codigo: str | None = None,
        momento: datetime | None = None,
    ) -> tuple[AnalisisBases, bool]:
        """Devuelve (análisis, reutilizado). Reutilizado = ya existía y no costó IA."""
        momento = momento or ahora()
        if not contenido.startswith(b"%PDF"):
            raise DocumentoInvalido("El archivo no es un PDF.")
        if len(contenido) > TAMANO_MAXIMO:
            raise DocumentoInvalido("El PDF supera los 32 MB que podemos analizar.")

        existente = self.buscar_existente(session, contenido)
        ya_solicitado = empresa is not None and existente is not None and session.scalar(
            select(SolicitudAnalisis).where(SolicitudAnalisis.empresa_id == empresa.id, SolicitudAnalisis.analisis_id == existente.id)
        ) is not None
        if empresa is not None and not ya_solicitado and self.usados_en_el_mes(session, empresa, momento) >= self.limite_mensual:
            raise LimiteAlcanzado(f"Ya usaste los {self.limite_mensual} análisis de bases de tu plan este mes.")

        analisis, reutilizado = existente, existente is not None
        if analisis is None:
            sha, ruta = self.almacen.guardar(contenido)
            documento = session.scalar(select(Documento).where(Documento.sha256 == sha))
            if documento is None:
                documento = Documento(sha256=sha, nombre_archivo=nombre_archivo, ruta=ruta, tamano=len(contenido), licitacion_codigo=codigo)
                session.add(documento)
                session.flush()
            lic = session.get(Licitacion, codigo) if codigo else None
            resultado, entrada, salida = self.analizador.analizar(contenido, _contexto_licitacion(lic))
            analisis = AnalisisBases(
                documento_id=documento.id, licitacion_codigo=codigo, modelo=self.analizador.modelo,
                resultado=resultado.model_dump(), tokens_entrada=entrada, tokens_salida=salida,
            )
            session.add(analisis)
            session.flush()
        if empresa is not None:
            if not ya_solicitado:
                session.add(SolicitudAnalisis(empresa_id=empresa.id, analisis_id=analisis.id, creado_en=momento))
            empresa.analisis_activo_id = analisis.id
            empresa.contexto_actualizado_en = momento
        session.commit()
        return analisis, reutilizado

    def preguntar(self, session: Session, analisis: AnalisisBases, pregunta: str) -> str:
        documento = session.get(Documento, analisis.documento_id)
        return self.analizador.preguntar(self.almacen.leer(documento.ruta), pregunta)


def detectar_codigo(*textos: str | None) -> str | None:
    for texto in textos:
        if texto and (m := PATRON_CODIGO.search(texto)):
            return m.group(0).upper()
    return None


def _contexto_licitacion(lic: Licitacion | None) -> str:
    if lic is None:
        return ""
    partes = [f"Código: {lic.codigo}", f"Nombre: {lic.nombre}", f"Organismo: {lic.organismo}"]
    if lic.monto_estimado:
        partes.append(f"Monto estimado: {lic.monto_estimado:.0f} {lic.moneda}")
    if lic.fecha_cierre:
        partes.append(f"Cierre de ofertas: {lic.fecha_cierre:%d-%m-%Y %H:%M}")
    return "\n".join(partes)


def textos_analisis(resultado: dict, *, titulo: str = "") -> list[str]:
    """Formatea el análisis para WhatsApp, dividido en mensajes de menos de 4.000 caracteres."""
    r = ResultadoAnalisis.model_validate(resultado)
    secciones = [f"📄 *Análisis de bases{': ' + titulo if titulo else ''}*\n\n{r.resumen}"]
    secciones.append(f"🎯 *Objeto:* {r.objeto}\n💰 *Presupuesto:* {r.presupuesto}\n🗓️ *Duración:* {r.duracion_contrato}")
    if r.requisitos_admisibilidad:
        secciones.append("🚫 *Requisitos que te pueden dejar fuera:*\n" + "\n".join(f"• {x.requisito}: {x.detalle}" for x in r.requisitos_admisibilidad))
    if r.documentos_a_presentar:
        secciones.append("📎 *Documentos a presentar:*\n" + "\n".join(f"☐ {d}" for d in r.documentos_a_presentar))
    if r.garantias:
        secciones.append("🔒 *Garantías:*\n" + "\n".join(f"• {g.tipo}: {g.monto} ({g.vigencia})" for g in r.garantias))
    if r.criterios_evaluacion:
        secciones.append("📊 *Cómo se evalúa:*\n" + "\n".join(f"• {c.criterio} ({c.ponderacion}): {c.como_se_evalua}" for c in r.criterios_evaluacion))
    if r.plazos:
        secciones.append("⏰ *Plazos:*\n" + "\n".join(f"• {h.hito}: {h.fecha}" for h in r.plazos))
    if r.multas_y_riesgos:
        secciones.append("⚠️ *Multas y riesgos:*\n" + "\n".join(f"• {m}" for m in r.multas_y_riesgos))
    if r.puntos_de_atencion:
        secciones.append("👀 *Ojo con esto:*\n" + "\n".join(f"• {p}" for p in r.puntos_de_atencion))
    if r.preguntas_para_el_foro:
        secciones.append("❓ *Preguntas sugeridas para el foro:*\n" + "\n".join(f"• {q}" for q in r.preguntas_para_el_foro))
    secciones.append("Puedes hacerme preguntas sobre estas bases durante las próximas 24 horas. Por ejemplo: _¿piden boleta de garantía?_")

    mensajes, actual = [], ""
    for seccion in secciones:
        if len(seccion) > 3900:
            seccion = seccion[:3890] + "…"
        if actual and len(actual) + len(seccion) + 2 > 3900:
            mensajes.append(actual)
            actual = seccion
        else:
            actual = f"{actual}\n\n{seccion}" if actual else seccion
    if actual:
        mensajes.append(actual)
    return mensajes
