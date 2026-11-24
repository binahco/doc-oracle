from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

import yaml
from cache_ratelimit import CachedProvider, RateLimiter, RealClock, ThrottledProvider
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider, Span
from llm_client.providers.opencode_cli import OpenCodeCLI
from schema_validate import SchemaRegistry
from secure_base import sanitize_for_prompt
from test_kit import EvalCase, EvalDataset, run

from .models import Claim, DocVerdict
from .oracles import Flota
from .scan import audit, hunks, reporte

DEFAULT_MODEL = "opencode/big-pickle"
SCHEMA_ID = "doc-verdict-v1"
PROMPT_ID = "doc-verdict-generator"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = ROOT / "prompts" / f"{PROMPT_ID}.md"
DEFAULT_DATASET = ROOT / "evals" / f"{PROMPT_ID}.jsonl"
TAGS = ["doc-oracle", "week-24"]


# --------------------------------------------------------------------------
# la narración: único LLM, validado o falla
# --------------------------------------------------------------------------
def load_prompt(path: Path) -> tuple[str, str, str, Path]:
    text = path.read_text()
    if not text.startswith("---"):
        raise SystemExit(f"{path}: falta frontmatter")
    _, frontmatter, body = text.split("---", 2)
    data = yaml.safe_load(frontmatter)
    eval_path = ROOT / data["eval"] if not Path(data["eval"]).is_absolute() else Path(data["eval"])
    return data["id"], data["version"], body.strip(), eval_path


def render_verdict(prompt_id: str, prompt_version: str, variables: dict) -> list[dict]:
    if prompt_id == PROMPT_ID:
        _, _, body, _ = load_prompt(DEFAULT_PROMPT)
        system_part = body.split("## Sistema\n", 1)[1].split("## Usuario\n", 1)[0].strip()
        user_part = body.split("## Usuario\n", 1)[1].strip().format(**variables)
        return [{"role": "system", "content": system_part}, {"role": "user", "content": user_part}]
    payload = {"prompt_id": prompt_id, "prompt_version": prompt_version, "variables": variables}
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def build_emitter(span_file: Path | None):
    def emit(span: Span, _result) -> None:
        if span_file is not None:
            with span_file.open("a") as handle:
                handle.write(span.as_jsonl() + "\n")
        else:
            sys.stderr.write(span.as_jsonl() + "\n")

    return emit


def build_client(inner_provider, *, clock=None, span_file: Path | None = None) -> dict:
    clock = RealClock() if clock is None else clock
    cached = CachedProvider(inner_provider, ttl_seconds=3600.0, clock=clock)
    limiter = RateLimiter(rpm=120, burst=20, clock=clock)
    provider = ThrottledProvider(cached, limiter)
    registry = SchemaRegistry()
    registry.register(SCHEMA_ID, DocVerdict)
    client = LlmClient(
        provider,
        consumer_repo="doc-oracle",
        model_aliases={"fast": DEFAULT_MODEL},
        renderer=render_verdict,
        validator=registry.make_validator(SCHEMA_ID),
        emitter=build_emitter(span_file),
    )
    return {"client": client, "cached": cached, "limiter": limiter}


def posiciones(vencidos: list[Claim]) -> set[tuple[str, int]]:
    """Las líneas afectadas. El arreglo se aplica por línea: es la unidad que no se repite."""
    return {(c.doc, c.linea) for c in vencidos}


def claims_text(vencidos: list[Claim]) -> str:
    por_linea: dict[tuple[str, int], list[Claim]] = {}
    for c in vencidos:
        por_linea.setdefault((c.doc, c.linea), []).append(c)
    bloques = []
    for i, ((doc, linea), claims) in enumerate(por_linea.items(), start=1):
        afirmaciones = [
            f"  - tipo: {c.tipo} · sujeto: {c.sujeto or '-'} · dice: {c.dice} · mide: {c.mide} · oráculo: {c.oraculo}"
            + (f" · detalle: {c.detalle}" if c.detalle else "")
            for c in claims
        ]
        bloques.append(
            "\n".join(
                [
                    f"LÍNEA {i}",
                    f"doc: {doc} · línea: {linea} · sección: {claims[0].seccion or '(cabecera)'}",
                    f"texto de la línea: {claims[0].texto}",
                    "afirmaciones vencidas:",
                    *afirmaciones,
                ]
            )
        )
    return "\n\n".join(bloques)


def verificar_citas(verdict: DocVerdict, posiciones: set[tuple[str, int]]) -> None:
    """La regla: la narración solo puede ordenar líneas medidas.

    Citar una que no se midió, omitir una o repetirla anula la narración completa.
    """
    citadas = [(a.doc, a.linea) for a in verdict.orden]
    inventadas = sorted(set(citadas) - posiciones)
    omitidas = sorted(posiciones - set(citadas))
    repetidas = sorted({p for p in citadas if citadas.count(p) > 1})
    if inventadas or omitidas or repetidas:
        raise RuntimeError(
            "la narración no coincide con lo medido:"
            f" inventadas={inventadas} omitidas={omitidas} repetidas={repetidas}"
        )


def narrate(client: LlmClient, vencidos: list[Claim]) -> DocVerdict:
    if not vencidos:
        return DocVerdict(summary="sin afirmaciones vencidas: el repo dice de sí mismo lo que el oráculo mide")
    texto = sanitize_for_prompt(claims_text(vencidos)).text.replace("{", "(").replace("}", ")")
    result = client.complete(
        CompletionRequest(
            prompt_id=PROMPT_ID,
            prompt_version="0.1.0",
            variables={"claims": texto},
            model_alias="fast",
            response_schema=SCHEMA_ID,
            tags=TAGS,
        )
    )
    if not result.validation.ok:
        raise RuntimeError("; ".join(result.validation.errors))
    verdict = result.parsed
    if not isinstance(verdict, DocVerdict):
        raise RuntimeError("el dictamen validó pero no es un DocVerdict")
    verificar_citas(verdict, posiciones(vencidos))
    return verdict


def judge_for(client: LlmClient) -> Callable[[EvalCase], CompletionResult]:
    def judge(case: EvalCase) -> CompletionResult:
        result = client.complete(
            CompletionRequest(
                prompt_id=case.prompt_id,
                prompt_version=case.prompt_version,
                variables=case.input,
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=TAGS,
            )
        )
        if result.validation.ok and isinstance(result.parsed, DocVerdict):
            verificar_citas(result.parsed, {(doc, linea) for doc, linea in case.input["posiciones"]})
        return result

    return judge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc-oracle", description="Lo que un repo afirma de sí mismo contra su oráculo (semana 24)."
    )
    parser.add_argument("command", nargs="?", default="scan", choices=["scan", "narrate", "record", "replay-check"])
    parser.add_argument("--repo", default=str(ROOT.parent / "llm-dev-core"), help="repo a auditar")
    parser.add_argument("--flota", default=None, help="directorio con los consumidores (default: el padre del repo)")
    parser.add_argument("--todo", action="store_true", help="lista también lo que no está vencido")
    args = parser.parse_args(argv)

    if args.command in {"record", "replay-check"}:
        dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
        if args.command == "replay-check":
            provider = ReplayProvider(ROOT / "cassettes", record=False)
        else:
            provider = ReplayProvider(ROOT / "cassettes", record=True, inner=OpenCodeCLI(DEFAULT_MODEL))
        out = run(dataset, judge_for(build_client(provider)["client"]), mode="full", threshold=1.0)
        print(f"pass={out.passed}/{out.total} threshold_ok={out.threshold_ok}")
        return 0 if out.threshold_ok else 1

    flota = Flota(args.flota) if args.flota else None
    auditoria = audit(Path(args.repo), flota)
    if args.command == "scan":
        print(json.dumps({**reporte(auditoria, todo=args.todo), "hunks": hunks(auditoria)}, ensure_ascii=False, indent=2))
        return 1 if auditoria.vencidos else 0

    verdict = narrate(build_client(OpenCodeCLI(DEFAULT_MODEL))["client"], auditoria.vencidos)
    print(
        json.dumps(
            {**reporte(auditoria), "hunks": hunks(auditoria), "veredicto": verdict.model_dump()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if auditoria.vencidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
