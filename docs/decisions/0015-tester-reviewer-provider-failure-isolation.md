# ADR 0015: Aislar fallos de proveedor del tester y el revisor

- Estado: aceptada
- Fecha: 2026-07-29

## Contexto

Al comparar qwen3:4b, qwen3:8b y qwen2.5-coder:7b con el mismo objetivo (una
herramienta CLI que procesa un CSV de gastos), la llamada al tester con
qwen3:8b agotó sus reintentos por timeout y lanzó `RoleExecutionError`. En
`Orchestrator._execute_work_item`, la llamada al worker ya capturaba ese error
y degradaba la tarea a `ready` o `failed` según el presupuesto de intentos, pero
`_evaluate_candidate` no envolvía las llamadas al tester ni al revisor. La
excepción se propagó sin capturar a través de `asyncio.gather` en
`run_project`, y como `asyncio.gather` no cancela las tareas hermanas cuando
una levanta una excepción (comportamiento documentado de asyncio), otra tarea
lista del mismo ciclo siguió ejecutándose en segundo plano, desconectada de la
petición HTTP que ya había fallado con 500. Esa tarea huérfana mantuvo
`scheduler.active` en un valor distinto de cero el tiempo suficiente para que
la siguiente petición de cambio de proveedor recibiera un 409 espurio. La
tarea original quedó bloqueada en `awaiting_review` hasta el próximo reinicio
del proceso.

## Decisión

Las llamadas al tester y al revisor (incluida la revisión focalizada por
criterio) en `_evaluate_candidate` ahora capturan `RoleExecutionError` con el
mismo tratamiento que ya usaba el worker: se llama a `_reject_evaluation`, que
reutiliza `_request_changes` para mover la tarea a `ready` si queda
presupuesto de intentos o a `failed` en caso contrario, y la petición HTTP
retorna una respuesta normal en vez de un error no controlado.

Actualización 2026-07-29: las llamadas de `brief` y `plan` dentro de
`plan_project` recibieron el mismo tratamiento, aunque no hay evidencia
reproducida de una falla real en ese camino todavía. Se decidió cerrar la
brecha igual porque es el mismo mecanismo exacto (`RoleExecutionError` sin
capturar propagándose desde `_call_role`), ya confirmado como explotable en
producción una vez; esperar una segunda reproducción antes de corregir un
código ya identificado como frágil no aportaba nada. Como todavía no existe
un `WorkItem` en esta etapa, no hay un `_request_changes` que reutilizar: se
agregó `_fail_planning`, que marca el proyecto como `failed` con un evento
explícito en vez de dejarlo atascado en `planning`.

## Consecuencias

Un timeout o una respuesta inválida agotada del tester, el revisor, el
director o el gestor técnico ya no puede tumbar una petición HTTP ni dejar
tareas hermanas huérfanas mutando estado sin que nadie las espere. El
presupuesto de intentos de la tarea sigue siendo la única fuente de verdad
sobre cuándo una tarea se da por fallida; para el fallo de planificación, la
única fuente de verdad es que el proyecto queda `failed` con el motivo en el
evento `planning_failed`, nunca atascado en `planning` indefinidamente.
