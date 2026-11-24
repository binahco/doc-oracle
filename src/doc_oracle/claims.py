"""El conjunto cerrado de afirmaciones y su oráculo.

Cada tipo tiene una forma anclada —no "cualquier número"— y un oráculo determinista.
Lo que no encaja en una forma no es afirmación: es contexto, ejemplo o meta.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .corpus import Bloque, Documento
from .models import Claim, Estado
from .oracles import Contexto

VERSION = r"(\d+\.\d+(?:\.\d+)?)"
ORD = (
    r"(?:primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|sext[oa]|s[eé]ptim[oa]|octav[oa]|"
    r"noven[oa]|d[eé]cim[oa]|und[eé]cim[oa]|duod[eé]cim[oa]|\d+(?:[.º°]|er|do|to|o|a)?)"
)
_MODULO = r"\**(`?)([\w-]+)`?"
TEXTO = 200  # caracteres de la línea que viajan al reporte y al prompt

ORDINALES = {
    "primer": 1, "primero": 1, "primera": 1,
    "segundo": 2, "segunda": 2,
    "tercer": 3, "tercero": 3, "tercera": 3,
    "cuarto": 4, "cuarta": 4,
    "quinto": 5, "quinta": 5,
    "sexto": 6, "sexta": 6,
    "septimo": 7, "septima": 7,
    "octavo": 8, "octava": 8,
    "noveno": 9, "novena": 9,
    "decimo": 10, "decima": 10,
    "undecimo": 11, "undecima": 11,
    "duodecimo": 12, "duodecima": 12,
}
_PALABRAS = (
    "", "primer", "segundo", "tercer", "cuarto", "quinto", "sexto",
    "séptimo", "octavo", "noveno", "décimo", "undécimo", "duodécimo",
)

Medir = Callable[[re.Match, Documento, Bloque, Contexto], Claim | None]


def _sin_acento(palabra: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", palabra.lower()) if not unicodedata.combining(ch))


def ordinal(token: str) -> int:
    """Valor de un ordinal en español: `tercer`, `Tercera`, `3.º`, `3er`, `12`."""
    if token[0].isdigit():
        return int(re.match(r"\d+", token).group())
    try:
        return ORDINALES[_sin_acento(token)]
    except KeyError:
        raise ValueError(f"ordinal desconocido: {token}") from None


def _palabra(n: int, *, fem: bool) -> str | None:
    """Cómo se escribe el ordinal `n` (hasta el 12), en el género del texto original."""
    if not 1 <= n < len(_PALABRAS):
        return None
    base = _PALABRAS[n]
    if not fem:
        return base
    return f"{base}a" if base.endswith("er") else f"{base[:-1]}a"


def _como_version(texto: str) -> tuple[int, ...]:
    partes = tuple(int(p) for p in texto.split("."))
    return partes + (0,) * (3 - len(partes))


def _es_mencion(texto: str, m: re.Match) -> bool:
    """Lo que va entre comillas o acentos es una mención (un ejemplo), no un uso."""
    abre, cierra = ("`", '"', "«", "“"), ("`", '"', "»", "”")
    return texto[m.start() - 1 : m.start()] in abre and texto[m.end() : m.end() + 1] in cierra


def _arranque(doc: Documento) -> Bloque | None:
    """El primer párrafo tras el H1: donde un README dice de qué semana es."""
    parrafos = [b for b in doc.bloques if not b.valla]
    if not parrafos:
        return None
    primero = parrafos[0]
    if len(parrafos) > 1 and primero.lineas == [1] and doc.lines[0].startswith("# "):
        return parrafos[1]
    return primero


def _claim(
    tipo: str,
    doc: Documento,
    bloque: Bloque,
    m: re.Match,
    *,
    grupo: int,
    sujeto: str,
    dice: str,
    oraculo: str,
    mide: str | None,
    estado: Estado,
    detalle: str = "",
    propuesta: str | None = None,
) -> Claim:
    # La afirmación vive en la línea del valor afirmado (`grupo`), que puede no ser la
    # línea donde arranca la frase.
    linea, base = bloque.ubicar(m.start(grupo))
    return Claim(
        tipo=tipo,
        doc=doc.rel,
        linea=linea,
        seccion=doc.seccion(linea),
        sujeto=sujeto,
        dice=dice,
        mide=mide,
        oraculo=oraculo,
        estado=estado,
        detalle=detalle,
        texto=doc.lines[linea - 1].strip()[:TEXTO],
        inicio=m.start(grupo) - base,
        fin=m.end(grupo) - base,
        propuesta=propuesta,
    )


def _veredicto(dice, mide) -> Estado:
    """Sin oráculo no hay veredicto: se dice, no se presume."""
    return "sin_oraculo" if mide is None else "ok" if dice == mide else "vencido"


# --------------------------------------------------------------------------
# versión y semana del repo auditado
# --------------------------------------------------------------------------
def _mide_version(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim:
    real = ctx.version
    estado = _veredicto(_como_version(m.group(1)), _como_version(real) if real else None)
    return _claim(
        "version", doc, bloque, m, grupo=1, sujeto=ctx.repo.name, dice=f"v{m.group(1)}",
        oraculo="pyproject.toml: [project] version", mide=f"v{real}" if real else None, estado=estado,
        detalle="" if real else "el repo no declara versión en pyproject.toml",
        propuesta=real if estado == "vencido" else None,
    )


def _mide_semana_estado(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim:
    actual = ctx.semana_actual
    estado = _veredicto(int(m.group(1)), actual)
    return _claim(
        "semana_estado", doc, bloque, m, grupo=1, sujeto=ctx.repo.name, dice=f"semana {m.group(1)}",
        oraculo="CHANGELOG.md: semana de la entrada más reciente",
        mide=f"semana {actual}" if actual else None, estado=estado,
        propuesta=str(actual) if estado == "vencido" else None,
    )


# --------------------------------------------------------------------------
# README de un consumidor contra su propio manifiesto
# --------------------------------------------------------------------------
def _mide_semana_repo(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim | None:
    week = ctx.manifiesto.get("week")
    if not isinstance(week, int):
        return None  # sin manifiesto no hay semana que afirmar
    estado = _veredicto(int(m.group(1)), week)
    return _claim(
        "semana_repo", doc, bloque, m, grupo=1, sujeto=ctx.repo.name, dice=f"semana {m.group(1)}",
        oraculo="core-consumer.yml: week", mide=f"semana {week}", estado=estado,
        propuesta=str(week) if estado == "vencido" else None,
    )


def _mide_core_version(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim | None:
    declarada = ctx.manifiesto.get("core_version")
    if not isinstance(declarada, (str, int, float)):
        return None
    real = str(declarada)
    estado = _veredicto(_como_version(m.group(1)), _como_version(real))
    return _claim(
        "core_version", doc, bloque, m, grupo=1, sujeto=ctx.repo.name, dice=f"v{m.group(1)}",
        oraculo="core-consumer.yml: core_version", mide=f"v{real}", estado=estado,
        propuesta=real if estado == "vencido" else None,
    )


# --------------------------------------------------------------------------
# orden de consumidor: la regla del tercer uso (D12) se decide con estas frases
# --------------------------------------------------------------------------
def _mide_orden_propio(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim | None:
    modulo = m.group(3)
    if not m.group(2) and modulo not in ctx.vocabulario:
        return None  # "consumidor de la caché": no es un módulo, no es afirmación
    return _orden(m, doc, bloque, ctx, repo=ctx.repo.name, modulo=modulo, propio=True)


def _mide_orden_nombre(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim:
    return _orden(m, doc, bloque, ctx, repo=m.group(2).rsplit("/", 1)[-1], modulo=doc.modulo_del_titulo(), propio=False)


def _orden(
    m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto, *, repo: str, modulo: str | None, propio: bool
) -> Claim:
    n, token = ordinal(m.group(1)), m.group(1)
    lugar = ctx.flota.posicion(repo, modulo)
    detalle = ""
    if lugar is None:
        detalle = f"{repo} no declara `{modulo}` en su manifiesto" if modulo else f"{repo} no está en la flota"
    estado = _veredicto(n, lugar)
    propuesta = None
    if propio and estado == "vencido":
        palabra = _palabra(lugar, fem=token.endswith("a"))
        propuesta = palabra.capitalize() if palabra and token[0].isupper() else palabra
    return _claim(
        "orden_consumidor", doc, bloque, m, grupo=1, sujeto=repo, dice=token.lower(),
        oraculo=(
            f"manifiestos de la flota: lugar de {repo} entre los consumidores de `{modulo}`, por semana"
            if modulo
            else f"manifiestos de la flota: lugar de {repo} entre los consumidores del core, por semana"
        ),
        mide=f"{_palabra(lugar, fem=False) or lugar} ({lugar})" if lugar else None,
        estado=estado, detalle=detalle, propuesta=propuesta,
    )


# --------------------------------------------------------------------------
# semilla de un módulo del core y contrato del span
# --------------------------------------------------------------------------
def _mide_seed(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim:
    modulo, n = m.group(1), int(m.group(2))
    if ctx.tiene_paquete(modulo):
        real = ctx.semana_de_siembra(modulo)
        estado = _veredicto(n, real)
        return _claim(
            "seed", doc, bloque, m, grupo=2, sujeto=modulo, dice=f"sem. {n}",
            oraculo=f"ADR de {modulo}: 'Implementada en' semana", mide=f"sem. {real}" if real else None,
            estado=estado,
            detalle="" if real else "el paquete existe pero ningún ADR declara su semana de siembra",
            propuesta=str(real) if estado == "vencido" else None,
        )
    actual = ctx.semana_actual
    detalle, estado, mide = "", "sin_oraculo", None
    if actual is not None:
        if n > actual:
            estado, mide = "planificado", "sin paquete todavía"
            detalle = f"el calendario lo siembra en {n - actual} semana(s)"
        else:
            estado, mide = "vencido", "sin paquete"
            detalle = "el paquete no existe"
            if consumidor := ctx.flota.por_nombre(modulo):
                detalle += (
                    f"; existe como consumidor de la flota (semana {consumidor.week}),"
                    " no como módulo del core"
                )
    return _claim(
        "seed", doc, bloque, m, grupo=2, sujeto=modulo, dice=f"sem. {n}",
        oraculo="packages/ + CHANGELOG.md: semana de la entrada más reciente",
        mide=mide, estado=estado, detalle=detalle,
    )


def _mide_span(m: re.Match, doc: Documento, bloque: Bloque, ctx: Contexto) -> Claim:
    real = ctx.campos_span
    estado = _veredicto(int(m.group(1)), real)
    return _claim(
        "span_campos", doc, bloque, m, grupo=1, sujeto="Span", dice=f"{m.group(1)} campos",
        oraculo="len(Span.model_fields)", mide=f"{real} campos", estado=estado,
        propuesta=str(real) if estado == "vencido" else None,
    )


@dataclass(frozen=True)
class Tipo:
    nombre: str
    patron: str
    medir: Medir
    solo: str | None = None  # documento único donde vive la forma (p. ej. README.md)
    bloque: Callable[[Documento], Bloque | None] | None = None  # un solo bloque del documento
    primero: bool = False  # solo la primera aparición
    en_valla: bool = False  # vale también dentro de un bloque de código

    def buscar(self, doc: Documento, ctx: Contexto) -> list[Claim]:
        if self.solo and doc.rel != self.solo:
            return []
        unico = self.bloque(doc) if self.bloque else None
        out: list[Claim] = []
        for bloque in doc.bloques:
            if bloque.valla and not self.en_valla:
                continue  # una salida capturada es transcripción, no afirmación
            if self.bloque and bloque is not unico:
                continue
            for m in re.finditer(self.patron, bloque.texto, re.IGNORECASE | re.MULTILINE):
                if _es_mencion(bloque.texto, m):
                    continue
                claim = self.medir(m, doc, bloque, ctx)
                if claim is not None:
                    out.append(claim)
                    if self.primero:
                        return out
        return out


TIPOS: tuple[Tipo, ...] = (
    Tipo("version", rf"\*\*(?:Versi[oó]n|Estado):\*\*\s*v?{VERSION}", _mide_version),
    Tipo("semana_estado", r"\*\*Estado:\*\*.*?\bSemana\s+(\d+)", _mide_semana_estado),
    Tipo("semana_repo", r"\*\*Semana:\*\*\s*(\d+)", _mide_semana_repo, solo="README.md"),
    Tipo(
        "semana_repo", r"\((?:semana|sem\.)\s+(\d+)\)", _mide_semana_repo,
        solo="README.md", bloque=_arranque, primero=True,
    ),
    Tipo("core_version", rf"\*\*Core:\*\*\s*`llm-dev-core`\s*v?{VERSION}", _mide_core_version, solo="README.md"),
    Tipo(
        "orden_consumidor", rf"\b(?:es\s+el|soy\s+el)\s+\**\s*({ORD})\s+consumidor\s+de\s+{_MODULO}",
        _mide_orden_propio,
    ),
    Tipo(
        "orden_consumidor", rf"^\s*[-*]\s+\[[xX]\]\s+\**\s*({ORD})\s+consumidor\s+de\s+{_MODULO}",
        _mide_orden_propio,
    ),
    # Con nombre solo vale entre acentos graves (`primer consumidor: `cassette-diff``):
    # "segundo consumidor: hunks línea a línea" explica, no nombra.
    Tipo("orden_consumidor", rf"\b({ORD})\s+consumidor:\s*`([\w./-]+)`", _mide_orden_nombre),
    Tipo("seed", r"([a-z][\w-]*)/?\s+#\s*C\d+\s+\[seed\s+sem\.?\s*(\d+)\]", _mide_seed, en_valla=True),
    Tipo("span_campos", r"span de (\d+) campos", _mide_span),
)
