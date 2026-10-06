"""Motor de calce: encuentra las licitaciones que le sirven a cada empresa.

Funciona en dos etapas para mantener bajo el costo de IA:
1. Prefiltro sin IA: estado, fecha de cierre, región, monto y coincidencia de palabras clave.
2. La IA evalúa solo las mejores candidatas y les asigna un puntaje de 0 a 100.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Calce, Empresa, Licitacion, ahora
from .ia import AsistenteIA

ESTADO_PUBLICADA = 5

STOPWORDS = {
    "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "para", "por", "que", "se",
    "su", "sus", "un", "una", "uno", "y", "o", "e", "u", "otros", "otras", "tipo", "segun",
    "servicio", "servicios", "adquisicion", "compra", "suministro", "contratacion",
}


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sin_tildes if not unicodedata.combining(c))


def tokens(texto: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", normalizar(texto)) if len(t) > 2 and t not in STOPWORDS}


def _raiz(token: str) -> str:
    # Raíz simple para que "guantes", "guante" y "papeles"/"papel" coincidan.
    if token.endswith("s") and len(token) > 4:
        token = token[:-1]
    if token[-1] in "aeo" and len(token) > 4:
        token = token[:-1]
    return token


def _texto_licitacion(lic: Licitacion) -> str:
    partes = [lic.nombre, lic.descripcion]
    for it in lic.items:
        partes += [it.get("categoria") or "", it.get("producto") or "", it.get("descripcion") or ""]
    if lic.clasificacion:
        partes += lic.clasificacion.get("rubros", []) + lic.clasificacion.get("palabras_clave", [])
    return " ".join(partes)


def region_compatible(empresa: Empresa, lic: Licitacion) -> bool:
    if not empresa.regiones or not lic.region:
        return True
    region = normalizar(lic.region)
    return any(normalizar(r) in region for r in empresa.regiones)


def monto_compatible(empresa: Empresa, lic: Licitacion) -> bool:
    if lic.monto_estimado is None:
        return True
    if empresa.monto_max is not None and lic.monto_estimado > empresa.monto_max:
        return False
    if empresa.monto_min is not None and lic.monto_estimado < empresa.monto_min:
        return False
    return True


def puntaje_prefiltro(empresa: Empresa, lic: Licitacion) -> float:
    """Fracción (0 a 1) de las palabras clave de la empresa que aparecen en la licitación."""
    if not region_compatible(empresa, lic) or not monto_compatible(empresa, lic):
        return 0.0
    claves = {_raiz(t) for kw in empresa.palabras_clave for t in tokens(kw)}
    if not claves:
        return 0.0
    texto = {_raiz(t) for t in tokens(_texto_licitacion(lic))}
    return len(claves & texto) / len(claves)


def candidatas(
    session: Session, empresa: Empresa, *, momento: datetime | None = None, limite: int = 20, minimo: float = 0.05
) -> list[tuple[Licitacion, float]]:
    momento = momento or ahora()
    ya_evaluadas = select(Calce.licitacion_codigo).where(Calce.empresa_id == empresa.id)
    consulta = select(Licitacion).where(
        Licitacion.estado_codigo == ESTADO_PUBLICADA,
        (Licitacion.fecha_cierre.is_(None)) | (Licitacion.fecha_cierre > momento),
        Licitacion.codigo.not_in(ya_evaluadas),
    )
    puntuadas = [(lic, puntaje_prefiltro(empresa, lic)) for lic in session.scalars(consulta)]
    puntuadas = [(lic, p) for lic, p in puntuadas if p >= minimo]
    puntuadas.sort(key=lambda par: par[1], reverse=True)
    return puntuadas[:limite]


def buscar_calces(
    session: Session, empresa: Empresa, ia: AsistenteIA, *, momento: datetime | None = None, limite: int = 20
) -> list[Calce]:
    """Evalúa con IA las mejores candidatas y guarda el resultado."""
    seleccion = candidatas(session, empresa, momento=momento, limite=limite)
    if not seleccion:
        return []
    prefiltro = {lic.codigo: p for lic, p in seleccion}
    evaluaciones = ia.evaluar_calce(empresa, [lic for lic, _ in seleccion])
    nuevos: list[Calce] = []
    for ev in evaluaciones:
        if ev.codigo not in prefiltro:
            continue  # código que no enviamos: se descarta
        calce = Calce(
            empresa_id=empresa.id,
            licitacion_codigo=ev.codigo,
            puntaje=max(0, min(100, ev.puntaje)),
            razon=ev.razon.strip(),
            puntaje_prefiltro=prefiltro.pop(ev.codigo),
        )
        session.add(calce)
        nuevos.append(calce)
    session.commit()
    return sorted(nuevos, key=lambda c: c.puntaje, reverse=True)
