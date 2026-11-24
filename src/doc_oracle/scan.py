from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from diff_engine import diff_text

from .claims import TIPOS
from .corpus import Documento, living_docs
from .models import ESTADOS, Claim
from .oracles import Contexto, Flota


@dataclass
class Auditoria:
    repo: Path
    docs: list[Documento]
    claims: list[Claim]

    @property
    def vencidos(self) -> list[Claim]:
        return [c for c in self.claims if c.estado == "vencido"]

    def por_estado(self) -> dict[str, int]:
        conteo = dict.fromkeys(ESTADOS, 0)
        for c in self.claims:
            conteo[c.estado] += 1
        return conteo


def audit(repo: Path, flota: Flota | None = None) -> Auditoria:
    """Mide cada afirmación de cada documento vivo. Determinista: mismo árbol, mismo veredicto."""
    repo = repo.resolve()
    ctx = Contexto(repo, flota or Flota(repo.parent))
    docs = living_docs(repo)
    claims = [claim for doc in docs for tipo in TIPOS for claim in tipo.buscar(doc, ctx)]
    orden = {doc.rel: i for i, doc in enumerate(docs)}
    claims.sort(key=lambda c: (orden[c.doc], c.linea, c.inicio))
    return Auditoria(repo, docs, claims)


def hunks(auditoria: Auditoria) -> list[dict]:
    """Lo que hay que cambiar para que el documento diga lo que el oráculo mide (diff-engine).

    Hunks con líneas, no parches (ADR-10): el consumidor muestra; nadie aplica.
    """
    out: list[dict] = []
    for doc in auditoria.docs:
        por_linea: dict[int, list[Claim]] = {}
        for c in auditoria.vencidos:
            if c.doc == doc.rel and c.propuesta is not None:
                por_linea.setdefault(c.linea, []).append(c)
        if not por_linea:
            continue
        corregido = list(doc.lines)
        for linea, cambios in por_linea.items():
            texto = doc.lines[linea - 1]
            for c in sorted(cambios, key=lambda c: c.fin, reverse=True):
                texto = texto[: c.inicio] + c.propuesta + texto[c.fin :]
            corregido[linea - 1] = texto
        for hunk in diff_text("\n".join(doc.lines), "\n".join(corregido)):
            out.append({"doc": doc.rel, **hunk.model_dump()})
    return out


def reporte(auditoria: Auditoria, *, todo: bool = False) -> dict:
    datos = {
        "repo": auditoria.repo.name,
        "documentos": len(auditoria.docs),
        "afirmaciones": len(auditoria.claims),
        "por_estado": auditoria.por_estado(),
        "vencidos": [c.model_dump() for c in auditoria.vencidos],
    }
    if todo:
        datos["mediciones"] = [c.model_dump() for c in auditoria.claims]
    return datos
