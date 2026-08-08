# ADR 0034: Frontera de autoridad para proyectos importados (P3.4)

- Estado: aceptada
- Fecha: 2026-08-07

## Contexto

PLANS.md P3.4: `SCRIPT_EXECUTION` controla el contrato de ejecución pero
no es un sandbox de sistema operativo; `network_policy: deny` es política
declarada, no aplicada. El criterio de cierre exige que importar un
repositorio (P4.1, todavía no implementado) no le dé al código generado
autoridad implícita para ejecutar fuera de la frontera aprobada.

## Decisión

`Project` gana un campo `imported: bool = False` (`domain/models.py`),
persistido vía una nueva `MigrationStep` (`repositories/migrations.py`,
generalizada para apuntar a tablas distintas de `work_items`).

La frontera vive dentro de `ValidationProfileExecutor`, no a nivel de
work item completo. `NON_EXECUTING_PROFILES` declara explícitamente los
perfiles que nunca ejecutan el contenido entregado
(`WORKSPACE_INVENTORY`, `PYTHON_SYNTAX`, `IMPORT_PREFLIGHT`,
`JSON_SYNTAX`, `JAVASCRIPT_SYNTAX`, `WEB_APPLICATION`). `_execute()` —el
único método que lanza un subproceso real dentro de esa clase— rechaza
fail-closed cualquier perfil que no esté en esa lista cuando
`allow_project_code_execution` es falso: un perfil nuevo que se agregue
más adelante queda bloqueado por defecto hasta que se declare
explícitamente no-ejecutor, en vez de heredar acceso por omisión.
`Orchestrator._evaluate_candidate` pasa
`allow_project_code_execution=not project.imported` a `validate()`, y si
el resultado trae un perfil marcado `blocked_by_authority` (marca
estructural, nunca detección de texto), la tarea termina directo en
`FAILED` sin pasar por tester/revisor y sin reintento ni división —un
proyecto importado nunca puede resolver ese bloqueo generando un
candidato distinto. Esta marca tiene precedencia fija sobre cualquier
otro rechazo del mismo candidato (por ejemplo `IMPORT_PREFLIGHT`): la
autoridad es una propiedad permanente del proyecto, no un defecto que un
candidato distinto podría evitar, así que nunca debe enrutarse por la
escalera de reintento/división pensada para defectos corregibles.

No se aceptó como mecanismo principal ninguna heurística de palabras
clave o AST para decidir si la frontera aplica: la clasificación es por
identidad de perfil, fija, no por contenido de la entrega.

## Garantía real

Un proyecto marcado `imported=True` puede leerse, modificarse dentro de
un worktree aislado y validarse estáticamente (inventario de archivos,
sintaxis Python/JSON/JavaScript, preflight de imports, contrato de
comportamiento web) — el `Artifact` que produce cada intento se conserva
siempre. Lo que nunca ocurre es que Agentarium ejecute ese código: ni en
el camino autónomo del worker, ni al recuperar un artefacto anterior, ni
al evaluar un candidato enviado por un operador humano vía API — los tres
convergen en el mismo punto de aplicación. Una tarea que sólo necesita
validación estática no se rechaza por el mero hecho de pertenecer a un
proyecto importado.

## Limitaciones

Los perfiles no-ejecutores igual lanzan procesos reales del intérprete
(Python/Node) — corren código fijo de Agentarium (listar archivos,
`compile()` sin `exec()`, chequeo de sintaxis) o hacen parseo/regex en
proceso, nunca la lógica del archivo entregado, pero no son un sandbox de
sistema operativo tampoco. Para un proyecto **no** importado, la
ejecución de `SCRIPT_EXECUTION` sigue sin ningún aislamiento de SO más
allá de lo que `SafeCommandExecutor` ya imponía (allowlist de
ejecutables, deny-tokens, cwd acotado, timeout de proceso directo) —esta
fase no amplía ni reduce esa garantía preexistente, sólo la niega por
completo para proyectos importados.

## Condición futura para habilitar ejecución aislada

El único punto que necesita cambiar si en el futuro se implementa
aislamiento real de proceso (o cualquier otra frontera que PLANS.md
acepte) es la línea `allow_project_code_execution=not project.imported`
en `Orchestrator._evaluate_candidate` — hoy una negación directa del
campo; el día que exista una frontera real, esa expresión es el lugar
donde declarar bajo qué condición un proyecto importado sí puede
ejecutar código.
