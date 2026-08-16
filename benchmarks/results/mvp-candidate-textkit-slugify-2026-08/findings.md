# Hallazgos — candidato MVP `textkit-slugify`

**Veredicto: `measurement_valid = true` / `candidate_passed = false`.**

Corrida única: sin repetir la corrida ni realizar reintentos manuales, sin
ajustar el goal, sin usar repair-center, sin corregir nada mientras el
proyecto corría. Los reintentos automáticos normales del orquestador sí
ocurrieron (tarea `22ec7032` intentos 1-3, subtarea `bc15f7f1` intentos
1-3 -- detalle en la adjudicación más abajo). El entorno estaba correcto,
el modelo respondió en las 12 llamadas (`agent_runs.outcome=artifact_delivered`
en todas), y el recorrido completo quedó registrado (`relevant-trace.json`).
El proyecto terminó `failed` y, aun mirando lo parcialmente entregado, no
califica como candidato aprobado.

## Checklist de aprobación (9 puntos — se necesitan los 9)

| # | Criterio | Resultado |
|---|---|---|
| 1 | Estado `completed` | ❌ `failed` — descalifica por sí solo |
| 2 | `unverified_completed_items` vacío | ✅[^2] `[]` (`project-report.json`) |
| 3 | `consistency.matches_git_history=true` | ✅ `true` (`export-summary.json`) |
| 4 | Patch aplica limpio | ✅ `git am` exit 0, sin conflictos |
| 5 | Árbol idéntico al `main` integrado | ✅ ambos `eb92017b5f126eadf8b592e1ca87d604c70d1b0a` |
| 6 | Suite previa+nueva en verde | ❌ sólo corrió 1/1 test (las 2 preexistentes fueron reemplazadas, no preservadas) y ese 1 test **erroró** |
| 7 | Test nuevo pasa post-fix | ❌ `NameError: name 're' is not defined` |
| 8 | Test nuevo falla pre-fix | ✅ `AssertionError: '-cafe-con-leche-' != 'cafe-con-leche'` |
| 9 | Origen intacto byte a byte | ✅ `source-hashes.txt` idéntico antes/después, HEAD sin mover |

5/9. El punto 1 ya cierra el veredicto; los puntos 6-7 son la razón de fondo.

[^2]: `unverified_completed_items=[]` da verde sólo según la definición
mecánica **actual** de esa red de seguridad (`services/delivery_report.py`):
confirma que el work item `completed` tiene un `Review` y un `TestReport`
persistidos y consistentes con los eventos -- **no** demuestra que el
candidato se haya ejecutado ni que sea funcionalmente correcto, como prueba
el punto 7 de esta misma tabla (el mismo work item que deja este punto en
verde es el que integró código con `NameError`). Esta insuficiencia -- que
la definición actual de "verificado" no requiera ejecución -- es
exactamente lo que Gate-MVP.2 existe para cerrar, no un error de esta
adjudicación.

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
- **`agent_runs.output_summary` truncado en almacenamiento a 1000
  caracteres.** No en `relevant-trace.json` (no incluye `agent_runs` a
  propósito, ver su `_note`); evidencia agregada, sin contenido completo,
  de las 12 filas de `agent_runs` de este proyecto — exactamente 5 quedan
  en `length=1000` con JSON inválido (`Unterminated string`), afectando no
  sólo a la tarea `22ec7032` sino también a la primera planificación
  (`technical_manager`) y al primer intento de la tarea 1:

  | run_id | work_item_id | length | json_valid |
  |---|---|---:|---|
  | `b9201b77-1198-4115-a80c-f7406b27d855` | *(planificación, sin work item)* | 1000 | false |
  | `07a9bd0f-5951-4ef9-b2ea-1600e57c3c9d` | `75b4c813-f124-42ac-bafb-db03df9433e5` | 1000 | false |
  | `4a7b613f-01e3-4762-84d6-262e3b8e607f` | `22ec7032-308e-4096-af5e-a562f4394798` | 1000 | false |
  | `f91457ff-1663-4682-8d32-ff5a84061351` | `22ec7032-308e-4096-af5e-a562f4394798` | 1000 | false |
  | `0d440277-aae5-4129-8e68-7e1b741527fe` | `22ec7032-308e-4096-af5e-a562f4394798` | 1000 | false |

  Las 7 filas restantes tienen `length` entre 554 y 930 y `json_valid=true`
  — el corte es un límite de longitud fijo, no un fallo esporádico. No
  impidió esta adjudicación (el mensaje de rechazo + el título del
  artifact + la tabla `artifacts` real alcanzan para reconstruir el
  candidato en cada caso), pero es una pérdida de fidelidad de diagnóstico
  real para cualquier lectura futura que necesite el contenido completo.
- **Gap de comunicación confirmado, en la dirección buena**: el DIRECTOR no
  sabe de la restricción de ejecución de proyectos importados (sin mención
  en `llm/prompts.py`), pero con el goal explícito no derivó criterios que
  dispararan `SCRIPT_EXECUTION` — la mitigación de redacción funcionó en
  este caso puntual.

Ningún hallazgo de esta lista fue corregido en el código — instrucción
explícita: sólo medir y documentar.
