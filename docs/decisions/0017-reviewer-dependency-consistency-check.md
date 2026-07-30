# ADR 0017: El revisor recibe los artefactos de dependencia y verifica consistencia

- Estado: aceptada
- Fecha: 2026-07-29

## Contexto

Se repitió la comparación de modelos con un segundo objetivo sin código
(documento de arquitectura en Markdown). Los 3 modelos terminaron
`completed`, pero qwen2.5-coder:7b produjo dos glosarios inconsistentes:
`docs/glossary.md` (de la tarea "Create Glossary") y un glosario distinto
embebido en `docs/design.md` (de la tarea "Combine Components, Decisions,
and Glossary", que declaraba "Create Glossary" como dependencia). El revisor
aprobó ambos artefactos igual.

Esto es la segunda ocurrencia independiente del mismo patrón visto con
qwen3:8b y el CSV de gastos (ADR 0016): una tarea produce contenido que
contradice o duplica de forma distinta lo que otra tarea del mismo proyecto
ya produjo, y nada lo detecta. Dos instancias, en dominios y modelos
distintos, dejan de ser un caso aislado.

Investigando la causa: `ContextBuilder.operational()` (usado para construir
el contexto del *worker*) ya incluye el contenido completo de los artefactos
de dependencia aprobados — el worker de "Combine..." literalmente tenía
disponible el glosario real de "Create Glossary" y aun así escribió uno
distinto. Pero `Orchestrator._review_payload()` (usado para el *revisor*)
nunca incluía artefactos de dependencia, sólo el artefacto y los archivos de
la tarea actual. El revisor no podía detectar la inconsistencia aunque
quisiera: no tenía con qué comparar.

## Decisión

- Se extrajo `ContextBuilder.dependency_artifacts(item)` de `operational()`
  (mismo cálculo, ahora reutilizable) para no duplicar la lógica de qué
  artefactos de dependencia están aprobados y con qué criterios.
- `_review_payload` ahora recibe y expone `dependency_artifacts` con el
  contenido completo de las dependencias aprobadas de la tarea, igual que ya
  recibía `artifact.files[].content` de la tarea actual.
- Cuando `dependency_artifacts` no está vacío, se agrega una entrada fija a
  los criterios semánticos de la tarea (no al `WorkItem.acceptance_criteria`
  persistido, para no interferir con la cobertura literal de ADR 0012 ni con
  lo que ve el usuario): `DEPENDENCY_CONSISTENCY_CRITERION` ("El artefacto no
  contradice ni redefine de forma distinta datos, cifras o términos ya
  establecidos en sus artefactos dependientes aprobados"). Esta lista
  extendida (`effective_criteria`) reemplaza a `item.acceptance_criteria` en
  los cuatro puntos donde antes se usaba dentro de `_evaluate_candidate`:
  selección de criterios semánticos, el relleno de criterios pendientes
  cuando la compuerta técnica ya falló, `_merge_review_fragments` y
  `_apply_technical_review_gate`.
- Cuando no hay dependencias (o ninguna está aprobada todavía), no se agrega
  nada: sigue siendo exactamente el comportamiento anterior. Verificado con
  el flujo mock existente, donde la tarea "scope" (sin dependencias) nunca
  recibe el criterio y "implementation"/"integration" (con dependencias)
  siempre lo reciben.
- El prompt del revisor ahora instruye explícitamente comparar
  `artifact.files[].content` contra `dependency_artifacts` cuando este último
  no está vacío.

No se intentó una comparación mecánica de contenido (diffs, hashes) porque
requeriría distinguir qué archivo es "entrada" y cuál es "salida" entre
tareas sin una noción general y confiable de eso — la misma razón por la que
ADR 0016 dejó ese caso explícitamente fuera de alcance. Esta corrección deja
el juicio semántico donde ya vivía (el revisor), simplemente dándole la
evidencia que le faltaba para ejercerlo.

## Consecuencias

El revisor ahora puede, en principio, rechazar una entrega que contradiga una
dependencia aprobada — antes era estructuralmente imposible porque nunca veía
esa dependencia. Sigue siendo un juicio de un modelo local, no una compuerta
mecánica determinista: no hay garantía de que el revisor use bien la
evidencia nueva (como ya se vio, el worker de qwen2.5-coder:7b tenía la misma
evidencia disponible y no la usó). El siguiente ciclo de "otro objetivo, 3
modelos" debe observar si esta corrección efectivamente cambia el resultado
del caso de glosarios duplicados o si el mismo patrón se repite una tercera
vez, lo que indicaría que hace falta una compuerta mecánica en vez de
depender del juicio del revisor.

## Adenda 2026-07-29: verificación real encontró un segundo bug

Antes de dar esto por resuelto se repitió el mismo objetivo contra
qwen2.5-coder:7b en vivo (no sólo tests). El plan real de ese run no repitió
la forma "Combine..." exacta, así que no probó directamente el caso de los
glosarios, pero reveló un bug distinto y más profundo: `ContextBuilder.
dependency_artifacts` exigía que `acceptance_criteria_addressed` del
artefacto de dependencia fuera superconjunto de los criterios propios de esa
tarea (`issubset`). El worker real de qwen2.5-coder:7b dejó
`acceptance_criteria_addressed: []` en una entrega que el revisor había
aprobado igual — el filtro la excluyó por completo, silenciosamente, tanto
del contexto del worker como de la compuerta de consistencia nueva.

Se corrigió `_addressed_matches_dependency`: sólo excluye cuando
`acceptance_criteria_addressed` es no vacío Y no comparte ningún criterio con
la dependencia (la señal real de "esto es de otra tarea", que es lo que
`test_operational_context_excludes_dependency_with_foreign_criteria` ya
verificaba y sigue verificando). Una lista vacía ya no se trata como
evidencia de exclusión — el veredicto `approved` del revisor es la
autoridad; `acceptance_criteria_addressed` es una señal adicional, no un
requisito estricto de proveedores reales.
