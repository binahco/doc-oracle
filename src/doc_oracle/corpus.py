from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from parser_io import ParseError, Section, iter_documents, parse

# Los documentos vivos: se auditan porque hablan del presente. El CHANGELOG queda
# fuera a propósito — una entrada fechada es una foto del pasado, no una afirmación
# sobre el presente — y entra como oráculo (semana actual, semana de siembra).
CABECERAS = ("README.md", "ARCHITECTURE.md")


@dataclass
class Bloque:
    """Un párrafo con su salto de línea suave conservado.

    Los README se cortan a mano a unas 95 columnas: "Es el" puede quedar en una línea y
    "**segundo consumidor de …**" en la siguiente. Una frase partida en dos líneas sigue
    siendo una frase: los patrones cruzan ese salto con `\\s+`, mientras `.*?` y `^`
    siguen operando por línea. Dentro de una valla de código cada línea es su propio bloque.
    """

    lineas: list[int]
    textos: list[str]
    valla: bool
    texto: str = field(init=False)
    inicios: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.texto = "\n".join(self.textos)
        self.inicios = []
        posicion = 0
        for texto in self.textos:
            self.inicios.append(posicion)
            posicion += len(texto) + 1

    def ubicar(self, offset: int) -> tuple[int, int]:
        """La línea (1-based) que contiene el offset y dónde arranca dentro de `texto`."""
        i = max(i for i, inicio in enumerate(self.inicios) if inicio <= offset)
        return self.lineas[i], self.inicios[i]


@dataclass
class Documento:
    path: Path
    rel: str
    lines: list[str]
    sections: list[Section]
    bloques: list[Bloque]

    def seccion(self, linea: int) -> str:
        """Título de la sección más interna que contiene la línea (parser-io)."""
        candidatas = [s for s in self.sections if s.line_start <= linea <= s.line_end]
        return max(candidatas, key=lambda s: s.line_start).title if candidatas else ""

    def modulo_del_titulo(self) -> str | None:
        r"""El módulo entre acentos del H1, cuando lo hay (`# Contrato mínimo — `diff-engine``)."""
        h1 = next((ln for ln in self.lines if ln.startswith("# ")), None)
        match = re.search(r"`([\w-]+)`", h1) if h1 else None
        return match.group(1) if match else None


def _bloques(lines: list[str]) -> list[Bloque]:
    """Párrafos fuera de las vallas; dentro, una línea por bloque."""
    out: list[Bloque] = []
    parrafo: list[tuple[int, str]] = []
    abierto = False

    def cerrar() -> None:
        if parrafo:
            out.append(Bloque([n for n, _ in parrafo], [t for _, t in parrafo], valla=False))
            parrafo.clear()

    for numero, ln in enumerate(lines, start=1):
        if ln.lstrip().startswith(("```", "~~~")):
            cerrar()
            abierto = not abierto
        elif abierto:
            out.append(Bloque([numero], [ln], valla=True))
        elif not ln.strip():
            cerrar()
        else:
            parrafo.append((numero, ln))
    cerrar()
    return out


def living_docs(repo: Path) -> list[Documento]:
    """Los documentos vivos del repo, en orden determinista."""
    files = [repo / nombre for nombre in CABECERAS if (repo / nombre).is_file()]
    if (repo / "docs").is_dir():
        files += iter_documents(repo / "docs", extensions=(".md",))
    out: list[Documento] = []
    for path in files:
        text = path.read_text()
        try:
            sections = parse(path, text).sections
        except ParseError:
            sections = []
        lines = text.splitlines()
        out.append(Documento(path, path.relative_to(repo).as_posix(), lines, sections, _bloques(lines)))
    return out
