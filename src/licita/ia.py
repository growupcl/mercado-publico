"""Funciones de IA con la API de Claude.

Según el modelo de costos, la clasificación y el filtro de calce usan Haiku
(rápido y económico). Cada licitación se clasifica una sola vez y el resultado
se comparte entre todos los usuarios.
"""

from __future__ import annotations

import json
from typing import Literal, Sequence

import anthropic
from pydantic import BaseModel

from .db import Empresa, Licitacion


class ErrorIA(Exception):
    pass


class ClasificacionLicitacion(BaseModel):
    resumen: str
    tipo_compra: Literal["bienes", "servicios", "obras", "mixto"]
    rubros: list[str]
    palabras_clave: list[str]
    requisitos_destacados: list[str]


class PerfilExtraido(BaseModel):
    rubros: list[str]
    palabras_clave: list[str]


class EvaluacionCalce(BaseModel):
    codigo: str
    puntaje: int
    razon: str


class EvaluacionesCalce(BaseModel):
    evaluaciones: list[EvaluacionCalce]


SISTEMA_CLASIFICACION = """Eres un analista experto en compras públicas de Chile (Mercado Público / ChileCompra).
Recibes los datos de una licitación y la clasificas para que pymes proveedoras encuentren oportunidades.
- resumen: una o dos frases en español simple, qué se compra y para quién.
- tipo_compra: bienes, servicios, obras o mixto.
- rubros: 1 a 4 rubros de negocio en español (ej.: "artículos de aseo", "mantención de áreas verdes").
- palabras_clave: 5 a 15 términos que un proveedor usaría para describir lo que vende, en minúsculas, incluyendo sinónimos comunes.
- requisitos_destacados: requisitos o restricciones visibles en los datos (garantías, visita a terreno, plazos de entrega). Lista vacía si no hay."""

SISTEMA_PERFIL = """Eres un asistente que ayuda a pymes chilenas a encontrar licitaciones en Mercado Público.
A partir de la descripción del negocio, extrae:
- rubros: 1 a 5 rubros de negocio en español.
- palabras_clave: 10 a 25 términos en minúsculas que aparecerían en licitaciones relevantes (productos, servicios, sinónimos y términos técnicos usados por compradores públicos)."""

SISTEMA_CALCE = """Eres un asesor experto en compras públicas de Chile. Evalúas qué tan conveniente es para una empresa postular a cada licitación.
Para cada licitación entrega:
- codigo: el código exacto recibido.
- puntaje: entero de 0 a 100. 90-100 = calce evidente con lo que vende; 60-89 = probable, vale la pena revisar; 30-59 = parcial o dudoso; 0-29 = no corresponde.
- razon: una frase en español, concreta, que explique el puntaje al dueño de la pyme.
Considera rubro, productos o servicios pedidos, región y monto respecto del perfil. No inventes datos que no estén en la información recibida."""


class AsistenteIA:
    def __init__(self, cliente: anthropic.Anthropic | None = None, *, modelo: str = "claude-haiku-4-5") -> None:
        self._cliente = cliente or anthropic.Anthropic()
        self._modelo = modelo

    def _parse(self, sistema: str, contenido: str, formato: type[BaseModel], max_tokens: int):
        respuesta = self._cliente.messages.parse(
            model=self._modelo,
            max_tokens=max_tokens,
            system=sistema,
            messages=[{"role": "user", "content": contenido}],
            output_format=formato,
        )
        if respuesta.stop_reason == "refusal":
            raise ErrorIA("El modelo rechazó la solicitud.")
        if respuesta.stop_reason == "max_tokens":
            raise ErrorIA("La respuesta del modelo quedó truncada (max_tokens).")
        if respuesta.parsed_output is None:
            raise ErrorIA("El modelo no devolvió una respuesta estructurada.")
        return respuesta.parsed_output

    def clasificar_licitacion(self, licitacion: Licitacion) -> ClasificacionLicitacion:
        return self._parse(SISTEMA_CLASIFICACION, _describir_licitacion(licitacion, detalle=True), ClasificacionLicitacion, 2000)

    def extraer_perfil(self, descripcion_negocio: str) -> PerfilExtraido:
        return self._parse(SISTEMA_PERFIL, f"<negocio>\n{descripcion_negocio}\n</negocio>", PerfilExtraido, 2000)

    def evaluar_calce(self, empresa: Empresa, licitaciones: Sequence[Licitacion]) -> list[EvaluacionCalce]:
        perfil = {
            "nombre": empresa.nombre,
            "descripcion": empresa.descripcion,
            "regiones": empresa.regiones or "todas",
            "monto_min": empresa.monto_min,
            "monto_max": empresa.monto_max,
        }
        bloques = "\n\n".join(_describir_licitacion(lic, detalle=False) for lic in licitaciones)
        contenido = (
            f"<perfil_empresa>\n{json.dumps(perfil, ensure_ascii=False)}\n</perfil_empresa>\n\n"
            f"<licitaciones>\n{bloques}\n</licitaciones>"
        )
        resultado = self._parse(SISTEMA_CALCE, contenido, EvaluacionesCalce, 4000)
        return resultado.evaluaciones


def _describir_licitacion(lic: Licitacion, *, detalle: bool) -> str:
    items = lic.items[:15] if detalle else lic.items[:5]
    datos = {
        "codigo": lic.codigo,
        "nombre": lic.nombre,
        "descripcion": lic.descripcion[:2000] if detalle else lic.descripcion[:500],
        "organismo": lic.organismo,
        "region": lic.region,
        "monto_estimado": lic.monto_estimado,
        "moneda": lic.moneda,
        "tipo": lic.tipo,
        "fecha_cierre": lic.fecha_cierre.isoformat() if lic.fecha_cierre else None,
        "items": [
            {k: it.get(k) for k in ("categoria", "producto", "descripcion", "cantidad", "unidad")} for it in items
        ],
    }
    if not detalle and lic.clasificacion:
        datos["resumen"] = lic.clasificacion.get("resumen")
        datos["rubros"] = lic.clasificacion.get("rubros")
    return f"<licitacion>\n{json.dumps(datos, ensure_ascii=False)}\n</licitacion>"


def clasificar_pendientes(session, ia: AsistenteIA, *, limite: int = 100) -> tuple[int, int]:
    """Clasifica las licitaciones publicadas que aún no tienen clasificación. Devuelve (ok, errores)."""
    from sqlalchemy import select

    from .db import ahora

    pendientes = session.scalars(
        select(Licitacion)
        # Las Compras Ágiles no se clasifican: son muchas, cortas y su calce usa el detalle directamente.
        .where(Licitacion.clasificacion.is_(None), Licitacion.estado_codigo == 5,
               (Licitacion.tipo.is_(None)) | (Licitacion.tipo != "COT"))
        .order_by(Licitacion.fecha_publicacion.desc())
        .limit(limite)
    ).all()
    ok = errores = 0
    for lic in pendientes:
        try:
            lic.clasificacion = ia.clasificar_licitacion(lic).model_dump()
            lic.clasificada_en = ahora()
            session.commit()
            ok += 1
        except (ErrorIA, anthropic.APIError):
            session.rollback()
            errores += 1
    return ok, errores
