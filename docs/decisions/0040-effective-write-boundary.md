# ADR 0040: Frontera efectiva de escritura del propio work item (Gate-MVP.1)

- Estado: aceptada
- Fecha: 2026-08-17

## Contexto

La corrida candidata a MVP `textkit-slugify`
(`benchmarks/results/mvp-candidate-textkit-slugify-2026-08/`, PR #27) encontró
que un work item con `expected_outputs=["textkit/slug.py"]` entregó también
`tests/test_slug.py` — fuera de su propio scope declarado. Nada lo impidió: el
`Artifact` resultante bloqueó permanentemente a la tarea hermana que sí tenía
ese archivo como su trabajo real, vía `Orchestrator._colliding_dependency_paths`
(`engine.py:2218-2272`), que nunca libera un reclamo mientras el work item
dueño no pase a `CANCELLED`.

Una adjudicación read-only posterior, aprobada, corrigió una primera lectura
del incidente: la causa no fue un interbloqueo contra la tarea de cierre
auto-generada por P0 (esa tarea nunca tuvo un `Artifact`, no puede ser
propietaria de nada) sino, exactamente, la ausencia de cualquier chequeo que
comparara el candidato de una tarea contra el scope que ella misma declaró.
Existían dos mecanismos de control de rutas y ninguno resolvía esto:

- El preflight de planificación (`_detect_owned_path_conflicts`/
  `_resolve_owned_path_conflicts`, `engine.py:2009-2115`) compara tareas
  **hermanas entre sí** antes de persistir el plan — no sirve cuando las
  hermanas declaran scopes disjuntos y el problema aparece recién en lo que
  se entrega, no en lo que se declara.
- `_colliding_dependency_paths` compara el candidato contra artefactos de
  **otras** tareas — nunca contra `item.expected_outputs`/`item.owned_paths`
  del propio item evaluado. Responde "¿pueden dos tareas relacionadas
  compartir un archivo?", no "¿esta tarea se salió de su propio scope?".

## Decisión

`Orchestrator._out_of_scope_paths(item, proposal)` (`engine.py`, junto a
`_colliding_dependency_paths`): función pura, sin `Repository` ni grafo de
dependencias. Calcula `merge_path_claims(item.owned_paths,
item.expected_outputs)` (`planning/contracts.py:49-58`, el mismo helper
conservador que ya resuelve "prosa vs. path" para el preflight de
planificación) y rechaza cualquier `file.path` del candidato que no esté en
ese conjunto, normalizando ambos lados con `strip().replace("\\",
"/").casefold()` antes de comparar (los paths originales, sin normalizar, se
conservan en el resultado y en el mensaje de error).

**Deliberadamente sin las excepciones de ancestro transitivo ni
`shared_component` que sí tiene `_colliding_dependency_paths`.** Esas
excepciones responden la pregunta cruzada ("¿pueden dos tareas relacionadas
compartir esto?"); acá la pregunta es sobre la tarea misma. Un caso legítimo
de escritura compartida (fragmento de un split, tarea que reescribe el
archivo de un ancestro) debe declarar ese path en su propio `owned_paths`/
`expected_outputs` — cosa que, para fragmentos de split, `_attempt_split`
(`engine.py:1513,1534-1536,1663`) ya hace mecánicamente. No se necesitó
ninguna heurística nueva para el problema de fondo ("`expected_outputs` es
casi siempre prosa, no un path literal") porque `merge_path_claims` ya lo
resolvía del lado cruzado.

`Orchestrator._reject_out_of_scope_write(item, proposal, correlation_id)`
envuelve el predicado: si hay paths ofensivos, emite el evento
`workspace_own_scope_rejected` (con `paths`, `candidate_paths`,
`claimed_paths`, `owned_paths`, `expected_outputs` en su metadata — distinto
de `workspace_action_rejected`, para que una adjudicación futura no necesite
releer código para distinguir "choqué con otra tarea" de "me salí de mi
propio scope") y lanza `InvalidPlan` liso, sin subclase nueva.

Tres puntos de integración, los tres **antes** de `isolation.prepare()`/
`workspace.stage()` — nunca dentro de `_evaluate_candidate`, que corre
después de que esos dos ya escribieron al worktree y que, en el camino
autónomo, vive fuera del único `except` que captura `InvalidPlan`
(confirmado leyendo `engine.py:508-519`: habría propagado sin capturar,
con riesgo real sobre el `asyncio.gather` de `run_project`):

- `_execute_work_item`: inmediatamente después de `_validate_work`, antes
  incluso de `_candidate_matches_artifact` y de `_colliding_dependency_paths`
  — la violación de scope propio es una causa intrínseca al candidato, más
  específica que un choque contra otra tarea, así que se evalúa primero.
- `re_evaluate_artifact`: primera línea del `try`, antes de `isolation.prepare`.
- `evaluate_operator_candidate`: primera línea del `try`, antes de
  `isolation.prepare`.

Ningún camino cambia su política de reintento/split existente:
`InvalidPlan` sigue entrando a `_candidate_failure_policy` (sin tocarla) en
el camino autónomo (`may_retry=True, may_split=True`, igual que una colisión
cruzada); los dos caminos manuales conservan su manejo ya existente
(`READY` mientras quede presupuesto, si no `FAILED`, nunca split).

## Garantía real

Un candidato nunca llega a materializarse en un worktree, ni produce un
`Artifact`, si alguno de sus archivos cae fuera del scope que su propio
work item declaró — en los tres caminos reales de evaluación, no sólo el
autónomo. Una tarea sin ningún claim parseable (el patrón dominante:
`expected_outputs` en prosa) no se ve afectada por este gate.

## Límites

**Sin ningún claim parseable, la frontera es permisiva — no una garantía
universal.** Si `item.owned_paths` está vacío y ningún `expected_outputs`
matchea `_PATH_CLAIM_PATTERN` (`planning/contracts.py:18`, exige una
extensión real), `_out_of_scope_paths` devuelve `set()` sin comparar nada.
Es una decisión consciente (ADR 0023 ya la tomó del lado cruzado): la
alternativa — exigir un claim literal siempre — rompería el patrón
dominante de tareas descritas en prosa, confirmado contra fixtures reales y
contra la tabla de traducción del proveedor mock antes de implementar esto.

**Es una frontera de coordinación entre tareas, no una sandbox de
seguridad.** No impide que un modelo escriba cualquier archivo dentro de lo
que ya tiene autorizado a nivel de proyecto (`authorized_files`, inerte hoy
por otras razones, ver P4.1-P4.5) — impide que una tarea, honesta pero
descuidada, dañe en silencio el trabajo de una tarea hermana excediendo lo
que ella misma declaró que iba a entregar. No debe presentarse en UI ni
documentación como aislamiento de escritura frente a contenido adversarial;
mismo espíritu de no sobreclamar que ya exige ADR 0034 para la frontera de
ejecución de proyectos importados.

La comparación sigue siendo textual/exacta tras normalizar separador y
mayúsculas — no resuelve dos paths distintos que apuntan al mismo archivo
por otra vía (symlinks, rutas relativas distintas fuera de backslash/case).
Mismo costo ya aceptado por ADR 0019 para `_colliding_dependency_paths`.

## Verificación

`backend/tests/test_evaluation_contracts.py` (10 tests nuevos: predicado
puro + wrapper) y `backend/tests/test_effective_write_boundary.py` (4 tests
end-to-end, uno por camino real más una regresión nombrada que reproduce el
incidente `textkit-slugify` y confirma que la tarea hermana deja de
bloquearse). Tres tests preexistentes necesitaron un ajuste, documentado en
el PR como evidencia de que el gate detecta exactamente la clase de
desajuste para la que se diseñó, no como indicio de mala calibración:

- `test_workspace_io_failures.py` (2 tests): `expected_outputs=["api.py"]`
  no coincidía con el `CANDIDATE_FILES` (`"library/api.py"`) que ya
  reutilizaban -- alineados a `expected_outputs=["library/api.py"]`.
- `test_expected_output_criteria.py`: el fallback de `MockProvider`
  producía `"deliverables/INFORME.md.md"` para
  `expected_outputs=["INFORME.md"]` -- el monkeypatch ya existente ahora
  también fija una respuesta `"work"` explícita que entrega literalmente
  `INFORME.md`.
- `test_task_splitting.py`: `MockProvider._workspace_file` traduce el
  label heredado sin sufijo de la tarea de consolidación
  (`"implementation_artifact"` → `"src/implementation.md"`) ignorando los
  `owned_paths` que la maquinaria de split realmente le asignó (unión de
  los `owned_paths` de sus hijas, ej. `"deliverables/implementation_artifact_part_1.md"`)
  -- el monkeypatch de esta prueba ahora hace que la consolidación mockeada
  entregue esos `owned_paths` reales en vez del path fijo de la tabla.
