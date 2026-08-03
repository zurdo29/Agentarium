# Informe de benchmark

- Corridas registradas: **27**
- Completadas y validadas: **3** (11.1%)
- Falsos `completed`: **4**

## Por modelo

| Objetivo | Corridas | Completadas | Tasa | Falsos | Segundos (media) |
|---|---|---|---|---|---|
| `ollama:qwen2.5-coder:7b` | 9 | 1 | 11.1% | 0 | 263.32 |
| `ollama:qwen3:4b` | 9 | 2 | 22.2% | 3 | 252.05 |
| `ollama:qwen3:8b` | 9 | 0 | 0.0% | 1 | 868.16 |

## Categorías de resultado

| Categoría | Corridas |
|---|---|
| `duplicate_candidate` | 9 |
| `technical_validation` | 7 |
| `completed` | 3 |
| `path_conflict` | 2 |
| `planning_contract` | 2 |
| `provider_failure` | 2 |
| `semantic_rejection` | 2 |

## Falsos `completed`

El orquestador dio la entrega por buena y los validadores del caso la rechazaron. Este número debe ser cero.

| Caso | Objetivo | Rep. | Validadores que fallaron |
|---|---|---|---|
| `csv_expenses_cli` | `ollama:qwen3:4b` | 1 | El programa procesa el CSV fixture y produce el resumen correcto |
| `csv_expenses_cli` | `ollama:qwen3:4b` | 2 | Existe documentación de uso en Markdown; La documentación explica cómo ejecutarlo |
| `csv_expenses_cli` | `ollama:qwen3:4b` | 3 | Existe documentación de uso en Markdown; La documentación explica cómo ejecutarlo; El programa procesa el CSV fixture y produce el resumen correcto |
| `architecture_document` | `ollama:qwen3:8b` | 2 | Hay una sección de decisiones de diseño |

## Detalle por corrida

| Caso | Objetivo | Rep. | Estado | Categoría | Validación | Intentos | Divisiones | Segundos |
|---|---|---|---|---|---|---|---|---|
| `architecture_document` | `ollama:qwen3:4b` | 1 | completed | `completed` | ok | 5 | 0 | 290.93 |
| `architecture_document` | `ollama:qwen3:4b` | 2 | failed | `path_conflict` | ok | 8 | 1 | 249.16 |
| `architecture_document` | `ollama:qwen3:4b` | 3 | completed | `completed` | ok | 5 | 0 | 204.37 |
| `csv_expenses_cli` | `ollama:qwen3:4b` | 1 | completed | `technical_validation` | falla | 4 | 0 | 173.82 |
| `csv_expenses_cli` | `ollama:qwen3:4b` | 2 | completed | `technical_validation` | falla | 4 | 0 | 182.48 |
| `csv_expenses_cli` | `ollama:qwen3:4b` | 3 | completed | `technical_validation` | falla | 5 | 0 | 195.74 |
| `library_api_sqlite` | `ollama:qwen3:4b` | 1 | failed | `technical_validation` | falla | 4 | 0 | 207.24 |
| `library_api_sqlite` | `ollama:qwen3:4b` | 2 | failed | `semantic_rejection` | ok | 14 | 1 | 615.64 |
| `library_api_sqlite` | `ollama:qwen3:4b` | 3 | failed | `duplicate_candidate` | falla | 4 | 0 | 149.11 |
| `architecture_document` | `ollama:qwen3:8b` | 1 | failed | `duplicate_candidate` | ok | 10 | 0 | 1011.08 |
| `architecture_document` | `ollama:qwen3:8b` | 2 | completed | `technical_validation` | falla | 4 | 0 | 785.76 |
| `architecture_document` | `ollama:qwen3:8b` | 3 | failed | `duplicate_candidate` | ok | 7 | 0 | 651.03 |
| `architecture_document` | `ollama:qwen2.5-coder:7b` | 1 | failed | `duplicate_candidate` | falla | 12 | 1 | 249.86 |
| `architecture_document` | `ollama:qwen2.5-coder:7b` | 2 | failed | `duplicate_candidate` | ok | 12 | 1 | 288.13 |
| `architecture_document` | `ollama:qwen2.5-coder:7b` | 3 | completed | `completed` | ok | 5 | 0 | 137.27 |
| `csv_expenses_cli` | `ollama:qwen3:8b` | 1 | failed | `technical_validation` | falla | 10 | 1 | 1063.45 |
| `csv_expenses_cli` | `ollama:qwen3:8b` | 2 | failed | `duplicate_candidate` | falla | 11 | 1 | 1823.85 |
| `csv_expenses_cli` | `ollama:qwen3:8b` | 3 | failed | `duplicate_candidate` | falla | 4 | 0 | 516.24 |
| `csv_expenses_cli` | `ollama:qwen2.5-coder:7b` | 1 | failed | `path_conflict` | falla | 32 | 2 | 499.19 |
| `csv_expenses_cli` | `ollama:qwen2.5-coder:7b` | 2 | failed | `duplicate_candidate` | falla | 10 | 0 | 233.03 |
| `csv_expenses_cli` | `ollama:qwen2.5-coder:7b` | 3 | planning | `planning_contract` | falla | 0 | 0 | 27.52 |
| `library_api_sqlite` | `ollama:qwen3:8b` | 1 | failed | `provider_failure` | falla | 12 | 0 | 973.43 |
| `library_api_sqlite` | `ollama:qwen3:8b` | 2 | planning | `planning_contract` | falla | 0 | 0 | 32.1 |
| `library_api_sqlite` | `ollama:qwen3:8b` | 3 | failed | `provider_failure` | falla | 24 | 0 | 956.47 |
| `library_api_sqlite` | `ollama:qwen2.5-coder:7b` | 1 | failed | `semantic_rejection` | falla | 20 | 1 | 487.59 |
| `library_api_sqlite` | `ollama:qwen2.5-coder:7b` | 2 | failed | `technical_validation` | falla | 7 | 0 | 218.21 |
| `library_api_sqlite` | `ollama:qwen2.5-coder:7b` | 3 | failed | `duplicate_candidate` | falla | 8 | 1 | 229.06 |
