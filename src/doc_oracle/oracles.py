"""Los oráculos: de dónde sale lo medido. Archivos y código del repo, nunca opinión."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

# La primera entrada del CHANGELOG que declare semana — la más reciente — dice en
# qué semana está el repo.
ENTADA = re.compile(r"^##\s+v[\w.\-+]*.*?\bsemana\s+(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class Consumidor:
    name: str
    week: int
    modules: tuple[str, ...]


class Flota:
    """Los `core-consumer.yml` de un directorio de proyectos: el oráculo de toda la flota."""

    def __init__(self, projects_dir: Path) -> None:
        self.consumidores: list[Consumidor] = []
        for manifest in sorted(Path(projects_dir).glob("*/core-consumer.yml")):
            data = yaml.safe_load(manifest.read_text()) or {}
            week, name, modules = data.get("week"), data.get("name"), data.get("reused_modules") or []
            if isinstance(week, int) and isinstance(name, str):
                self.consumidores.append(Consumidor(name, week, tuple(str(m) for m in modules)))
        self.consumidores.sort(key=lambda c: (c.week, c.name))

    def por_nombre(self, repo: str) -> Consumidor | None:
        return next((c for c in self.consumidores if c.name == repo), None)

    def modulos(self) -> frozenset[str]:
        return frozenset(m for c in self.consumidores for m in c.modules)

    def posicion(self, repo: str, modulo: str | None = None) -> int | None:
        """Lugar del repo (1-based) entre los consumidores — de `modulo`, o del core — por semana."""
        grupo = [c.name for c in self.consumidores if modulo is None or modulo in c.modules]
        return grupo.index(repo) + 1 if repo in grupo else None


def version(repo: Path) -> str | None:
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        declarada = tomllib.loads(pyproject.read_text()).get("project", {}).get("version")
    except tomllib.TOMLDecodeError:
        return None
    return str(declarada) if declarada else None


def semana_actual(repo: Path) -> int | None:
    changelog = repo / "CHANGELOG.md"
    if not changelog.is_file():
        return None
    match = ENTADA.search(changelog.read_text())
    return int(match.group(1)) if match else None


class Contexto:
    """Los hechos del repo auditado y de su flota. Se leen una vez por corrida."""

    def __init__(self, repo: Path, flota: Flota) -> None:
        self.repo = repo
        self.flota = flota

    @cached_property
    def version(self) -> str | None:
        return version(self.repo)

    @cached_property
    def semana_actual(self) -> int | None:
        return semana_actual(self.repo)

    @cached_property
    def manifiesto(self) -> dict:
        manifest = self.repo / "core-consumer.yml"
        data = yaml.safe_load(manifest.read_text()) if manifest.is_file() else None
        return data if isinstance(data, dict) else {}

    @cached_property
    def vocabulario(self) -> frozenset[str]:
        """Los módulos que la flota conoce: lo que declaran los manifiestos y lo que el repo empaqueta."""
        paquetes = {p.name for p in (self.repo / "packages").glob("*") if (p / "pyproject.toml").is_file()}
        return self.flota.modulos() | paquetes

    @cached_property
    def campos_span(self) -> int:
        from llm_client import Span

        return len(Span.model_fields)

    def tiene_paquete(self, modulo: str) -> bool:
        return (self.repo / "packages" / modulo / "pyproject.toml").is_file()

    def semana_de_siembra(self, modulo: str) -> int | None:
        """La semana que declara el ADR del módulo (`Implementada en: semana N (`packages/x`…`).

        Se busca por contenido, no por nombre de archivo: el ADR de `test-kit` se llama
        `test-kit-dataset-contract` y un patrón de nombre lo dejaría sin oráculo.
        """
        patron = re.compile(rf"Implementada en:\*\*\s*semana\s+(\d+)\s*\(`packages/{re.escape(modulo)}`")
        for adr in sorted((self.repo / "docs" / "decisions").glob("*.md")):
            match = patron.search(adr.read_text())
            if match:
                return int(match.group(1))
        return None
