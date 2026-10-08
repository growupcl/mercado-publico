"""Lectura de documentos Word (.docx) para el análisis de bases.

Un .docx es un .zip con el texto en word/document.xml. Se extraen párrafos y tablas en el orden del documento
(las tablas importan: ahí suelen ir requisitos, cantidades y criterios de evaluación) y se envían a Claude como
documento de texto. No requiere librerías externas.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAXIMO_XML = 50 * 1024 * 1024  # protege contra archivos comprimidos que se inflan demasiado
FIRMA_DOC_ANTIGUO = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .doc de Word 97-2003 (formato binario OLE)


class WordInvalido(ValueError):
    pass


def es_docx(contenido: bytes) -> bool:
    if not contenido.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def es_doc_antiguo(contenido: bytes) -> bool:
    return contenido.startswith(FIRMA_DOC_ANTIGUO)


def _texto_parrafo(p: ElementTree.Element) -> str:
    partes = []
    for nodo in p.iter():
        if nodo.tag == f"{W}t" and nodo.text:
            partes.append(nodo.text)
        elif nodo.tag == f"{W}tab":
            partes.append("\t")
        elif nodo.tag in (f"{W}br", f"{W}cr"):
            partes.append("\n")
    return "".join(partes).strip()


def _texto_tabla(tabla: ElementTree.Element) -> list[str]:
    filas = []
    for fila in tabla.findall(f"{W}tr"):
        celdas = []
        for celda in fila.findall(f"{W}tc"):
            celdas.append(" ".join(t for t in (_texto_parrafo(p) for p in celda.iter(f"{W}p")) if t))
        if any(celdas):
            filas.append("| " + " | ".join(c.replace("\n", " ") for c in celdas) + " |")
    return filas


def texto_de_docx(contenido: bytes) -> str:
    """Texto del documento, con las tablas como filas "| celda | celda |"."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAXIMO_XML:
                raise WordInvalido("El Word es demasiado grande para analizarlo.")
            xml = z.read(info)
    except (zipfile.BadZipFile, KeyError) as e:
        raise WordInvalido("El archivo no es un Word (.docx) válido.") from e
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise WordInvalido("No pude leer el contenido del Word.") from e
    cuerpo = raiz.find(f"{W}body")
    if cuerpo is None:
        raise WordInvalido("El Word no tiene contenido.")
    bloques: list[str] = []
    for nodo in cuerpo:
        if nodo.tag == f"{W}p":
            if texto := _texto_parrafo(nodo):
                bloques.append(texto)
        elif nodo.tag == f"{W}tbl":
            if filas := _texto_tabla(nodo):
                bloques.append("\n".join(filas))
        elif nodo.tag == f"{W}sdt":  # controles de contenido (índices, formularios): se leen sus párrafos
            bloques += [t for t in (_texto_parrafo(p) for p in nodo.iter(f"{W}p")) if t]
    return "\n\n".join(bloques)
