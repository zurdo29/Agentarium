# Entorno de medición — suite `p1-baseline-2026-08`

Este documento fija, en prosa legible, bajo qué entorno se midió el baseline
de P1.2. Es la contraparte humana de `runtime_identity` (ver ADR 0021/P1.2a en
`PLANS.md`): no reemplaza al ledger ni a la base de datos de la corrida —
ninguno de los dos se versiona — pero deja el resultado auditable desde el
repositorio sin depender de artefactos locales que no se comparten.

Este archivo, `report.md`, `report.json` y `findings.md` viven en
`benchmarks/results/<suite>/` — versionados sin necesitar excepciones de
`.gitignore`, junto a `benchmarks/cases/` y `benchmarks/fixtures/`. El
ledger y `agentarium.db` de la corrida siguen en
`runtime/benchmarks/<suite>/`, sin versionar: son estado de ejecución local
que la CLI regenera, no un registro que deba sobrevivir en el repositorio.

## Identidad congelada

| Campo | Valor |
|---|---|
| Suite | `p1-baseline-2026-08` |
| Commit de Agentarium | `2b4ecb2aa9e0bb2257f392702190a3938ce6817d` |
| Árbol de trabajo | limpio (`agentarium_dirty: false`) |
| Python | 3.14.6 |
| Plataforma | Windows-AMD64 |
| Concurrencia | `model_concurrency: 1` — un solo semáforo global para llamadas al modelo (`ResourceScheduler`); varias tareas independientes del mismo proyecto pueden quedar listas a la vez y encolarse detrás de esa única llamada activa. Relevante para P1.3a: el timeout actual envuelve la espera en cola además de la generación, y hoy no se puede separar una de otra |
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
Resultado completo tal como lo calculó la maquinaria (incluye 4 falsos
`completed` señalados) en `report.md` / `report.json` de este mismo
directorio. La adjudicación manual de esos 4 (3 confirmados, 1 falso
negativo del validador) y el diagnóstico de causas están en `findings.md`,
también aquí. Resumen y hoja de ruta en la sección "P1.2 — la matriz" de
`PLANS.md`.
