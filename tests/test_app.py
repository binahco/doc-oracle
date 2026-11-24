from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from llm_client import Span
from llm_client.provider import ProviderRequest, ProviderResponse
from test_kit import EvalDataset, run as run_evalset

from doc_oracle.claims import ordinal
from doc_oracle.main import DEFAULT_DATASET, build_client, judge_for, main, narrate, verificar_citas
from doc_oracle.models import Arreglo, Claim, DocVerdict
from doc_oracle.scan import audit, hunks, reporte

TOKEN = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz123456"


class StubProvider:
    """Devuelve siempre el mismo JSON: para probar la regla de las citas."""

    name = "stub"

    def __init__(self, orden: list[tuple[str, int]]) -> None:
        self.text = json.dumps(
            {"summary": "resumen", "orden": [{"doc": d, "linea": n, "arreglo": "corregida"} for d, n in orden]},
            ensure_ascii=False,
        )
        self.calls = 0
        self.last_request: ProviderRequest | None = None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
        return ProviderResponse(text=self.text, model=request.model)

    def stream(self, request: ProviderRequest) -> list[str]:
        self.calls += 1
        return [self.text]


class EcoProvider(StubProvider):
    """Cita exactamente las líneas que recibió: la narración válida por definición."""

    def __init__(self) -> None:
        super().__init__([])
        self.text = ""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
        citadas = re.findall(r"doc: (\S+) · línea: (\d+)", request.messages[-1]["content"])
        orden = [{"doc": doc, "linea": int(linea), "arreglo": "corregida"} for doc, linea in citadas]
        text = json.dumps({"summary": "resumen", "orden": orden}, ensure_ascii=False)
        return ProviderResponse(text=text, model=request.model)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _consumer(root: Path, name: str, week: int, modules: list[str], readme: str, core: str = "1.4.0") -> Path:
    repo = root / name
    lista = ", ".join(modules)
    _write(repo / "core-consumer.yml", f"week: {week}\nname: {name}\ncore_version: {core}\nreused_modules: [{lista}]\n")
    _write(repo / "README.md", readme)
    return repo


def mini_flota(tmp_path: Path) -> Path:
    """Un core con cinco afirmaciones vencidas y cuatro consumidores con manifiesto. Devuelve la raíz de la flota."""
    core = tmp_path / "core"
    _write(core / "pyproject.toml", '[project]\nname = "llm-dev-core"\nversion = "1.4.0"\n')
    _write(
        core / "CHANGELOG.md",
        "# Changelog\n\n## v1.4.0 — 2026-11-21 (semana 23, C11)\n\n- seed\n\n## v1.0 — 2026-09-30 (hito v1.0, semana 13)\n",
    )
    _write(core / "README.md", "# core\n\nNúcleo.\n\n- **Estado:** v1.0 · Semana 13 (hito: 12 consumidores, contratos estables)\n")
    _write(
        core / "ARCHITECTURE.md",
        "# Arquitectura\n\n> **Estado:** v0.4 (seed revisado)\n\n## 5. Estructura\n\n```\npackages/\n"
        "│   ├── llm-client/   # C1  [seed sem. 2]\n"
        "│   ├── docs-gen/     # C10 [seed sem. 12]\n"
        "│   ├── vector-core/  # C11 [seed sem. 16]\n"
        "│   └── auth-base/    # C14 [seed sem. 25]\n"
        "```\n",
    )
    for modulo in ("llm-client", "vector-core"):
        _write(core / "packages" / modulo / "pyproject.toml", "[project]\n")
    _write(
        core / "docs" / "decisions" / "0001-llm-client-contract.md",
        "# Contrato mínimo — `llm-client`\n\n- **Implementada en:** semana 2 (`packages/llm-client` v0.1.0)\n",
    )
    _write(
        core / "docs" / "decisions" / "0012-vector-core-contract.md",
        "# Contrato mínimo — `vector-core`\n\n- **Implementada en:** semana 23 (`packages/vector-core` v0.1.0)\n",
    )
    _consumer(
        tmp_path, "alpha", 10, ["llm-client", "parser-io"],
        "# alpha\n\nHace la una (semana 10). Es el **primer consumidor de `parser-io`**.\n\n## Roadmap\n\n"
        "- [ ] Segundo consumidor de `parser-io`: lo que venga\n"
        "- sin expresiones cron en v1 (queda para el segundo consumidor de `bot-base`)\n",
    )
    _consumer(tmp_path, "beta", 17, ["parser-io"], "# beta\n\nHace la otra (semana 17): es el **cuarto consumidor de `parser-io`**.\n")
    _consumer(
        tmp_path, "gamma", 12, ["llm-client"],
        f"# gamma\n\n> **Semana:** 14 · **Core:** `llm-dev-core` 0.9.0 — probado con token {TOKEN}\n",
        core="1.1.0",
    )
    _consumer(tmp_path, "docs-gen", 12, ["parser-io"], "# docs-gen\n\nSitio de documentación (semana 12).\n")
    return tmp_path


def _claim(auditoria, tipo: str, doc: str, sujeto: str | None = None) -> Claim:
    return next(c for c in auditoria.claims if c.tipo == tipo and c.doc == doc and (sujeto is None or c.sujeto == sujeto))


def test_el_ordinal_en_espanol_se_lee_en_palabras_y_en_cifras() -> None:
    assert [ordinal(t) for t in ("primer", "Tercera", "séptimo", "duodécimo")] == [1, 3, 7, 12]
    assert [ordinal(t) for t in ("3.º", "3er", "12", "2do")] == [3, 3, 12, 2]


def test_la_version_y_la_semana_del_readme_se_miden_contra_el_repo(tmp_path) -> None:
    a = audit(mini_flota(tmp_path) / "core")
    version = _claim(a, "version", "README.md")
    assert (version.dice, version.mide, version.estado) == ("v1.0", "v1.4.0", "vencido")
    semana = _claim(a, "semana_estado", "README.md")
    assert (semana.dice, semana.mide, semana.estado, semana.linea) == ("semana 13", "semana 23", "vencido", 5)
    assert _claim(a, "version", "ARCHITECTURE.md").estado == "vencido"


def test_el_seed_se_mide_contra_el_adr_y_distingue_el_plan_de_la_deuda(tmp_path) -> None:
    semillas = {c.sujeto: c for c in audit(mini_flota(tmp_path) / "core").claims if c.tipo == "seed"}
    assert semillas["llm-client"].estado == "ok"
    vector = semillas["vector-core"]
    assert (vector.estado, vector.mide, vector.seccion) == ("vencido", "sem. 23", "5. Estructura")
    assert semillas["auth-base"].estado == "planificado"
    docs_gen = semillas["docs-gen"]
    assert docs_gen.estado == "vencido"
    assert "existe como consumidor de la flota (semana 12)" in docs_gen.detalle


def test_el_orden_de_consumidor_sale_de_los_manifiestos_y_una_meta_no_es_afirmacion(tmp_path) -> None:
    alpha = audit(mini_flota(tmp_path) / "alpha")
    propios = [c for c in alpha.claims if c.tipo == "orden_consumidor"]
    assert [(c.dice, c.mide, c.estado) for c in propios] == [("primer", "primer (1)", "ok")]  # solo "es el…"
    beta = audit(tmp_path / "beta")
    orden = _claim(beta, "orden_consumidor", "README.md")
    # alpha (10), docs-gen (12) y beta (17) declaran parser-io: beta es el tercero, no el cuarto
    assert (orden.dice, orden.mide, orden.estado) == ("cuarto", "tercer (3)", "vencido")
    assert [h["lineas_b"] for h in hunks(beta)] == [["Hace la otra (semana 17): es el **tercer consumidor de `parser-io`**."]]


def test_el_readme_de_un_consumidor_se_mide_contra_su_manifiesto(tmp_path) -> None:
    a = audit(mini_flota(tmp_path) / "gamma")
    semana = _claim(a, "semana_repo", "README.md")
    core = _claim(a, "core_version", "README.md")
    assert (semana.dice, semana.mide, semana.estado) == ("semana 14", "semana 12", "vencido")
    assert (core.dice, core.mide, core.estado) == ("v0.9.0", "v1.1.0", "vencido")


def test_el_contrato_del_span_se_mide_contra_el_modelo(tmp_path) -> None:
    real = len(Span.model_fields)
    raiz = mini_flota(tmp_path)
    delta = _consumer(
        raiz, "delta", 20, ["llm-client"],
        f"# delta\n\nDelta (semana 20).\n\n## Cómo funciona\n\nEl span de {real + 1} campos viaja con cada llamada.\n",
    )
    claim = _claim(audit(delta), "span_campos", "README.md")
    assert (claim.dice, claim.mide, claim.estado, claim.seccion) == (f"{real + 1} campos", f"{real} campos", "vencido", "Cómo funciona")


def test_una_cifra_entre_comillas_es_una_mencion_no_una_afirmacion(tmp_path) -> None:
    raiz = mini_flota(tmp_path)
    _write(raiz / "core" / "docs" / "guia.md", f'# Guía\n\nLa frase "span de {len(Span.model_fields) + 5} campos" es solo un ejemplo.\n')
    tipos = {(c.doc, c.tipo) for c in audit(raiz / "core").claims}
    assert ("docs/guia.md", "span_campos") not in tipos


def test_dentro_de_un_bloque_de_codigo_solo_se_audita_el_arbol_de_modulos(tmp_path) -> None:
    core = mini_flota(tmp_path) / "core"
    _write(
        core / "docs" / "demo.md",
        '# Demo\n\n```bash\n# "lineas_a": ["- **Estado:** v9.9 · Semana 99 (hito)"]\n'
        '```\n\nFuera de la valla: **Versión:** v1.0\n',
    )
    claims = [c for c in audit(core).claims if c.doc == "docs/demo.md"]
    assert [(c.tipo, c.linea, c.dice) for c in claims] == [("version", 7, "v1.0")]


def test_una_frase_partida_en_dos_lineas_sigue_siendo_una_frase(tmp_path) -> None:
    epsilon = _consumer(
        mini_flota(tmp_path), "epsilon", 18, ["parser-io"],
        "# epsilon\n\nHace la otra cosa (semana 18). Es el\n**quinto consumidor de `parser-io`**.\n",
    )
    a = audit(epsilon)
    orden = _claim(a, "orden_consumidor", "README.md")
    assert (orden.linea, orden.dice, orden.mide, orden.estado) == (4, "quinto", "cuarto (4)", "vencido")
    assert [h for h in hunks(a) if h["doc"] == "README.md"] == [
        {
            "doc": "README.md", "op": "cambiada", "a_start": 4, "b_start": 4,
            "lineas_a": ["**quinto consumidor de `parser-io`**."],
            "lineas_b": ["**cuarto consumidor de `parser-io`**."],
        }
    ]


def test_el_consumidor_por_nombre_solo_cuenta_entre_acentos(tmp_path) -> None:
    core = mini_flota(tmp_path) / "core"
    _write(
        core / "docs" / "decisions" / "0009-parser-io-contract.md",
        "# Contrato mínimo — `parser-io`\n\n- Primer consumidor: `alpha`\n- Segundo consumidor: hunks línea a línea\n",
    )
    claims = [c for c in audit(core).claims if c.doc.endswith("0009-parser-io-contract.md")]
    assert [(c.sujeto, c.dice, c.mide, c.estado) for c in claims] == [("alpha", "primer", "primer (1)", "ok")]


def test_los_hunks_proponen_el_texto_corregido_sin_aplicarlo(tmp_path) -> None:
    core = mini_flota(tmp_path) / "core"
    readme, architecture = core / "README.md", core / "ARCHITECTURE.md"
    antes = (readme.read_text(), architecture.read_text())
    cambios = {(h["doc"], h["a_start"]): h for h in hunks(audit(core))}
    assert cambios["README.md", 5]["lineas_b"] == [
        "- **Estado:** v1.4.0 · Semana 23 (hito: 12 consumidores, contratos estables)"
    ]
    assert cambios["ARCHITECTURE.md", 11]["lineas_b"] == ["│   ├── vector-core/  # C11 [seed sem. 23]"]
    assert ("ARCHITECTURE.md", 10) not in cambios  # sin paquete no hay número que proponer
    assert (readme.read_text(), architecture.read_text()) == antes


def test_mismo_arbol_mismo_veredicto(tmp_path) -> None:
    core = mini_flota(tmp_path) / "core"
    assert reporte(audit(core), todo=True) == reporte(audit(core), todo=True)


def test_scan_sale_1_cuando_hay_vencidos(tmp_path, capsys) -> None:
    raiz = mini_flota(tmp_path)
    assert main(["scan", "--repo", str(raiz / "core")]) == 1
    salida = json.loads(capsys.readouterr().out)
    assert salida["por_estado"] == {"ok": 1, "vencido": 5, "planificado": 1, "sin_oraculo": 0} and salida["hunks"]
    assert main(["scan", "--repo", str(raiz / "alpha")]) == 0
    assert json.loads(capsys.readouterr().out)["vencidos"] == []


def test_la_narracion_valida_y_no_recibe_secretos(tmp_path) -> None:
    stub = EcoProvider()
    verdict = narrate(build_client(stub)["client"], audit(mini_flota(tmp_path) / "gamma").vencidos)
    assert [(a.doc, a.linea) for a in verdict.orden] == [("README.md", 3)]
    mensaje = json.dumps([m["content"] for m in stub.last_request.messages], ensure_ascii=False)
    assert "ghp_" not in mensaje, "un secreto del README entró en la narración"


def test_la_narracion_que_cita_una_linea_no_medida_se_anula(tmp_path) -> None:
    vencidos = audit(mini_flota(tmp_path) / "core").vencidos
    with pytest.raises(RuntimeError, match="inventadas"):
        narrate(build_client(StubProvider([("README.md", 5), ("README.md", 999)]))["client"], vencidos)
    with pytest.raises(RuntimeError, match="omitidas"):
        narrate(build_client(StubProvider([("README.md", 5)]))["client"], vencidos)
    solo_readme = [v for v in vencidos if v.doc == "README.md"]
    with pytest.raises(RuntimeError, match="repetidas"):
        narrate(build_client(StubProvider([("README.md", 5), ("README.md", 5)]))["client"], solo_readme)


def test_sin_vencidos_no_hay_llamada(tmp_path) -> None:
    stub = StubProvider([("README.md", 1)])
    verdict = narrate(build_client(stub)["client"], audit(mini_flota(tmp_path) / "alpha").vencidos)
    assert verdict.orden == [] and stub.calls == 0


def test_el_judge_exige_la_cita_en_cada_caso() -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    valida = run_evalset(dataset, judge_for(build_client(EcoProvider())["client"]), mode="full", threshold=1.0)
    assert (valida.total, valida.passed) == (3, 3)
    inventada = DocVerdict(summary="x", orden=[Arreglo(doc="README.md", linea=999, arreglo="y")])
    with pytest.raises(RuntimeError, match="inventadas"):
        verificar_citas(inventada, {("README.md", 9)})
