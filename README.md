# doc-oracle

Lo que un repo afirma de sí mismo contra su oráculo (semana 24): las afirmaciones de los
documentos vivos —versión, semana, orden de consumidor, semilla de un módulo, campos del
span— se extraen por formas ancladas y se **miden** contra `pyproject`, el `CHANGELOG`, los
manifiestos y los ADRs. El estado sale de la medición, nunca de una opinión; el único LLM
redacta el arreglo, y si cita una línea que no se midió la narración se anula. Es el
**segundo consumidor de `diff-engine`**: muestra, línea a línea, lo que habría que cambiar.

Nació de un hallazgo al arrancar la semana: el README del core anunciaba "Semana 13" y su
`ARCHITECTURE.md` sembraba `vector-core` en la semana 16. La metodología de la sem. 14 ya
contrasta cifras de contrato contra su oráculo, pero solo en los READMEs de una lista de
consumidores: nadie miraba los documentos del propio core, ni las versiones, ni las semanas.

## Demo

```bash
uv sync
uv run doc-oracle scan            # default: ../llm-dev-core · sale 1 si hay vencidas
# {
#   "repo": "llm-dev-core", "documentos": 20, "afirmaciones": 26,
#   "por_estado": {"ok": 15, "vencido": 6, "planificado": 5, "sin_oraculo": 0},
#   "vencidos": [
#     {"tipo": "version", "doc": "README.md", "linea": 9, "dice": "v1.0", "mide": "v1.4.0", …},
#     {"tipo": "version", "doc": "ARCHITECTURE.md", "linea": 3, "dice": "v0.4", "mide": "v1.4.0", …},
#     {"tipo": "seed", "doc": "ARCHITECTURE.md", "linea": 189, "sujeto": "vector-core",
#      "dice": "sem. 16", "mide": "sem. 23", …},
#     …
#   ],
#   "hunks": [
#     {"doc": "README.md", "op": "cambiada", "a_start": 9,
#      "lineas_a": ["- **Estado:** v1.0 · Semana 13 (hito: 12 consumidores, contratos estables)"],
#      "lineas_b": ["- **Estado:** v1.4.0 · Semana 23 (hito: 12 consumidores, contratos estables)"]},
#     …
#   ]
# }

uv run doc-oracle narrate         # único LLM: valida contra doc-verdict-v1 o falla
# {"summary": "Cinco afirmaciones vencidas en dos documentos: …",
#  "orden": [
#    {"doc": "README.md", "linea": 9,
#     "arreglo": "- **Estado:** v1.4.0 · Semana 23 (hito: 12 consumidores, contratos estables)"},
#    …
#  ]}
```

Salida del 2026-11-23, antes de arreglar el core: es una foto, no una cifra que se mantenga.
El resto de la flota salió en orden salvo `commit-cli`, cuyo README declara core 0.1.0
mientras su manifiesto dice 0.3.0. El scan es **determinista** (mismo árbol, mismo veredicto);
el arreglo es el único LLM.

## Cómo funciona

```
doc-oracle
  ├─ parser-io: cada documento vivo (README, ARCHITECTURE, docs/*.md) leído en secciones
  │    └─ el CHANGELOG no se audita: es oráculo — una entrada fechada es una foto del pasado
  ├─ formas ancladas → afirmaciones, cada una contra su oráculo
  │    └─ pyproject · CHANGELOG · manifiestos de la flota · ADRs · Span.model_fields
  ├─ diff-engine: hunks entre lo que el documento dice y lo que debería decir (no se aplican)
  └─ arreglo LLM validado por doc-verdict-v1, sobre líneas saneadas
       └─ la cita invalida: una línea no medida, omitida o repetida anula la narración
```

| Afirmación | Forma anclada | Oráculo |
|---|---|---|
| `version` | etiqueta de versión o de estado seguida de un número | `pyproject.toml`: `[project] version` |
| `semana_estado` | etiqueta de estado que dice la semana actual | semana de la entrada más reciente del `CHANGELOG.md` |
| `semana_repo` | la semana entre paréntesis en el arranque del README, o la etiqueta de semana | `core-consumer.yml`: `week` |
| `core_version` | etiqueta del core seguida de la versión | `core-consumer.yml`: `core_version` |
| `orden_consumidor` | el lugar de un repo entre los consumidores de un módulo, predicado del repo o dado por hecho en un `[x]` | lugar por semana de manifiesto |
| `seed` | un módulo del árbol con su semana de siembra | el ADR del módulo; sin paquete, `planificado` o `vencido` |
| `span_campos` | la cuenta de campos del span | `len(Span.model_fields)` |

Cuatro reglas definen qué es afirmación:

- **Forma anclada, no cualquier número.** Puertos, fechas, ejemplos entre comillas y cifras
  sin oráculo (tests, archivos) no se auditan: el baseline del core ya los clasifica aparte.
- **Un bloque de código es transcripción.** Dentro de una valla solo se audita el árbol de
  módulos; una salida capturada en una demo no afirma nada sobre el repo.
- **Meta no es afirmación.** `- [ ] Segundo consumidor de …` y "queda para el segundo
  consumidor" hablan del futuro; solo cuentan lo predicado (`es el …`) y lo dado por hecho
  (`[x] …`). Importa porque la regla del tercer uso (D12) se decide con esas frases.
- **Sin oráculo no hay veredicto.** `sin_oraculo` se reporta como tal; nunca cuenta como `ok`.

Un corte de línea no esconde una afirmación: "Es el" al final de una línea y el ordinal en
la siguiente se lee como una sola frase. Las tres reglas de arriba salieron de auditar este
mismo README con la herramienta: su demo y su tabla dieron positivo antes de existir.

Los cuatro estados: `ok`, `vencido` (el documento miente sobre el presente), `planificado`
(una siembra cuya semana no llegó) y `sin_oraculo`.

## Recicla de

| Módulo | Uso |
|---|---|
| `diff-engine` | segundo consumidor: hunks línea a línea entre lo dicho y lo medido — se muestran, no se aplican (ADR-10) |
| `parser-io` | cada documento vivo leído en secciones; cada afirmación viaja con la suya |
| `llm-client` | la llamada del arreglo (retry + reparación + span) |
| `schema-validate` | el arreglo valida contra `doc-verdict-v1` o falla |
| `secure-base` | las líneas que entran al arreglo van saneadas |
| `test-kit` | dataset congelado + replay determinista (D4) |
| `cache-ratelimit` | caché TTL + token bucket alrededor del proveedor |

## Limitaciones

- Solo siete formas. Lo que se escribe distinto no se detecta: en la extracción no hay LLM,
  a propósito — un extractor que opina no se puede repetir.
- Cada forma se busca dentro de un párrafo: un párrafo en medio la parte.
- No corre suites ni lee git: los oráculos son archivos y el modelo instalado.
- La semana actual es la del último release del CHANGELOG. Una semana sin release no mueve
  ese oráculo, y una semana saltada tampoco.
- Un paquete que no existe no tiene número que proponer: no hay hunk, y el arreglo es la
  decisión a tomar, no un reemplazo.
- Solo se audita lo que habla del presente. "En la semana 13, con 12 consumidores…" es
  historia y pasa sin que nadie lo mire.
- Sin persistencia ni estado entre corridas: no avisa cuando algo *empieza* a estar vencido.

## Roadmap

- [x] Siete formas con oráculo, hunks y arreglo validado (sem. 24)
- [x] Segundo consumidor de `diff-engine`: el patrón se repite y D12 toma nota
- [ ] Pase recurrente sobre la flota con `bot-base`: digest del día en que un repo empieza a mentir
- [ ] Afirmaciones de la flota en el `CHANGELOG` ("N-ésimo consumidor: …") con oráculo de tag
