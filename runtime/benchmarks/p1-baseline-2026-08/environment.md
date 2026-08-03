# Entorno de medición — suite `p1-baseline-2026-08`

Este documento fija, en prosa legible, bajo qué entorno se midió el baseline
de P1.2. Es la contraparte humana de `runtime_identity` (ver ADR 0021/P1.2a en
`PLANS.md`): no reemplaza al ledger ni a la base de datos de la corrida —
ninguno de los dos se versiona — pero deja el resultado auditable desde el
repositorio sin depender de artefactos locales que no se comparten.

## Identidad congelada

| Campo | Valor |
|---|---|
| Suite | `p1-baseline-2026-08` |
| Commit de Agentarium | `2b4ecb2aa9e0bb2257f392702190a3938ce6817d` |
| Árbol de trabajo | limpio (`agentarium_dirty: false`) |
| Python | 3.14.6 |
| Plataforma | Windows-AMD64 |
| Concurrencia | 1 (por invocación de `benchmark run`; tareas independientes dentro de un mismo proyecto sí corren en paralelo — ver P1.3, punto del timeout) |
| Versión de Ollama | 0.32.5 |

La identidad se congeló una vez por invocación de `benchmark run`, y la
versión/digest de Ollama se revalidó antes de cada corrida (P1.2a). Ninguna
de las dos tandas (9 + 18 corridas) disparó `SuiteDrift`.

## Digests de los modelos medidos

| Objetivo | Digest |
|---|---|
| `ollama:qwen3:4b` | `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` |
| `ollama:qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| `ollama:qwen2.5-coder:7b` | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |

Los digests se comparan por `(proveedor, modelo)`. Si cualquiera de estos
tres tags se vuelve a hacer `pull` con pesas distintas, una reanudación de
esta misma suite fallaría con `SuiteDrift` en vez de mezclar resultados de
dos modelos distintos bajo el mismo nombre.

## Aislamiento usado durante la medición

```powershell
$env:AGENTARIUM_DATABASE_URL = "sqlite:///runtime/benchmarks/p1-baseline-2026-08/agentarium.db"
$env:AGENTARIUM_WORKSPACE_ROOT = "C:\Users\Renzo\agbench\workspaces"
```

La base de datos (`agentarium.db`) y los workspaces de cada corrida quedan
**fuera de control de versiones** deliberadamente: son estado de ejecución
local, potencialmente grandes, y no aportan nada que este archivo más
`report.md`/`report.json` no capturen ya para efectos de auditoría. Quien
necesite inspeccionar una corrida puntual (artefactos entregados, work items,
test reports, reviews) reproduce el entorno de arriba y consulta esa base
localmente — no se recrea automáticamente a partir de lo versionado.

## Qué se corrió

27 corridas: 3 casos (`architecture_document`, `csv_expenses_cli`,
`library_api_sqlite`) × 3 modelos × 3 repeticiones, en dos tandas (9 + 18).
Resultado completo, categorías y los 4 falsos `completed` en `report.md` /
`report.json` de este mismo directorio, y el análisis de causas en la
sección "P1.2 — la matriz" de `PLANS.md`.
