# Entorno de medición — `gate-mvp3-textkit-slugify-2026-08`

Gate-MVP.3: **una sola repetición** del mismo caso `textkit-slugify` medido
en `mvp-candidate-textkit-slugify-2026-08`, con el mismo modelo, goal y
condiciones, contra el Agentarium ya corregido por Gate-MVP.1 (ADR 0040) y
Gate-MVP.2 (ADR 0041). No es una matriz ni una implementación: es una
medición.

## Identidad congelada

Registrada **antes** de importar y de correr.

| Campo | Valor | ¿Igual que la corrida original? |
|---|---|---|
| Commit de Agentarium medido | `6c50d58aa9c382b3a33929779ba14b3ea1b81c91` (`main`) | **no** — antes `14ad053`; es justamente lo que se mide |
| Árbol de trabajo | limpio (sólo `.claude/launch.json` sin trackear, ajeno al backend/frontend) | sí |
| Python | 3.14.6 | sí |
| Plataforma | Windows-10-10.0.19044-SP0 | sí |
| Proveedor / modelo | `ollama` / `qwen2.5-coder:7b` | sí |
| Digest del modelo | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` | sí — verificado por `/api/tags`, coincidencia exacta |
| Versión de Ollama | 0.32.5 | sí |
| Concurrencia | `model_concurrency: 1` | sí |
| `agentarium doctor` previo | 14 ok, 1 advertencia (`openai_compatible` inactivo, no bloquea), 0 errores | sí |
| Base de datos | `sqlite:///<AGENTARIUM_ROOT>/runtime/agentarium.db` (sin aislar, mismo criterio que P3.0 y que la corrida original) | sí |
| `project_id` | `531ea073-b183-4e07-b1e6-23bb0472a657` | nuevo, por definición |
| `imported_commit` | `f3e6ba583a3688398fc7a339681f2767fa40de39` | sí |
| Goal (persistido) | sha256 `eb6b09a0571e99485875bfad9dd2a6376659e6645d940e27330c361291b48d71`, 777 caracteres | sí — byte a byte |

Ollama estaba apagado al empezar; se levantó (`ollama serve`) y se repitió
`doctor` antes de crear el proyecto, según la autorización explícita para
ese caso. Ningún otro elemento de identidad se tocó.

**Sin deriva de identidad.** Modelo, digest, proveedor, versión de Ollama y
concurrencia son idénticos a la corrida original. La única variable que
cambió a propósito es el commit de Agentarium — que es exactamente la
hipótesis que Gate-MVP.3 pone a prueba.

## Fixture verificado antes de medir

Se reutilizó el repositorio fixture original preservado, **sin
reconstruirlo**. Cinco condiciones verificadas antes de importar:

| Verificación | Resultado |
|---|---|
| `HEAD` | `f3e6ba583a3688398fc7a339681f2767fa40de39` ✅ |
| Árbol Git | limpio (`git status --porcelain` vacío) ✅ |
| Hashes de los 5 archivos trackeados | idénticos a `source-hashes.txt` ✅ |
| Suite original | `Ran 2 tests — OK` ✅ |
| Bug original | reproducido: `slugify('  Cafe con Leche!!  ')` → `'-cafe-con-leche-'` ✅ |

## Qué se corrió

Mismo goal exacto que la corrida original (777 caracteres, sha256
`eb6b09a0…b48d71`), verificado en el archivo de origen y releído desde la
columna `projects.goal` de la base después de importar.

```
agentarium project import <SOURCE_FIXTURE> "<goal exacto>" -t "textkit-slugify"
agentarium project run 531ea073-b183-4e07-b1e6-23bb0472a657
```

Una sola corrida. Desde `project_run_started` no se ejecutó otra corrida,
no se hizo retry/rework/recover/candidate manual, no se usó Repair Center,
no se modificó goal, fixture, modelo ni configuración, y no se corrigió
ningún hallazgo. Los reintentos y splits automáticos normales del
orquestador sí ocurrieron (2 `task_split_created`, 1
`task_split_depth_exhausted`).

Ventana real: `project_run_started` 20:40:54.696 → `project_failed`
20:44:29.522 (≈3m35s). 28 llamadas al modelo, las 28 con
`outcome=artifact_delivered` (ningún fallo de proveedor). Roles: 1
director, 3 technical_manager, 16 implementation_worker, 3 tester, 5
critical_reviewer.

Estado final del proyecto: **`failed`**, progreso 11.11%.

## Nota de normalización

En los `.json` de este directorio se reemplazaron rutas absolutas locales
por placeholders — `<AGENTARIUM_ROOT>`, `<WORKSPACE_ROOT>`,
`<SOURCE_FIXTURE>`, `<EXPORT_DIR>`, `<SCRATCH>` — para no versionar la ruta
de usuario/máquina ni el GUID de sesión del scratchpad. Ningún hash,
commit, ID, timestamp, contenido técnico ni resultado fue alterado; el
reemplazo se hizo con un script que revalida que cada archivo siga siendo
JSON bien formado. `changes.patch` no se tocó en absoluto.

No se versiona: `runtime/agentarium.db`, `workspaces/` completos, los
clones de verificación, `changes.bundle` (binario) ni logs temporales.
Quedan en el scratchpad local de esta sesión.
