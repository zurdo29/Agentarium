# ADR 0029: Preflight estático de imports antes de tester/revisor

- Estado: aceptada
- Fecha: 2026-08-05

## Contexto

ADR 0020 agregó una frase de prompt diciéndole al worker que un `.py`
ejecutado corre sin red ni instalación de paquetes, sólo biblioteca
estándar — verificado en vivo, el modelo volvió a importar Flask en el
intento siguiente. ADR 0028 (P2.1) dio el siguiente paso: los mismos
hechos como **datos** estructurados (`RuntimeCapabilityManifest`,
`runtime_capabilities` en el payload de `plan`/`plan_revision`/`work`),
dejando explícitamente sin probar si eso solo cambia algo. Cita textual de
esa ADR: *"Tampoco prueba que aplicar una consecuencia mecánica ayude —
eso es P2.2."*

Esta ADR es esa consecuencia mecánica. `PLANS.md` describía P2.2 como
"preflight de imports/comandos + `unsupported_capability`" — al
implementarlo se decidió acotar el alcance sólo a imports (ver "Alcance:
sólo imports, no comandos" más abajo) y se descubrió, en el camino, que
cortar antes de TESTER rompía el único canal existente que informaba a un
reintento qué había fallado antes. Ambos puntos se documentan aquí.

## Decisión

### Perfil de validación `IMPORT_PREFLIGHT`

Nuevo miembro de `ValidationProfile` (`backend/agentarium/execution/validation.py`),
estático, sin subprocess — primer uso del módulo `ast` en este repo. Por
cada archivo `.py` entregado:

```python
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        roots = [alias.name.split(".", 1)[0] for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level > 0 or node.module is None:
            continue  # import relativo: parte de la propia entrega
        roots = [node.module.split(".", 1)[0]]
    ...
```

Cada raíz se compara contra `sys.stdlib_module_names` (estable desde
Python 3.10; `requires-python = ">=3.11"` en `pyproject.toml`, sin guard
de versión necesario) ∪ `RuntimeCapabilityManifest.third_party_packages_allowed`
∪ los nombres de módulo de cualquier otro `.py` bajo el mismo `project_root`
(exención de "nombre local" — sin ella, una tarea `consolidation`/`patch`
que importe un módulo entregado por una tarea hermana ya integrada en un
intento anterior generaría un falso positivo).

**`ast.walk` completo, no sólo el nivel superior del módulo — corrección
sobre la propuesta inicial de esta misma sesión.** La primera versión
recorría sólo `tree.body` (los hijos directos del `Module`), razonando que
eso evitaba falsos positivos en el patrón `try: import ujson as json /
except ImportError: import json`. Corregido: si la política dice que
Flask no está permitido, no debe aparecer aunque el import esté detrás de
un guard de fallback o dentro de una función — dejarlo pasar habría sido
exactamente el mismo problema que ADR 0020 ya documentó (una restricción
que no se aplica de verdad), sólo que escondida detrás de una excepción en
vez de ignorada en un prompt. La única exención por "es del propio código"
que se mantiene es la de imports relativos (`node.level > 0`), porque ésos
son parte de la propia entrega, no una dependencia externa — categoría
distinta de un guard de fallback.

**Manejo de errores:** `ast.parse` puede lanzar `SyntaxError`; leer el
archivo puede fallar. En ambos casos el preflight no propaga la excepción
— la traga y no reporta ese archivo. `PYTHON_SYNTAX` (perfil ya existente,
incondicional para todo `.py`) es quien tiene la responsabilidad de
reportar un archivo inválido; `IMPORT_PREFLIGHT` no debe duplicar ese
reporte ni, mucho menos, tumbar `validate()` entero por un archivo que ya
está roto por otra razón.

Cuando falla, `SCRIPT_EXECUTION` para ese script se salta — ejecutarlo de
verdad sólo repetiría el mismo fallo unos milisegundos después, esta vez
como un `ModuleNotFoundError` real, que es exactamente la tardanza que el
criterio de salida de PLANS.md prohíbe.

### `VALIDATION_CONTRACT_VERSION` sube a `profiles-v7`

**Corrección sobre la propuesta inicial de esta sesión.** Se había
asumido que, como el shape de `ValidationProfileResult.as_evidence()` no
cambia (mismas claves, sólo un valor nuevo posible para `"profile"`), no
hacía falta bump. Incorrecto: ADR 0016 ya fija el precedente real al
agregar `SCRIPT_EXECUTION` — cita textual, *"`VALIDATION_CONTRACT_VERSION`
avanza a `profiles-v6` siguiendo el mismo precedente de ADR 0014, para que
reintentos en curso no hereden como requisito acumulado un fallo producido
por la activación de un perfil nuevo"*. El criterio es la activación de un
perfil de validación nuevo, no si cambió la forma del diccionario.
`IMPORT_PREFLIGHT` es exactamente ese caso, así que sube a `profiles-v7`.

### Corte antes de TESTER y CRITICAL_REVIEWER

Hoy, aunque la validación técnica ya haya fallado por cualquier motivo,
TESTER y CRITICAL_REVIEWER se llaman igual — `_apply_technical_review_gate`
sólo normaliza el veredicto del revisor *después* de que la llamada al
modelo ya ocurrió. Ese comportamiento amplio no se toca en esta PR (sería
alcance mayor, no evaluado aquí). Se agrega un corte nuevo y específico:
si `IMPORT_PREFLIGHT` falló, `Orchestrator._evaluate_candidate` llama a
`_reject_unsupported_capability` y retorna antes de invocar a TESTER,
reusando la escalera de reintento existente (`_request_changes`:
`CHANGES_REQUESTED` → `READY` si quedan intentos, si no
`_on_attempts_exhausted`, mismo camino de split que cualquier otro
agotamiento — no se inventa un estado terminal nuevo).

### El canal de contexto de reintento que había que reparar

Cortar antes de TESTER tiene una consecuencia que no era obvia hasta
investigar el código: el único canal real que hoy informa a un reintento
qué falló en el intento anterior
(`retry_guidance.prior_validation_failures`/`cumulative_validation_requirements`
en `ContextBuilder.operational()`) se alimenta de `TestReport`, y un
`TestReport` sólo se crea si TESTER fue llamado (requiere `tester_run_id`
de un `AgentRun` real). Si el corte nuevo salta TESTER, ese `TestReport`
nunca se crea para ese intento — el reintento volvería a ser tan ciego
como en la era pre-P2.1, exactamente lo que esta PR existe para evitar.

Resuelto con un canal independiente, basado en eventos:
`_reject_unsupported_capability` emite un evento
`unsupported_capability_detected` con `metadata.rejected_imports`. Nuevo
método de repositorio, `list_events_for_work_item(project_id, work_item_id,
limit=250)` (`EventRow.work_item_id` ya estaba indexado, pero
`list_events` sólo filtraba por `project_id`). `ContextBuilder.operational()`
lo lee para el mismo work item a través de **todos** los intentos previos
(no sólo el último) y arma `retry_guidance.cumulative_rejected_imports`,
deduplicado, tope 20 — mismo patrón que `cumulative_validation_requirements`,
canal complementario, no un reemplazo (`item.last_error` sigue llevando
sólo el motivo del intento más reciente, como prosa libre, no como lista
acumulada).

**Orden `DESC` + `reversed`, no `ASC` + `LIMIT` — corrección sobre la
propuesta inicial de esta sesión.** La primera versión de
`list_events_for_work_item` ordenaba `ORDER BY sequence ASC LIMIT`, lo que
conserva los eventos más **antiguos** una vez que un work item supera el
límite — exactamente al revés de lo útil para contexto de reintento, que
necesita ver qué pasó más recientemente. Corregido a `ORDER BY sequence
DESC LIMIT`, revertido en Python antes de devolver, conservando la cola en
vez de la cabeza.

Un método nuevo, no filtrar `list_events(project_id)` en Python dentro de
`ContextBuilder` (el patrón que sí usan `list_reviews`/`list_test_reports`
hoy): `operational()` corre en cada intento de cada work item — hot path
— y traer todos los eventos del proyecto para descartar la mayoría no
escala igual que filtrar `reviews`/`reports`, de volumen mucho menor.

`WORKSPACE_PROMPT_VERSION` sube a `workspace-v11` (ADR 0003: el payload de
`work` gana una clave nueva, cambio de contrato aunque no se toque una
sola frase de instrucción — mismo razonamiento que ADR 0028).

### Taxonomía: firma nueva, no reuso de la existente

`FailureCategory.UNSUPPORTED_CAPABILITY` ya existía en
`backend/agentarium/benchmarks/taxonomy.py`, usada post-hoc para
clasificar un `ModuleNotFoundError` real ya ocurrido en runtime
(`_ERROR_SIGNATURES` incluye `"modulenotfounderror"`, `"no module named"`,
`"importerror"`). El mensaje que arma `_reject_unsupported_capability` no
reusa esas firmas — agrega una propia, `"import no permitido"`, ordenada
antes de las genéricas: decir `ModuleNotFoundError` sería falso cuando el
script nunca llegó a ejecutarse.

### Alcance: sólo imports, no comandos

PLANS.md nombraba el ítem "preflight de imports/comandos". Esta PR cubre
sólo imports. Detectar "comandos" (llamadas `subprocess`/`os.system`/CLI
dentro del código entregado) se decidió explícitamente fuera de alcance:
`executables_allowed` (ADR 0028) describe qué invoca **el propio pipeline
de Agentarium** contra una entrega (el intérprete que corre o chequea el
`.py`/`.js`), no un límite sobre lo que un `subprocess` del script
entregado podría hacer — ese límite no existe en ningún nivel hoy, y
tratar "comandos permitidos para código entregado" como una extensión de
`executables_allowed` habría confundido dos conceptos que ADR 0028 ya
separó a propósito. Definir una lista de política nueva y distinta para
esto, con detección estática inherentemente floja (cualquier string
armado en runtime la esquiva), queda como su propio ítem futuro, sin
fecha — `PLANS.md` se actualiza para que el encabezado ya no lea como si
P2.2 lo hubiera cerrado.

## Consecuencias

- El caso de benchmark `library_api_sqlite` ya no puede llegar tarde a un
  `ModuleNotFoundError`: un import fuera de `third_party_packages_allowed`
  se detecta antes de gastar TESTER y CRITICAL_REVIEWER, con un mensaje
  que nombra el módulo y por qué se rechazó.
- Sigue sin haber garantía de que el worker deje de intentar el mismo
  import en el siguiente intento — `cumulative_rejected_imports` le da al
  reintento la información para no repetirlo, pero, igual que ADR 0028 ya
  advirtió, ningún dato estructurado por sí solo garantiza que el modelo
  lo respete. Medirlo con una corrida real queda para una sesión futura,
  no para esta PR.
- Detección estática vía AST no cubre imports dinámicos
  (`importlib.import_module`, `__import__`) ni nombres armados en tiempo
  de ejecución — límite aceptado a propósito, misma lógica que
  `network_policy`: una consecuencia mecánica donde es barato aplicarla,
  no una garantía exhaustiva.
- La brecha "declarado vs. aplicado" en `SCRIPT_EXECUTION` (ADR 0028: ni
  `third_party_packages_allowed` ni `network_policy` se fuerzan
  mecánicamente en el camino real) sigue sin resolver — sigue sin ser
  parte de este ítem.
- Preflight de "comandos" queda pendiente, sin fecha, como su propio ítem.
