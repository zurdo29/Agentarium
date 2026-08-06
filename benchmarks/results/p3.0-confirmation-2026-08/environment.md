# Entorno de medición — suite `p3.0-confirmation-2026-08`

Contraparte humana de `runtime_identity` para la corrida única de P3.0
(confirmación real dirigida de P2, ver `PLANS.md`). No es una matriz: es
una sola combinación caso×modelo×repetición, corrida una vez, sin volver a
lanzarse.

`report.md`/`report.json` de este mismo directorio son la proyección
agregada que `agentarium benchmark report` calculó leyendo el ledger. El
ledger (`runtime/benchmarks/p3.0-confirmation-2026-08/ledger.jsonl`) y la
base de datos que registró el detalle de esta corrida
(`runtime/agentarium.db`, la misma que usa cualquier invocación normal de
la CLI — no se aisló con `AGENTARIUM_DATABASE_URL`/`AGENTARIUM_WORKSPACE_ROOT`
como sí hizo P1, porque una sola corrida no necesita esa separación) quedan
sin versionar, igual que en P1. El detalle usado para escribir
`findings.md` — eventos de `execution_events`, `agent_runs`,
`artifacts` — sólo existe ahí.

## Identidad congelada

| Campo | Valor |
|---|---|
| Suite | `p3.0-confirmation-2026-08` |
| Commit de Agentarium | `61746397bea9f747e7109a6c64e469c483081c88` (`main`, cierre de P2.2, PR #11) |
| Árbol de trabajo | limpio (`agentarium_dirty: false`) — ver nota abajo |
| Python | 3.14.6 |
| Plataforma | Windows-AMD64 |
| Concurrencia | `model_concurrency: 1` |
| Versión de Ollama | 0.32.5 |
| Digest `ollama:qwen2.5-coder:7b` | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |

**Nota sobre "árbol limpio":** al empezar esta sesión, `PLANS.md` tenía
cambios sin commitear en el working tree (la propia actualización de
roadmap que documenta el cierre de P2 y da paso a P3.0). `assert_clean()`
del runner rechaza medir sobre un árbol sucio, así que ese cambio se
guardó con `git stash` antes de congelar la identidad y se restauró
después de la corrida — el commit medido (`6174639`) es exactamente el de
`main`/`origin/main`, sin modificar durante la ejecución.

## Qué se corrió

Una corrida: `library_api_sqlite` × `ollama:qwen2.5-coder:7b` × repetición 1.
Caso elegido porque, históricamente (ver `p1-baseline-2026-08`), el modelo
tendió a elegir Flask para este caso — oportunidad natural de observar P2
sin fabricar un fallo artificial. Comando:

```powershell
agentarium benchmark run --case library_api_sqlite `
  --model ollama:qwen2.5-coder:7b --repetitions 1 `
  --suite p3.0-confirmation-2026-08
```

No se repitió la corrida. No se cambió ningún `timeout_seconds` como
consecuencia de esta medición.
