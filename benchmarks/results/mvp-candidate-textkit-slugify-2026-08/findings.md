# Hallazgos — candidato MVP `textkit-slugify`

**Veredicto: `measurement_valid = true` / `candidate_passed = false`.**

Corrida única, sin reintentos, sin ajustar el goal, sin usar repair-center,
sin corregir nada mientras el proyecto corría. El entorno estaba correcto,
el modelo respondió en las 12 llamadas (`agent_runs.outcome=artifact_delivered`
en todas), y el recorrido completo quedó registrado (`relevant-trace.json`).
El proyecto terminó `failed` y, aun mirando lo parcialmente entregado, no
califica como candidato aprobado.

## Checklist de aprobación (9 puntos — se necesitan los 9)

| # | Criterio | Resultado |
|---|---|---|
| 1 | Estado `completed` | ❌ `failed` — descalifica por sí solo |
| 2 | `unverified_completed_items` vacío | ✅ `[]` (`project-report.json`) |
| 3 | `consistency.matches_git_history=true` | ✅ `true` (`export-summary.json`) |
| 4 | Patch aplica limpio | ✅ `git am` exit 0, sin conflictos |
| 5 | Árbol idéntico al `main` integrado | ✅ ambos `eb92017b5f126eadf8b592e1ca87d604c70d1b0a` |
| 6 | Suite previa+nueva en verde | ❌ sólo corrió 1/1 test (las 2 preexistentes fueron reemplazadas, no preservadas) y ese 1 test **erroró** |
| 7 | Test nuevo pasa post-fix | ❌ `NameError: name 're' is not defined` |
| 8 | Test nuevo falla pre-fix | ✅ `AssertionError: '-cafe-con-leche-' != 'cafe-con-leche'` |
| 9 | Origen intacto byte a byte | ✅ `source-hashes.txt` idéntico antes/después, HEAD sin mover |

5/9. El punto 1 ya cierra el veredicto; los puntos 6-7 son la razón de fondo.

---

## Adjudicación read-only de la colisión de ownership

**Corrección de un hallazgo previo.** La primera lectura de esta corrida
atribuyó las 6 colisiones "Workspace file paths collide..." a un
interbloqueo contra la tarea de cierre auto-generada por P0
(`2ce2d980-bd41-4257-936e-222bce031c9e`, "Completar y verificar la entrega
del proyecto"). Esa atribución se hizo leyendo el mensaje de evento y el
scope declarado de las tareas, **sin releer el código real de
`Orchestrator._colliding_dependency_paths`**
(`backend/agentarium/orchestration/engine.py:2218-2272`) ni consultar la
tabla `artifacts`. Releído el código exacto y re-trazado contra
`relevant-trace.json`, la atribución era incorrecta. Se corrige acá.

### Semántica exacta del código (verificada, no inferida)

`_colliding_dependency_paths(item, proposal)`:

1. `owners` se construye iterando **únicamente** `repository.list_artifacts(project_id)`
   (`engine.py:2234`) — un `SELECT * FROM artifacts WHERE project_id=?` sin
   ningún filtro de estado (`repositories/repository.py:474-480`). **No**
   consulta `owned_paths` ni `expected_outputs` de ningún work item para
   construir `owners` — sólo archivos que aparecen en un `Artifact` ya
   persistido.
2. Se excluye un artifact si `artifact.work_item_id` es el propio `item.id`
   o está en `_transitive_dependency_ids(item)` (`engine.py:2223,2235-2236`)
   — dependencias reales, vía la tabla `dependencies`, no por scope
   declarado.
3. Se excluye un artifact si su work item está `CANCELLED`
   (`engine.py:2228-2232,2237-2241`) — **ningún otro estado excluye**,
   ni `BLOCKED` ni `COMPLETED` liberan el reclamo.
4. Para cada archivo del candidato evaluado, si su path coincide con un
   `owners[path]` no excluido, colisiona — salvo la excepción de
   `shared_component` compartido con `output_strategy` no exclusivo
   (`engine.py:2264-2270`), que nunca aplicó acá porque las 3 tareas
   involucradas en cada colisión tenían `shared_component: null` o no
   coincidente.
5. El mensaje de error lista exactamente `colliding_paths`, ordenado
   (`engine.py:446-448`) — no la lista completa de archivos del candidato.

### Los hechos verificados contra esa semántica

- **Un solo `Artifact` existe en todo el proyecto**: `dedcb040-e743-4fb5-ba5f-f192bb640f1f`,
  `work_item_id=75b4c813-f124-42ac-bafb-db03df9433e5` (tarea 1, "Modificar la
  función `slugify` en `textkit/slug.py`"), creado `2026-08-15 21:01:15.458017`,
  con `content.files[].path = ["textkit/slug.py", "tests/test_slug.py"]`
  (`relevant-trace.json` → `artifacts[0]`). La tarea 1 declaraba
  `expected_outputs_json: ["textkit/slug.py"]` y `owned_paths_json: []` — el
  segundo archivo (`tests/test_slug.py`) está **fuera de su scope
  declarado**, pero el candidato lo incluyó igual y la materialización
  (`workspace_files_materialized`, evento 4188) lo aceptó sin restringirlo
  al `expected_outputs` de la tarea.
- **La tarea de cierre (`2ce2d980`) nunca tuvo un `Artifact`.** `attempt_count=0`
  durante toda la corrida (quedó `blocked` de punta a punta, nunca corrió un
  candidato). No aparece como `work_item_id` en ninguna fila de `artifacts`.
  Por el punto 1 de la semántica exacta, una tarea sin `Artifact` **no puede
  aparecer en `owners` bajo ninguna circunstancia** — no importa qué declare
  en `owned_paths`/`expected_outputs` (que además, en este caso, ni siquiera
  contienen los paths como strings literales: `expected_outputs` de la tarea
  de cierre son descripciones en prosa, no rutas). La tarea de cierre **no
  pudo haber sido la propietaria** de ninguna colisión.
- **La tabla `dependencies` confirma que la tarea 1 y la tarea 2 son
  hermanas, sin relación de dependencia en ningún sentido** (`relevant-trace.json`
  → `dependencies`): la única fila con `work_item_id=22ec7032...` no existe
  (0 filas) — la tarea 2 no depende de nada. La única dependencia de la
  tarea 1 es que la tarea de cierre depende *de ella* (`work_item_id=2ce2d980,
  depends_on_id=75b4c813`), no al revés. Por lo tanto `_transitive_dependency_ids`
  de la tarea 2 (y de su subtarea `bc15f7f1`) es el conjunto vacío: la tarea
  1 nunca queda excluida como "dependencia".

### Las 6 colisiones, una por una

| # | Evento | work_item rechazado | estado en ese momento | paths colisionados (= mensaje exacto) | owner real (`artifact_id=dedcb040...`) | relación |
|---|---|---|---|---|---|---|
| 1 | `seq 4192`, 21:01:23.071 | `22ec7032` "Agregar caso de prueba en `tests/test_slug.py`", intento 1 | `running` | `tests/test_slug.py`, `textkit/slug.py` | `75b4c813` "Modificar la función `slugify`...", estado `awaiting_review` | hermana (sin dependencia) |
| 2 | `seq 4202`, 21:01:51.718 | `22ec7032`, intento 2 | `running` | `tests/test_slug.py` | `75b4c813`, estado **`completed`** | hermana |
| 3 | `seq 4207`, 21:01:57.370 | `22ec7032`, intento 3 | `running` | `tests/test_slug.py`, `textkit/slug.py` | `75b4c813`, estado `completed` | hermana |
| 4 | `seq 4215`, 21:02:15.484 | `bc15f7f1` "[subtarea] Escribir el caso de prueba", intento 1 (split de `22ec7032`) | `running` | `tests/test_slug.py` | `75b4c813`, estado `completed` | hermana (split, sin dependencia con `75b4c813`) |
| 5 | `seq 4220`, 21:02:20.082 | `bc15f7f1`, intento 2 | `running` | `tests/test_slug.py` | `75b4c813`, estado `completed` | hermana |
| 6 | `seq 4225`, 21:02:24.679 | `bc15f7f1`, intento 3 | `running` | `tests/test_slug.py` | `75b4c813`, estado `completed` | hermana |

En las 6, `2ce2d980` (tarea de cierre) tenía 0 `Artifact` — no pudo ser la
propietaria en ninguna. El owner real fue, siempre, la tarea 1, vía el
único `Artifact` del proyecto, nunca excluido porque nunca pasó a
`CANCELLED`.

### Causa raíz corregida

No fue un interbloqueo contra la tarea de cierre. Fue que **la tarea 1
entregó un candidato fuera de su scope efectivo** (tocó `tests/test_slug.py`
sin que ese archivo estuviera en su `expected_outputs`), ese candidato se
materializó y se integró sin que nada restringiera la escritura a su scope
declarado, y el `Artifact` resultante — que persiste indefinidamente, sin
ningún mecanismo que libere o reconcilie el reclamo una vez conocida la
intención real de la tarea 2 — bloqueó de forma permanente a la tarea
hermana cuyo trabajo real era ese mismo archivo. La tarea de cierre es un
efecto secundario del bloqueo (nunca pudo correr porque sus dependencias
—tarea 1 y la cadena de split de la tarea 2— nunca terminaron limpio), no
la causa.

**Esto no suaviza el veredicto**: el candidato sigue `failed`, por esta
causa exacta. Pero cambia dónde hay que mirar para una eventual corrección
(ver `PLANS.md` → Gate-MVP.1): la frontera de escritura efectiva de un
work item, no la lógica de la tarea de cierre.

---

## Segundo hallazgo (sin cambios): `completed` no fue confiable

Independiente de la colisión de arriba. El diff real integrado
(`relevant-trace.json` → `test_reports[0].command_evidence_json` →
`isolated_change_set.diff`) muestra que el worker reescribió `textkit/slug.py`
sin `import re` (usado en la nueva implementación) ni `import unicodedata`,
y reemplazó la clase de test existente por una nueva con un solo caso,
borrando `test_basic_lowercase` y `test_strips_accents` — violando la
instrucción explícita del goal ("dentro de la clase existente y siguiendo
su estilo").

El `critical_reviewer` aprobó (`reviews[0].verdict = "approved"`) afirmando
*"La función `slugify` procesa correctamente texto..."* — falso, el código
no ejecuta. El `tester` marcó `passed: true` con seis checks
(`test_reports[0].checks_json`), ninguno ejecuta el código entregado
(`checksums`, `aislamiento`, `validation_profiles`, `model_assessment` =
*"Evaluación explicativa del tester local"*). `PYTHON_SYNTAX` sólo hace
`compile()`, que no detecta un `NameError` — sólo errores de sintaxis.

Confirmado con evidencia dura (`verification.md`): `git am changes.patch`
sobre un clon limpio del original + `python -m unittest discover -s tests -v`
→ `ERROR`, no `FAIL`.

---

## Hallazgos secundarios (documentados, no corregidos)

- **Encoding Windows en stdout redirigido**: goal/salidas se ven con `�`
  sin `PYTHONUTF8=1`; el dato persistido en la base es correcto byte a
  byte (verificado por hash). Mismo ítem ya anotado en el backlog de
  `PLANS.md` para `benchmark run`, ahora confirmado también en
  `project import`/`report`.
- **`agent_runs.output_summary` truncado en almacenamiento**: al menos 3
  filas para `22ec7032` quedaron con JSON cortado a mitad de string
  (`Unterminated string`) — no impidió esta adjudicación porque el mensaje
  de rechazo y el título del artifact alcanzan para reconstruir el
  candidato, pero es una pérdida de fidelidad de diagnóstico real.
- **Gap de comunicación confirmado, en la dirección buena**: el DIRECTOR no
  sabe de la restricción de ejecución de proyectos importados (sin mención
  en `llm/prompts.py`), pero con el goal explícito no derivó criterios que
  dispararan `SCRIPT_EXECUTION` — la mitigación de redacción funcionó en
  este caso puntual.

Ningún hallazgo de esta lista fue corregido en el código — instrucción
explícita: sólo medir y documentar.
