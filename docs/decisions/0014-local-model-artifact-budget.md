# ADR 0014: Presupuesto de contexto para entregas integradas

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

Una tarea terminal debía revisar una aplicación completa contra veintidós
entradas de alcance y aceptación. El modelo local recibió los artefactos de
dependencia y el contrato, pero el contexto de 8192 tokens dejaba poco espacio
para devolver HTML, CSS y JavaScript completos. Varias respuestas terminaron
con cadenas JSON abiertas, sin llegar a materializar un candidato.

Además, el proveedor informaba el fallo sólo como JSON inválido, ocultando si el
runtime había terminado por límite de longitud.

## Decisión

El trabajador de implementación usa un contexto de 16384 tokens y un máximo de
6000 tokens de salida. Tester y revisor usan 8192/2400 para poder inspeccionar
artefactos y contratos de cierre más amplios. La concurrencia local permanece
limitada a una inferencia, por lo que el cambio amplía capacidad por tarea sin
ampliar paralelismo ni autoridad.

El proveedor Ollama detecta `done_reason=length` y produce un error explícito de
límite de contexto o salida antes de intentar interpretar JSON truncado.

Cuando una compuerta técnica fija ya rechazó el candidato, la revisión semántica
se conserva pero se limita a tres criterios representativos. Los criterios
restantes quedan conservadoramente en falso para el siguiente intento. Si la
compuerta técnica pasa, el revisor sigue obligado a cubrir todos los criterios.
Así se evita gastar hasta una llamada por cada criterio de un candidato que no
puede integrarse de todos modos.

En reintentos, si el conjunto de rutas y contenidos es idéntico byte por byte al
último candidato rechazado de la misma tarea, el orquestador lo rechaza antes de
crear un worktree o invocar tester y revisor. Cambiar sólo el resumen del
artefacto no cuenta como corrección.

Los atributos HTML `placeholder` y el selector CSS `::placeholder` no se
consideran marcadores de implementación pendiente. La prohibición conserva
`TODO`, `FIXME`, texto “placeholder” suelto y comentarios de lógica pendiente.

## Consecuencias

Las entregas integradas tienen espacio suficiente para conservar el producto y
aplicar correcciones reales. Cada inferencia puede consumir más memoria y
tiempo, pero el límite sigue acotado y auditable. Los diagnósticos distinguen
respuestas estructuralmente incorrectas de respuestas cortadas por presupuesto.
