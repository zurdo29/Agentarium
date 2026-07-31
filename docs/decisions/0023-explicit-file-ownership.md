# ADR 0023: Propiedad explícita de archivos en el DAG

- Estado: aceptada
- Fecha: 2026-07-31

## Contexto

Hallazgo colateral de la verificación en vivo de ADR 0021 (workspace
`bfab46f3-6ab8-4729-9561-f29963777538`): cuatro tareas hermanas sin relación
de dependencia entre sí ("listar", "agregar", "actualizar", "eliminar
libros") reachaban, cada una por su cuenta, el mismo nombre de archivo
convencional (`library_api.py`). `_colliding_dependency_paths` (ADR 0019) —
una compuerta real que protege contra pérdida silenciosa de datos —
rechazaba correctamente a la segunda en llegar. Dividir la primera en
subtareas (ADR 0021) no arregló esto: multiplicó cuántas tareas
independientes competían por el mismo archivo, porque `_attempt_split` sólo
razona sobre `acceptance_criteria`, nunca sobre paths.

Pedido explícito del usuario, con diseño propio incluido: el planificador
(`technical_manager`) nunca declaraba ni veía un solo path de archivo — cada
worker decidía su propio path de forma aislada. El pedido fue explícito en
no resolver esto debilitando `_colliding_dependency_paths`, sino dándole a
cada tarea un contrato de propiedad de archivo (`owned_paths`,
`shared_component`, `output_strategy`) verificado con una compuerta
mecánica antes de ejecutar el DAG, más un mecanismo real (no sólo orden de
dependencia) para que varias tareas combinen contenido en el mismo archivo
cuando genuinamente lo necesitan.

Diseño validado con un agente `Plan` antes de escribir código, que corrigió
tres puntos del planteo inicial: `WorkspaceMaterializer.stage()` no tiene
ningún enforcement por-tarea hoy (`_colliding_dependency_paths` sigue siendo
el único punto real de aplicación reactiva); no hay Alembic, así que las
columnas nuevas necesitan el mismo patrón de migración manual que ya usó
`_ensure_work_item_version_column` (ADR 0022); y `ContextBuilder.operational`
vacía `dependency_artifacts` en cada reintento, lo cual rompe la estrategia
`patch`/`consolidation` si no se ajusta — una tarea que debe extender el
contenido de una dependencia necesita seguir viéndolo después del primer
intento.

## Lo que se implementó

- **Contrato nuevo**: `OutputStrategy` (`exclusive`/`fragment`/`patch`/
  `consolidation`, `domain/enums.py`) y tres campos nuevos —
  `owned_paths: list[str]`, `shared_component: str | None`,
  `output_strategy: OutputStrategy = EXCLUSIVE` — en `TaskProposal`,
  `SubtaskProposal` (`planning/contracts.py`) y `WorkItem`
  (`domain/models.py`), persistidos vía columnas nuevas en `WorkItemRow`
  (migración manual idempotente, mismo patrón que ADR 0022).
  `owned_paths` sin `min_length=1`: una tarea sin archivo concreto puede
  dejarlo vacío, la compuerta reactiva sigue siendo la red de seguridad.
- **Preflight a nivel de plan** (`Orchestrator._resolve_owned_path_conflicts`,
  `engine.py`): tras planificar, detecta pares de tareas hermanas (ninguna en
  el cierre transitivo de dependencias de la otra) con `owned_paths`
  solapados sin un `shared_component` compartido + estrategia no-exclusiva
  de ambos lados. Si hay conflicto, pide una revisión a `technical_manager`
  (operación nueva `"plan_revision"`, hasta 2 intentos) antes de persistir
  ningún `WorkItem`. Si el conflicto persiste tras agotar los intentos, sigue
  con el plan tal cual — `_colliding_dependency_paths` es la red final.
- **Compuerta reactiva extendida, no debilitada**
  (`_colliding_dependency_paths`): una colisión se exime únicamente cuando
  el candidato comparte `shared_component` no nulo con el dueño actual del
  path **y** el candidato mismo no es `exclusive`. El camino por defecto (sin
  etiquetas en ningún lado) queda idéntico al de ADR 0019: rechaza igual que
  antes.
- **`_attempt_split` (ADR 0021) hereda propiedad de forma incondicional**:
  cada hija recibe `shared_component = item.shared_component or
  f"split-{item.id}"` y `output_strategy = FRAGMENT` **programáticamente**,
  sin importar lo que el propio `SubtaskProposal` del modelo declare. La
  consolidación hereda el mismo `shared_component` con
  `output_strategy = CONSOLIDATION`. Esto es deliberado: no depende de que
  el modelo use el vocabulario nuevo para que las hijas de una misma
  división no choquen entre sí en la compuerta reactiva.
- **Prompts**: `"plan"` pide `owned_paths`/`shared_component`/
  `output_strategy` por tarea, aclarando explícitamente que una dependencia
  sola sólo ordena ejecución, no dice cómo combinar contenido. `"work"`
  bifurca por estrategia (exclusive/fragment/patch/consolidation). Nueva
  entrada `"plan_revision"`. `"decompose"` pide lo mismo por subtarea.
  `ContextBuilder.operational` deja de vaciar `dependency_artifacts` en
  reintentos cuando `output_strategy` es `patch`/`consolidation`.
- **Tests**: contrato (`owned_paths` rechaza absolutos/`..`, `DecomposeProposal`
  rechaza solapamiento sin agrupamiento y lo acepta con estrategia no-
  exclusiva de ambos lados), `plan_project` end-to-end (dos hermanas
  solapadas → evento de conflicto → revisión mock la resuelve → `WorkItem`s
  sin conflicto), colisión reactiva (regresión: hermanas `exclusive`/`None`
  siguen chocando igual que ADR 0019; exención sólo con `shared_component` +
  estrategia no-exclusiva del candidato), y `_attempt_split` (hijas/
  consolidación con los campos nuevos correctos). Suite completa: 134/134 en
  verde, ruff y mypy limpios.

## Verificación en vivo — resultado mixto, documentado sin maquillar

Se recreó el mismo objetivo de biblioteca de ADR 0021 contra
qwen2.5-coder:7b (workspace `487c5194-296c-4e2c-9b33-7f5dd2e1835b`,
"Biblioteca API v4"), para comparar directamente contra el baseline de ADR
0021.

**El plan inicial no reprodujo el patrón de ADR 0021**: esta vez
`technical_manager` generó un único task grueso "Implementación de la API
REST en Python" (`expected_outputs: ["api.py"]`) en vez de cuatro tareas
hermanas por endpoint. El preflight de nivel de plan
(`plan_owned_path_conflict_detected`) nunca se disparó — no porque el
mecanismo fallara, sino porque no había nada que detectar. Esto es varianza
de granularidad del planificador entre corridas, no evidencia a favor ni en
contra del preflight.

**El mismo patrón reapareció, un nivel más abajo, dentro de `_attempt_split`
— y ahí sí es una señal real**: esa única tarea agotó sus 3 intentos (un
rechazo del revisor + dos candidatos idénticos byte-a-byte, la misma
compuerta anti-estancamiento de siempre) y disparó `decompose`.
`technical_manager` devolvió 4 subtareas ("listar", "agregar", "actualizar",
"eliminar"), **todas con `expected_outputs: ["api.py"]` y
`output_strategy: "exclusive"`, y `owned_paths` vacío en las que se
alcanzaron a inspeccionar** — exactamente el mismo patrón de colisión que
motivó este ADR, reproducido por el propio modelo dentro del flujo que se
diseñó para prevenirlo. El modelo no adoptó el vocabulario nuevo pese a que
el prompt de `"decompose"` se lo pide explícitamente — mismo tipo de
hallazgo negativo que ADR 0020 (una instrucción de prompt no garantiza que
un modelo chico la siga).

Esto **no** tumbó el proyecto por colisión: el `DecomposeProposal` es
válido igual (`owned_paths` vacío no viola ningún validador — no hay nada
que comparar), y `_attempt_split` habría forzado
`shared_component`/`FRAGMENT` en las 4 hijas de todas formas, sin depender
de que el modelo cooperara — ese es justamente el punto de que la herencia
sea incondicional. Pero el `decompose` falló por una razón previa y no
relacionada, ya existente desde ADR 0021: el criterio original combinado
("responder correctamente a todas las solicitudes de listar, agregar,
actualizar y eliminar libros") no es cubierto textualmente por ningún
criterio individual de las subtareas ("responder correctamente a
solicitudes de listar libros"), así que `_attempt_split` cortó por
`original_criteria.issubset(covered_criteria) == False` antes de llegar a
crear ningún `WorkItem` hijo. El proyecto terminó `failed` (71% de
progreso, 5/7 tareas completadas) sin haber llegado a ejercitar la herencia
de propiedad de forma end-to-end contra artefactos reales.

**Lectura honesta**:

1. La parte del mecanismo que **no depende de que el modelo use el
   vocabulario nuevo** (la herencia incondicional de `shared_component`/
   `FRAGMENT` en `_attempt_split`) sigue siendo sólida por diseño — el
   modelo puede seguir ignorando `owned_paths`/`output_strategy` y las
   hijas de una división no van a chocar entre sí en la compuerta reactiva.
2. La parte que **sí depende de que el modelo declare `owned_paths`** (el
   preflight de nivel de plan, y la posibilidad de que `decompose` etiquete
   bien un solapamiento genuino desde el origen) no se pudo confirmar ni
   refutar en esta corrida: nunca hubo un caso con `owned_paths` realmente
   poblado y solapado para ejercitar el validador. El modelo sigue
   señalando "este archivo" mediante `expected_outputs` (el campo viejo),
   no mediante `owned_paths` (el campo nuevo) — el mismo comportamiento que
   causó el bug original sigue vivo, sólo que ahora hay una capa
   programática debajo que no depende de que cambie.
3. La falla real de esta corrida (criterio combinado no cubierto por
   criterios individuales tras un `decompose`) es un límite preexistente de
   ADR 0021, no de este ADR — coincide con el mismo tipo de matching
   textual exacto que ya se sabía frágil, y queda fuera de alcance aquí.

No se repitió la corrida buscando una reproducción "más limpia" del
conflicto de plan: variar goal o reintentar indefinidamente hasta que el
modelo colisione de la forma exacta que se quiere observar no es
verificación, es *fishing* — y el hallazgo obtenido (el modelo ignora el
campo nuevo incluso cuando el prompt se lo pide, igual que en ADR 0020) ya
es información real y suficiente para cerrar esta iteración.

## Consecuencias

- El código es correcto y las pruebas lo confirman (134/134, incluyendo
  regresión explícita de que ADR 0019 sigue rechazando el caso por defecto).
- La protección **estructural** (herencia incondicional en `_attempt_split`)
  es la parte de este ADR en la que se puede confiar sin depender de
  compliance del modelo. Es la misma lección de ADR 0020/0018: cualquier
  parte del sistema que dependa de que un modelo chico siga una instrucción
  de prompt debe tratarse como probablemente ignorada, y diseñarse con una
  capa mecánica debajo que no dependa de eso — acá esa capa ya existe para
  el caso de `_attempt_split`, pero no (todavía) para el preflight de nivel
  de plan ni para el primer `decompose` de una tarea sin dividir antes.
- Trabajo futuro razonable, no implementado: hacer que el preflight (y el
  validador de `DecomposeProposal`) también miren `expected_outputs`
  solapados como señal de conflicto además de `owned_paths` — el campo que
  el modelo efectivamente sigue usando para indicar "este archivo es mío".
  Esto convertiría la detección en mecánica de verdad, sin depender de la
  adopción del vocabulario nuevo. Queda fuera de este ADR para no perseguir
  compliance del modelo indefinidamente en la misma sesión; es candidato
  natural para la próxima iteración.
- Workspace `487c5194-296c-4e2c-9b33-7f5dd2e1835b` queda como evidencia de
  auditoría (ver `PLANS.md`), no se borra ni se reintenta en esta sesión.
