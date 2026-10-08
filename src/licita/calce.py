"""Motor de calce: encuentra las licitaciones que le sirven a cada empresa.

Funciona en dos etapas para mantener bajo el costo de IA:
1. Prefiltro sin IA: estado, fecha de cierre, región, monto y coincidencia de palabras clave.
2. La IA evalúa solo las mejores candidatas y les asigna un puntaje de 0 a 100.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Calce, Empresa, Licitacion, hora_chile
from .ia import AsistenteIA
from .planes import PLANES_CON_COMPRA_AGIL

ESTADO_PUBLICADA = 5
TIPO_COMPRA_AGIL = "COT"

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


def _compacto(texto: str) -> str:
    """Solo letras y números: 'Región del Libertador ... O´Higgins' contiene 'ohiggins'."""
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def region_compatible(empresa: Empresa, lic: Licitacion) -> bool:
    if not empresa.regiones or not lic.region:
        return True
    region = _compacto(lic.region)
    return any(_compacto(r) in region for r in empresa.regiones)


def interes_de_clientes(session: Session, *, planes: tuple[str, ...] | None = None) -> Callable[[str], bool]:
    """True si el texto comparte alguna palabra clave con un cliente activo (de esos planes, si se indican).

    Filtra las órdenes de compra que vale la pena guardar y las Compras Ágiles cuyo detalle vale la pena pedir.
    """
    consulta = select(Empresa).where(Empresa.plan.in_(planes)) if planes else select(Empresa).where(Empresa.plan != "gratis")
    claves = {
        _raiz(t)
        for e in session.scalars(consulta)
        for kw in e.palabras_clave for t in tokens(kw)
    }
    return lambda texto: bool(claves & {_raiz(t) for t in tokens(texto)})


def monto_compatible(empresa: Empresa, lic: Licitacion) -> bool:
    if lic.monto_estimado is None:
        return True
    if empresa.monto_max is not None and lic.monto_estimado > empresa.monto_max:
        return False
    if empresa.monto_min is not None and lic.monto_estimado < empresa.monto_min:
        return False
    return True


def puntaje_prefiltro(empresa: Empresa, lic: Licitacion) -> float:
    """Puntaje de 0 a 1 según cuántas palabras clave de la empresa aparecen completas en la licitación.

    Una palabra clave de varias palabras ("soporte técnico") solo cuenta si aparecen todas, así
    términos genéricos sueltos ("técnico", "equipo") no inflan el puntaje. Las coincidencias
    parciales suman poco y solo sirven para desempatar.
    """
    if not region_compatible(empresa, lic) or not monto_compatible(empresa, lic):
        return 0.0
    frases = [r for r in ({_raiz(t) for t in tokens(kw)} for kw in empresa.palabras_clave) if r]
    if not frases:
        return 0.0
    texto = {_raiz(t) for t in tokens(_texto_licitacion(lic))}
    completas = sum(1 for f in frases if f <= texto)
    parciales = sum(len(f & texto) / len(f) for f in frases if not f <= texto)
    return (completas + 0.25 * parciales) / len(frases)


def _evaluable(empresa: Empresa, lic: Licitacion) -> bool:
    """Las Compras Ágiles son del plan Pro y se evalúan solo cuando ya tienen el detalle (productos)."""
    if lic.tipo != TIPO_COMPRA_AGIL:
        return True
    return empresa.plan in PLANES_CON_COMPRA_AGIL and bool((lic.raw or {}).get("detalle"))


def candidatas(
    session: Session,
    empresa: Empresa,
    *,
    momento: datetime | None = None,
    limite: int = 20,
    minimo: float = 0.05,
    solo_compra_agil: bool = False,
) -> list[tuple[Licitacion, float]]:
    # Mercado Público informa los cierres en hora de Chile.
    cierre_minimo = hora_chile(momento)
    ya_evaluadas = select(Calce.licitacion_codigo).where(Calce.empresa_id == empresa.id)
    consulta = select(Licitacion).where(
        Licitacion.estado_codigo == ESTADO_PUBLICADA,
        (Licitacion.fecha_cierre.is_(None)) | (Licitacion.fecha_cierre > cierre_minimo),
        Licitacion.codigo.not_in(ya_evaluadas),
    )
    if solo_compra_agil:
        consulta = consulta.where(Licitacion.tipo == TIPO_COMPRA_AGIL)
    puntuadas = [(lic, puntaje_prefiltro(empresa, lic)) for lic in session.scalars(consulta) if _evaluable(empresa, lic)]
    puntuadas = [(lic, p) for lic, p in puntuadas if p >= minimo]
    puntuadas.sort(key=lambda par: par[1], reverse=True)
    return puntuadas[:limite]


def buscar_calces(
    session: Session,
    empresa: Empresa,
    ia: AsistenteIA,
    *,
    momento: datetime | None = None,
    limite: int = 20,
    solo_compra_agil: bool = False,
) -> list[Calce]:
    """Evalúa con IA las mejores candidatas y guarda el resultado."""
    seleccion = candidatas(session, empresa, momento=momento, limite=limite, solo_compra_agil=solo_compra_agil)
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
