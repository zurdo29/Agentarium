# Entorno de medición — `mvp-candidate-textkit-slugify-2026-08`

Corrida única del flujo candidato a MVP (import → run → export → verificar)
sobre un proyecto importado, siguiendo el diseño acordado tras el cierre de
P4 (P4.1-P4.5). No es una matriz: es una sola combinación caso×modelo,
corrida una vez, sin repetirse.

## Identidad congelada

| Campo | Valor |
|---|---|
| Commit de Agentarium medido | `14ad053d6bf24938f60ba27c7350fbbc744118ae` (`main`) |
| Árbol de trabajo | limpio (sólo `.claude/launch.json` sin trackear, ajeno al backend/frontend) |
| Python | 3.14.6 |
| Plataforma | Windows-10-10.0.19044-SP0 |
| Proveedor / modelo | `ollama` / `qwen2.5-coder:7b` |
| Digest del modelo | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| Versión de Ollama | 0.32.5 |
| Concurrencia | `model_concurrency: 1` |
| `agentarium doctor` previo a la corrida | 14 ok, 1 advertencia (`openai_compatible` inactivo, no bloquea), 0 errores |
| Base de datos | `sqlite:///C:/Users/Renzo/Desktop/Agentarium/runtime/agentarium.db` (la misma que usa cualquier invocación normal de la CLI, sin aislar — una sola corrida no lo necesita, mismo criterio que P3.0) |
| `project_id` | `f685b363-da54-4280-99cb-e2ca22b354b4` |
| `imported_commit` | `f3e6ba583a3688398fc7a339681f2767fa40de39` |
| Goal (persistido) | sha256 `eb6b09a0571e99485875bfad9dd2a6376659e6645d940e27330c361291b48d71` — verificado byte a byte contra el texto exacto acordado, tanto vía CLI (con `PYTHONUTF8=1`) como por consulta directa a la base |

## Qué se corrió

Repo fixture `textkit-slugify`: librería mínima de una función (`slugify`),
100% stdlib (`re`, `unicodedata`), con un bug real y acotado (no recorta
guiones al borde del resultado) y una suite `unittest` preexistente con 2
casos que pasan. Construido con 4 commits reales (3 de contenido + 1 de
limpieza de `__pycache__` encontrada durante la construcción), fuera del
`workspace_root` de Agentarium, con el mismo patrón `_init_git_repo`/
`_commit_all` que ya usan los tests de P4.1.

Goal de importación (texto exacto, redactado para evitar los tokens que
disparan `SCRIPT_EXECUTION` en `ValidationProfileExecutor._script_execution_requested`
-- "python", "script", "linea de comandos", "command line", "terminal",
"consola" -- y para primar al modelo hacia una entrega verificable por
lectura de archivo):

> El módulo `textkit/slug.py` contiene una función `slugify` que convierte
> texto libre en un identificador separado por guiones. Hoy, cuando el
> texto empieza o termina con caracteres no alfanuméricos, el resultado
> conserva guiones extra: por ejemplo, `' Café con Leche!! '` produce
> `'-cafe-con-leche-'` en lugar de `'cafe-con-leche'`. Corrige ese
> comportamiento para que el resultado nunca tenga guiones al inicio ni al
> final. Agrega un caso nuevo en `tests/test_slug.py`, dentro de la clase
> existente y siguiendo su estilo, que cubra exactamente ese ejemplo. No
> modifiques archivos fuera de `textkit/slug.py` y `tests/test_slug.py` ni
> agregues dependencias externas. La evaluación interna de este proyecto
> importado será estática: revisará el contenido entregado sin ejecutarlo.

Comando:

```
agentarium project import <source> "<goal de arriba>" -t "textkit-slugify"
agentarium project run f685b363-da54-4280-99cb-e2ca22b354b4
```

Una sola corrida. No se reintentó, no se ajustó el goal, no se usó
repair-center, no se corrigió nada del hallazgo mientras el proyecto seguía
`RUNNING`. Duración real de la ejecución (`project_run_started` →
`project_failed`): 21:00:59.317 → 21:02:24.690 (≈1m45s), 12 llamadas al
modelo, las 12 con `outcome=artifact_delivered` en `agent_runs` (el modelo
respondió en todas; ningún fallo de proveedor).

## Adjudicación de la causa

Ver `findings.md` para la adjudicación read-only completa de la colisión de
ownership, hecha releyendo `Orchestrator._colliding_dependency_paths`
(`backend/agentarium/orchestration/engine.py:2218-2272`) contra
`relevant-trace.json` en vez de inferir la causa por lectura superficial de
los mensajes de evento.
