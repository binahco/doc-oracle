from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Estado = Literal["ok", "vencido", "planificado", "sin_oraculo"]

ESTADOS: tuple[Estado, ...] = ("ok", "vencido", "planificado", "sin_oraculo")


class Claim(BaseModel):
    """Una afirmación de un documento vivo sobre su propio repo, medida contra su oráculo."""

    tipo: str
    doc: str
    linea: int = Field(ge=1)
    seccion: str = ""
    sujeto: str = ""
    dice: str
    mide: str | None = None
    oraculo: str
    estado: Estado
    detalle: str = ""
    texto: str = ""
    # Dónde está lo afirmado dentro de la línea y con qué texto se corrige: alimentan los hunks.
    inicio: int = Field(default=0, exclude=True)
    fin: int = Field(default=0, exclude=True)
    propuesta: str | None = Field(default=None, exclude=True)


class Arreglo(BaseModel):
    """El texto con el que se corrige una afirmación vencida."""

    doc: str
    linea: int = Field(ge=1)
    arreglo: str = Field(min_length=1)


class DocVerdict(BaseModel):
    """Afirmaciones vencidas, ordenadas para arreglar (`doc-verdict-v1`)."""

    summary: str = Field(min_length=1)
    orden: list[Arreglo] = Field(default_factory=list)
