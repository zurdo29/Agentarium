# ADR 0041: Verificación honesta -- estática vs. ejecutada (Gate-MVP.2)

- Estado: aceptada
- Fecha: 2026-08-18

## Contexto

La corrida candidata a MVP `textkit-slugify`
(`benchmarks/results/mvp-candidate-textkit-slugify-2026-08/`, PR #27) mostró
un work item de un proyecto importado que llegó a `completed` con un
`NameError` real (el worker reescribió `slugify` y se olvidó `import re`) y
que además borró 2 tests preexistentes (`SlugifyTests.test_basic_lowercase`,
`SlugifyTests.test_strips_accents`) reemplazándolos por 1 nuevo dentro de
una clase renombrada. El `critical_reviewer` (LLM) aprobó afirmando que el
código "procesa correctamente" -- falso: nunca se ejecutó nada, porque
P3.4/ADR 0034 bloquea `SCRIPT_EXECUTION` para proyectos importados (y eso
**no se toca ni se afloja acá**).

Dos huecos mecánicos independientes, ninguno solo hubiera cubierto el
incidente completo:

1. Ningún perfil de validación no-ejecutante detecta un nombre no definido.
   `PYTHON_SYNTAX` sólo hace `compile()` (nunca `exec()`); `IMPORT_PREFLIGHT`
   sólo verifica que los imports **declarados** resuelvan, nunca detecta un
   nombre usado sin ningún import que lo traiga.
2. Ni `TestReport` ni el reviewer tenían ninguna señal que distinguiera
   evidencia de ejecución real de evidencia sólo estática, ni contexto sobre
   qué existía antes del candidato. `_apply_technical_review_gate` es
   unidireccional: fuerza rechazo si `report_passed=False`, pero si
   `report_passed=True` no hace nada -- confía por completo en el veredicto
   semántico del reviewer.

## Decisión

Un único PR, dos incrementos lógicos.

**Incremento A -- verdad estructural de la evidencia.** Campo nuevo,
requerido y sin default, `TestReport.verification_mode: static_only |
executed`. Calculado por el Orchestrator, nunca por el LLM tester (vive
fuera de `checks`, donde el tester escribe libremente sin enum ni
unicidad de `name` -- meterlo ahí habría dejado abierto el mismo lavado de
una afirmación falsa que este campo existe para cerrar):

```python
verification_mode = (
    VerificationMode.EXECUTED
    if any(
        check.get("profile") == ValidationProfile.SCRIPT_EXECUTION.value
        and check.get("started") is True
        for check in validation_checks
    )
    else VerificationMode.STATIC_ONLY
)
```

**No se deriva de `passed`.** Un diseño inicial que exigía además
`check["passed"]` era incorrecto: un proceso que arrancó y terminó con
exit code distinto de cero produjo igual evidencia ejecutada. La señal real
es `CommandResult.started: bool` (ejecutor), requerido y sin default --
`True` únicamente cuando `SafeCommandExecutor` llegó a lanzar un
subproceso real, sin importar el resultado; `False` para todo resultado
sintético, bloqueo de autoridad, comando rechazado o timeout nunca
alcanzado. `report.passed` conserva su significado independiente: un
reporte puede ser `verification_mode=executed` y `passed=false` a la vez
(confirmado con un caso real y no importado que ejecuta y falla,
`test_honest_verification.py`). No se agrega `externally_verified` en este
PR: todavía no existe un mecanismo que ingiera y conserve evidencia
externa.

`unverified_completed_items` gana un `reason` estructurado:
`missing_review_or_test_report` (red de integridad de datos, caso legacy
no alcanzable por ningún camino real) o `static_only_verification` (caso
nuevo y esperado para un proyecto importado). `COMPLETED` sigue
significando que el workflow terminó; nunca por sí solo "se demostró que
funciona". Propagado a `delivery_report.py`, `export_summary.py`
(markdown: "sólo estática" / "ejecutada: ok" / "ejecutada: falla", nunca
"ok" para evidencia estática) y a `TaskDrawer`/`DeliveryReportView`
(tercera cláusula visible: "código ejecutado" / "código no ejecutado").

**Incremento B -- red estática y contexto base-vs-candidato.** Perfil
no-ejecutante nuevo `PYTHON_UNDEFINED_NAMES`, mismo patrón que
`PYTHON_SYNTAX`, vía subprocess:

```
python -m ruff check --isolated --no-cache --select F821 <target>
```

En `NON_EXECUTING_PROFILES` (corre siempre, incluso con
`allow_project_code_execution=False`). `--isolated` no es cosmético: sin
él, un `ruff.toml`/`pyproject.toml` propio del repo importado
(`ignore = ["F821"]`) desactivaría exactamente el chequeo que este gate
existe para garantizar -- verificado con un test que coloca ese archivo
dentro del árbol validado. Ruff pasa de `[project.optional-dependencies].dev`
a `[project].dependencies`: se invoca en operación normal, no sólo en
desarrollo. Activar el perfil bumpea `VALIDATION_CONTRACT_VERSION`
(`profiles-v7` -> `profiles-v8`), mismo precedente que ADR 0029.

`GitWorktreeIsolation.read_base_file(changes, path) -> str | None` lee
`git show {changes.commit}^:{path}` contra el worktree del candidato, que
sigue vivo durante toda la evaluación (`discard()` corre en un `finally`
que envuelve tester+reviewer en los 3 caminos reales). `None` si el archivo
es nuevo. `Orchestrator._removed_top_level_definitions(base, candidate) ->
list[str]` compara AST: `def`/`async def`/`class` de nivel de módulo, y
dentro de cada clase de ese nivel, también sus métodos directos como
`"Clase.metodo"` -- necesario porque en el incidente real la clase completa
fue renombrada, así que un walk de sólo `tree.body` habría visto que
`SlugifyTests` desapareció pero no que sus dos métodos también. Un nombre
que sobrevive nunca se reporta, aunque su cuerpo haya sido reescrito por
completo -- este mecanismo detecta eliminación, no cambios de
comportamiento. Persistido como una entrada determinista por archivo `.py`
entregado en `TestReport.command_evidence` (incluso vacía -- así el rastro
de evidencia distingue "revisado, nada eliminado" de "nunca revisado") y
enviado como mapa completo `removed_top_level_names` en las dos rutas de
`_review_payload` (revisión inicial y focalizada por criterio). No es un
gate automático: el prompt del reviewer exige que justifique explícitamente
cualquier lista no vacía, pero un refactor legítimo puede seguir
aprobándose.

**Visible, no sólo persistido.** Persistir sin exponer habría repetido la
misma clase de problema que la nota [^2] de `findings.md` ya corrigió sobre
`unverified_completed_items`: un dato que no llega a los ojos de quien
decide es, en la práctica, invisible. `export_summary.removed_top_level_names()`
extrae las entradas por `check == "removed_top_level_names"` (no por la
presencia de `path`, que `materialized_file_checksum` también trae), entra
en `summary.json` por item entregado y se rinde en `summary.md` como
subsección propia -- no una columna más de la tabla, donde una lista de
nombres sería ilegible. `DeliveryReportView` y `TaskDrawer` la muestran en
el detalle del tester. En los tres lugares se muestran **sólo las listas no
vacías**: el caso normal es que no se eliminó nada, y una sección siempre
presente y casi siempre vacía entrena a ignorarla.

Por la misma razón, `OUTCOME_HINT.completed` ("Completada con evidencia
verificada") dejó de aplicarse a ciegas: para un item `completed` con
`verification_mode=static_only` -- el caso normal de un proyecto importado
-- ese texto era exactamente el sobreclaim que este gate existe para
evitar. Ahora pasa por `outcomeHint()`, que devuelve "Completada con
verificación estática; el código no se ejecutó". Y la advertencia de
`unverified_completed_items` muestra el `reason` de cada item en vez de
unir títulos: `static_only_verification` -> "código no ejecutado" (estado
honesto y esperado de un proyecto importado) frente a
`missing_review_or_test_report` -> "sin review o informe técnico" (problema
real de integridad de datos). Dos hechos estructuralmente distintos que no
deben leerse igual.

`WORKSPACE_PROMPT_VERSION` sólo aplica a `operation=="work"`
(`llm/prompts.py`); review/test usaban un literal `"artifact-v1"` fijo.
Corrección sobre el diseño inicial: se extrajo ese literal a
`ARTIFACT_PROMPT_VERSION = "artifact-v2"` y se bumpeó esa constante, no
`WORKSPACE_PROMPT_VERSION` (que nunca se habría aplicado a este cambio).

**Congelamiento en el benchmark.** Bumpear la constante no alcanzaba:
`prompt_versions()` (`benchmarks/runner.py`) sólo declaraba
`planning`/`workspace`/`decompose`/`plan_revision`, así que el prompt de
tester/reviewer era el único que una corrida usaba sin declarar -- un
cambio de su contrato no producía `SuiteDrift` y dos baselines
incomparables podían mezclarse en un mismo informe. `ARTIFACT_PROMPT_VERSION`
se exporta desde `agentarium.llm` y entra como clave `"artifact"`.
Consecuencia real, declarada y no maquillada: `assert_comparable` compara
la unión de ambos conjuntos de claves, así que **todo registro de ledger
anterior a esta decisión ahora produce drift** (la clave está ausente, no
sólo distinta). Es exactamente la semántica buscada -- esas corridas
usaron otro contrato de reviewer -- y es inocuo para la evidencia
versionada: los ledgers locales viven bajo `runtime/`, gitignorado, y los
snapshots de `benchmarks/results/` son inmutables y nadie los vuelve a
appendear. PLANS.md ya exigía suite nueva para una matriz nueva.

## Hallazgos durante la implementación

**`--output-format=concise` quedaba bloqueado por la propia política de
seguridad.** El deny-token `format` de `configs/policies/security.yaml`
usa límites de palabra que tratan `-` y `=` como frontera (no forman parte
de `[0-9A-Za-z_]`), así que `--output-format=concise` matcheaba `format`
como token independiente y `SafeCommandExecutor` rechazaba el comando
completo con `CommandRejected` -- `PYTHON_UNDEFINED_NAMES` habría fallado
siempre, para cualquier archivo `.py`, sin ejecutar nunca `ruff` de verdad.
Encontrado por un test que corría el comando real, no por inspección de
código. Corregido quitando esa flag (formato de salida por defecto de
`ruff`, sin efecto sobre `return_code`, que es lo único que decide
`passed`) -- no se tocó la política de seguridad ni se agregó una excepción
para este caso.

**Renombre `attempted` -> `started`.** El campo se implementó primero como
`CommandResult.attempted`; una corrección posterior lo renombró a
`started` en todo el código (mismo significado: el proceso llegó a
arrancar, sin importar el resultado).

## Comportamiento para proyectos importados

`allow_project_code_execution=not project.imported` sigue exactamente
igual; `SCRIPT_EXECUTION` sigue bloqueado fail-closed para proyectos
importados, sin excepciones nuevas. `PYTHON_UNDEFINED_NAMES` corre igual
para proyectos importados que para cualquier otro -- no está gateado por
`imported`. `verification_mode` es estructuralmente siempre `static_only`
para un work item de un proyecto importado, porque `SCRIPT_EXECUTION`
nunca llega a arrancar ahí -- la honestidad no es "un item ya no puede
llegar a `completed`", sino que ese hecho ya no puede quedar oculto. Los
mecanismos corren universalmente, no sólo para proyectos importados:
consecuencia esperada y documentada, no oculta, es que `PYTHON_UNDEFINED_NAMES`
puede rechazar por primera vez candidatos de proyectos **no importados**
que hoy pasaban con un `NameError` latente.

## Límites

- No detecta una función/método que **conserva su nombre** pero cuya
  lógica fue reescrita o degradada por completo -- exactamente lo que le
  pasó a `slugify`; sigue dependiendo por completo del juicio del reviewer
  LLM para ese caso, sin señal mecánica nueva.
- `removed_top_level_names` no es un gate automático -- un candidato puede
  seguir aprobándose aunque la lista no esté vacía, si el reviewer lo
  justifica o simplemente lo ignora.
- No cubre el borrado completo de un archivo preexistente que el candidato
  simplemente deja de entregar, ni JS/TS -- mismo alcance ya establecido
  por `PYTHON_SYNTAX`/`IMPORT_PREFLIGHT`.
- F821 vía ruff no cubre nombres armados dinámicamente (`globals()`,
  `exec`, metaprogramación) ni import estrella.
- P3.4/ADR 0034 sin cambios: no es aislamiento de proceso real: los
  perfiles no-ejecutantes (incluido el nuevo) siguen siendo procesos reales
  del intérprete/`ruff` corriendo código fijo de Agentarium, nunca la
  lógica del archivo entregado.

## Verificación

`test_validation_profiles.py` (F821 positivo/negativo, `--isolated` contra
un `ruff.toml` que intenta ignorarlo, el perfil corriendo con
`allow_project_code_execution=False`, `started` sobre éxito/exit
distinto de cero/timeout/autoridad bloqueada/comando rechazado/entrypoint
ausente); `test_safe_commands.py` (`started` bajo timeout real);
`test_schema_migration.py` (migración+backfill legacy a `static_only`,
round-trip de ambos modos); `test_git_worktree_isolation.py`
(`read_base_file` con archivo preexistente y archivo nuevo);
`test_evaluation_contracts.py` (`_removed_top_level_definitions`: archivo
nuevo, contenido idéntico, cuerpo reescrito con mismo nombre, función y
`async def` eliminadas, método eliminado dentro de clase que sobrevive,
clase renombrada con sus métodos); `test_honest_verification.py` (archivo
nuevo): reconstrucción byte a byte del incidente real vía
`evaluate_operator_candidate` contra un proyecto importado -- F821 falla,
`report.passed=false`, `verification_mode=static_only`,
`removed_top_level_names` contiene los 3 nombres reales del incidente,
presentes en cada payload del reviewer que efectivamente se disparó; más
un control negativo (mismo flujo sin el bug, nada dispara) y un caso no
importado que ejecuta y falla (`executed`, no `static_only` -- prueba
directa de que el modo no depende de `passed`), y el cruce de que la misma
evidencia persistida es la que el export puede leer, sin volver a derivar
el diff.

Benchmark: `test_benchmarks.py` cubre las dos formas del drift de
`artifact` -- versión distinta y clave **ausente** (la forma real de todo
registro anterior a esta decisión, que `assert_comparable` detecta porque
compara la unión de claves); `test_benchmark_identity.py` fija el conjunto
exacto de prompts que una corrida declara, para que un prompt que la suite
ejercita no vuelva a quedar sin declarar.

Delivery/export/UI: `test_delivery_report.py` (item `completed`
`static_only` entra en `unverified_completed_items` con su razón),
`test_export_summary.py` (las 3 ramas de texto del tester, la extracción
que ignora `materialized_file_checksum` aunque también traiga `path`, y el
markdown que renderiza la subsección con archivo y nombres pero **no** la
renderiza cuando nada se eliminó), `test_export_project_service.py`,
`tests/home-delivery-report.test.mjs` y `tests/home-work-items.test.mjs`
(la tercera cláusula visible en ambos componentes; los borrados visibles
con archivo y nombres, con el archivo de lista vacía ausente; el bloque
entero ausente cuando no se eliminó nada; cada razón de
`unverified_completed_items` con su texto propio; y que el hint del outcome
no diga "evidencia verificada" para un item `static_only`).
`test_type_contract.py` confirma que no hay drift backend↔TypeScript --
los dos campos nuevos de `command_evidence` (`path?`, `removed?`) son
opcionales, que es lo que ese gate exige para un campo TS que no toda
respuesta real trae. `ruff`, `mypy` y `.\test.ps1` completos en verde.
