# ADR 0019: Rechazar rutas de archivo que colisionan entre tareas no relacionadas

- Estado: aceptada
- Fecha: 2026-07-29

## Contexto

Repitiendo el objetivo de documento de arquitectura contra qwen2.5-coder:7b
una cuarta vez (ya con ADR 0017/0018 aplicados), el proyecto volvió a fallar,
pero por una causa nueva y más grave que las anteriores: dos tareas
**sin relación de dependencia entre sí** ("Crear Glosario de Términos del
Dominio" e "Escribir Decisiones de Diseño", ambas dependientes sólo de
"Identificar Componentes") declararon el mismo path, `docs/design.md`. Al
integrarse ambas, la segunda sobrescribió por completo el contenido de la
primera sin ningún aviso, error o evidencia — el glosario simplemente
desapareció. La tarea final de combinación heredó ese estado ya roto y
falló tres veces contra un criterio genérico ("no cumple con todos los
criterios de éxito"), sin que el motivo real (pérdida silenciosa de datos
por colisión de ruta) quedara nunca expuesto.

A diferencia de la inconsistencia semántica de ADR 0017 (que requiere juicio
de un revisor), esto es determinista y mecánico: dos tareas no relacionadas
que declaran la misma ruta es, en sí mismo, siempre una señal de un problema
de planificación o de ejecución, sin necesidad de leer el contenido.

## Decisión

`Orchestrator._colliding_dependency_paths(item, proposal)` calcula, para cada
archivo propuesto por el candidato actual, si esa ruta ya fue declarada por
un artefacto de OTRA tarea que no es la tarea actual ni pertenece al
**cierre transitivo completo** de sus dependencias (`_transitive_dependency_ids`,
no sólo `dependency_ids` directas). Si hay alguna coincidencia, el candidato
se rechaza con `InvalidPlan` antes de crear un worktree (mismo punto y misma
disciplina que el chequeo de candidato idéntico de ADR 0014), consumiendo
presupuesto de intentos como cualquier otro rechazo.

Una tarea SÍ puede reescribir legítimamente una ruta que pertenece a
cualquiera de sus ancestros en el DAG — sólo se bloquea la colisión con
tareas no relacionadas. No se distingue entre artefactos aprobados y
rechazados de la tarea ajena: si otra tarea alguna vez propuso esa ruta, se
trata como señal suficiente, priorizando detectar el problema temprano sobre
permitir una coincidencia posiblemente inocente.

**Nota sobre la primera versión de este ADR**: la implementación inicial sólo
miraba `dependency_ids` directas. Verificándolo en vivo contra
qwen2.5-coder:7b, la propia compuerta nueva rechazó a la tarea de cierre de
ADR 0012 tres veces seguidas: esa tarea sólo depende directamente del último
nodo del DAG ("Revisión y Aprobación Final"), no de los nodos que
originalmente crearon `docs/architecture.md` varios pasos atrás. Corregido
para usar el cierre transitivo completo, con una prueba dedicada
(`test_colliding_dependency_paths_allows_transitive_ancestor_ownership`) que
reproduce exactamente esa forma de DAG (abuelo → padre → cierre).

## Consecuencias

Dos tareas independientes que compiten por el mismo archivo ya no pueden
destruirse silenciosamente entre sí; el rechazo consume el presupuesto de
reintentos de la tarea más reciente y queda registrado como evidencia, en
vez de manifestarse como un fallo genérico río abajo sin explicación. El
costo es un falso rechazo poco probable: una tarea distinta y no relacionada
que casualmente elige el mismo nombre de archivo por coincidencia (no por
error de planificación) también sería rechazada; dado que las rutas suelen
ser descriptivas, se acepta ese costo con el mismo criterio que ya usa ADR
0014 para su propio chequeo de candidato idéntico.
