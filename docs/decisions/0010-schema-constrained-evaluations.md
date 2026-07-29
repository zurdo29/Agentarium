# ADR 0010: Evaluaciones de modelo restringidas por esquema

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

Los contratos de brief, plan y trabajo se incluían en el prompt, pero Ollama
recibía únicamente `format: "json"`. Tester y revisor ni siquiera tenían un
contrato Pydantic propio. En la primera ejecución real, el tester devolvió JSON
válido con nombres de campos distintos y el orquestador intentó leer `passed`
directamente, provocando un error interno y dejando la tarea en revisión.

## Decisión

Todas las operaciones estructuradas tienen un contrato Pydantic versionado:

- `TestEvaluationProposal` exige `passed`, checks tipados y resumen.
- `ReviewEvaluationProposal` exige veredicto, razones y un resultado por cada
  criterio de aceptación.

Para Ollama, `model_json_schema()` se envía en el campo `format` además de
incluirse en el prompt. El orquestador vuelve a validar cada respuesta antes de
persistirla y convierte una respuesta inválida en cambios solicitados, nunca en
un error HTTP sin control.

Las llamadas estructuradas envían `think: false` para reservar el presupuesto de
salida al JSON y al contenido de archivos, evitando que modelos con razonamiento
interno consuman `num_predict` antes de completar el contrato.

El trabajador local dispone de una única ventana de generación de 300 segundos.
No se repite automáticamente la misma solicitud al proveedor: los reintentos se
mantienen en el nivel de tarea, donde cambian el feedback, quedan auditados y
consumen explícitamente el presupuesto correspondiente.

Los checks fijos conservan autoridad: el tester no puede aprobar si checksum,
aislamiento o perfiles locales fallan, aunque el modelo indique lo contrario.
Las tareas interrumpidas durante `awaiting_review` también se recuperan como
listas para reintento al iniciar.

El revisor semántico recibe el artefacto, el contenido completo de los archivos y
los criterios de aceptación, pero no el detalle ni la opinión del tester. La
compuerta técnica se aplica por separado para evitar que una apreciación técnica
incorrecta contamine el veredicto sobre el contenido.

El veredicto final se deriva de `acceptance_results`: solamente se aprueba cuando
todos los criterios son verdaderos. Si el modelo devuelve un veredicto
contradictorio, se normaliza y el ajuste queda registrado como evento auditable.
Si el revisor omite criterios, el orquestador solicita revisiones focalizadas de
cada criterio faltante, fusiona los fragmentos en el orden contractual y registra
la recuperación. El payload semántico excluye autodeclaraciones del trabajador,
evidencia narrativa y metadatos de aislamiento. Si una respuesta focalizada sigue
siendo inválida, ese criterio se conserva como falso para generar feedback en vez
de descartar toda la evaluación.

Para evitar que un modelo pequeño copie una dependencia en lugar de ejecutar la
tarea actual, el contexto de trabajo incluye únicamente artefactos de dependencia
aprobados, hasta tres revisiones previas de la tarea y una instrucción de reintento.
Una declaración de criterios que no coincide con la tarea genera una advertencia
auditable, pero no descarta el contenido: el revisor semántico inspecciona los
archivos contra todos los criterios y la lista autodeclarada no reemplaza esa
evaluación.

El contenido completo de dependencias se entrega sólo en el primer intento. En
reintentos se sustituye por referencias resumidas (id, título, resumen y paths) y
se prioriza el feedback de revisión, evitando que el modelo vuelva a copiar el
mismo artefacto rechazado.

El contrato de trabajo prohíbe TODOs, placeholders, funciones vacías y comentarios
que sustituyan comportamiento. Un `expected_output` funcional debe contener una
implementación ejecutable concreta antes de llegar a revisión. Los marcadores
inequívocos también se rechazan de forma determinista al validar el contrato.

En Windows, un indexador o Git puede mantener abierto brevemente un archivo de
worktree y negar `os.replace`. La materialización intenta la vía atómica y, sólo
ante ese error de permisos, escribe el contenido validado en el destino, fuerza
el flush y conserva el checksum como compuerta. Si tampoco puede escribir, el
fallo se convierte en un rechazo recuperable de workspace en vez de dejar la
tarea ejecutándose y devolver un error HTTP.

Cuando una tarea agotó su presupuesto, un reintento solicitado explícitamente
por el usuario amplía el máximo en una unidad, hasta el límite de dominio de
veinticinco. La ampliación queda registrada como evento y no borra intentos
anteriores. El límite sigue siendo finito, pero no confunde fallos de integración
recuperables con diez defectos reales del producto.

## Consecuencias

Las evaluaciones locales son más predecibles y un modelo pequeño dispone de
menos grados de libertad para cambiar el contrato. Una evaluación inválida
consume un intento de la tarea, queda auditada y puede corregirse; no bloquea
todo el proyecto en un estado intermedio.
