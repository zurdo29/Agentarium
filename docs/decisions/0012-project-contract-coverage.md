# ADR 0012: Cobertura obligatoria del contrato de proyecto

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

En una prueba con una aplicación local de inventario, el director conservó el
alcance, los entregables y siete criterios de éxito, pero señaló una duda menor
sobre el significado de “estado de stock”. El gestor técnico convirtió esa
aclaración en la única tarea del DAG. La tarea produjo un documento válido y
superó tester y revisión, por lo que el proyecto se declaró completado sin
crear la aplicación solicitada.

Los contratos de tarea eran coherentes internamente, pero no existía un
invariante que relacionara el DAG completo con el brief.

## Decisión

El prompt de planificación distingue las ambigüedades que cambian materialmente
el alcance o requieren autoridad externa de las dudas no bloqueantes. Estas
últimas se resuelven como supuestos conservadores y nunca sustituyen el objetivo
principal.

Cada entregable del brief debe aparecer literalmente en `expected_outputs` de
al menos una tarea y cada entrada de alcance y criterio de éxito debe aparecer
literalmente en `acceptance_criteria`. El alcance también es contractual: un
resumen correcto no puede omitir búsquedas, filtros u otras capacidades sólo
porque el director no las repitió en `success_criteria`. Si la propuesta del
modelo omite cualquier entrada, el orquestador añade una tarea terminal que
depende de todos los extremos del DAG y materializa el contrato completo. La
reparación queda registrada como evento con las entradas ausentes.

Antes de marcar un proyecto como completado, el orquestador vuelve a comprobar
la misma cobertura. Si el DAG persistido no cubre el contrato, el proyecto
falla con evidencia explícita en vez de producir un falso éxito.

La coincidencia es normalizada pero literal. Esto evita depender de similitud
semántica no determinista y garantiza que los criterios revisados a nivel de
tarea sean exactamente los criterios aceptados en el brief.

## Consecuencias

Una mala descomposición ya no puede reducir un objetivo amplio a una tarea de
aclaración y terminar con éxito. En planes que parafrasean correctamente el
brief puede aparecer una tarea de cierre adicional; ese costo se acepta a
cambio de mantener un contrato auditable de extremo a extremo. El prompt
versionado incentiva a los proveedores a copiar las entradas exactas y evitar
esa tarea cuando el plan ya ofrece cobertura completa.
