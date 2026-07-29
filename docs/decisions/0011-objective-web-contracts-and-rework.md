# ADR 0011: Contratos web objetivos y revisiones posteriores

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

Una entrega web superó sintaxis, checksums, aislamiento y una revisión semántica,
pero no podía iniciar: JavaScript solicitaba un contexto 2D a un `div`. Además,
la navegación actualizaba un solo eje, los enemigos no se movían y no existían
elementos básicos del dominio solicitado. El modelo revisor aprobó afirmaciones
por presencia de nombres de funciones, sin comprobar su comportamiento.

## Decisión

Las entregas que incluyen HTML y JavaScript ejecutan un perfil fijo
`web_application`. El perfil relaciona ids y etiquetas HTML con su uso desde
JavaScript, comprueba scripts locales y, cuando los criterios lo exigen, verifica
señales concretas de navegación bidimensional, persecución y reglas básicas de
Pac-Man. Los comentarios se excluyen de esas señales.

Una falla de perfil tiene autoridad sobre la opinión del tester o del revisor:
normaliza el veredicto a cambios solicitados, marca los criterios como no
demostrados e incorpora el stderr del perfil al feedback del siguiente intento.
Si la explicación estructurada del tester es inválida, no invalida los checks
fijos: se reemplaza por una explicación local auditada y el resultado técnico se
conserva. La revisión semántica sigue siendo obligatoria.
Los tres fallos de validación más recientes se incluyen de forma estructurada y
prominente en el contexto del trabajador; una afirmación en el resumen no cuenta
como corrección si el comportamiento no aparece en el contenido de los archivos.
Los archivos completos de candidatos rechazados no se reinyectan en el prompt:
las pruebas reales mostraron que modelos locales pequeños tendían a copiarlos
literalmente. El feedback sí incluye instrucciones correctivas concretas para
producir una implementación nueva. Además, se deduplican hasta veinte requisitos
de validación históricos para que una reescritura no olvide capacidades ya
aprendidas. Coleccionables y puntuación requieren cambios de estado observables,
no sólo variables o funciones de dibujo.

Las tareas completadas permanecen inmutables. Una revisión posterior crea una
nueva tarea dependiente y auditable, reduce el progreso del proyecto y lo
devuelve a estado listo. Así se conserva el historial aprobado sin reescribirlo.

Un canvas creado desde JavaScript debe insertarse en el documento, usar un id
que no duplique elementos existentes y ejecutar su renderer durante la
inicializacion; crear el objeto en memoria no demuestra una vista visible.
Las revisiones pueden extender criterios de aceptacion sin modificar la tarea
historica. Cuando los nuevos criterios lo solicitan, el perfil web exige
colision geometrica entre jugador y paredes, vidas que disminuyen hasta game
over, victoria al consumir todos los coleccionables y una accion de reinicio
conectada a la interfaz.
La evidencia de perfiles incluye una version de contrato. El contexto de un
reintento solo reutiliza fallos de la version vigente, para que correcciones del
validador no conviertan falsos positivos antiguos en requisitos permanentes.

## Enmienda: refinamiento del candidato de la misma tarea

La evidencia posterior mostro que reconstruir cada intento desde el producto
integrado tambien hacia perder capacidades que ya habian superado perfiles
objetivos. Se reemplaza por ello la decision de omitir todos los archivos
rechazados: en un reintento se incluyen, con limites de cantidad y tamano,
solamente los archivos del candidato mas reciente de la misma tarea. Se
presentan como base editable, junto con los fallos acumulados. Nunca se mezclan
candidatos de otras tareas ni artefactos de dependencias rechazados.

Una respuesta inicial malformada del revisor tampoco descarta el candidato:
cada criterio se reevalua por separado. Una respuesta focalizada invalida sigue
produciendo un resultado conservador negativo, de modo que la revision
semantica no se omite ni se convierte en una aprobacion automatica.

Los candidatos historicos pueden recuperarse explicitamente. La recuperacion
extiende el presupuesto, rematerializa los archivos en un worktree nuevo y
repite perfiles fijos, tester y revision semantica antes de integrar. El
artefacto debe pertenecer al mismo proyecto y a la misma tarea; recuperar no
equivale a aprobar ni a copiar directamente sobre el producto.
Una revision tambien puede sembrarse desde un artefacto aprobado de su
dependencia directa. La semilla se evalua contra los criterios extendidos de la
revision y puede fallar normalmente; su objetivo es conservar una base funcional
sin confiar en candidatos regresivos de intentos posteriores.
Cuando el modelo repite un candidato sin cambios, un operador local puede
presentar archivos completos. La intervencion crea una ejecucion auditada y
atraviesa el mismo worktree, perfiles fijos, tester y revisor; no escribe
directamente sobre el producto ni implica aprobacion automatica.

## Consecuencias

Los falsos positivos web simples se detienen antes de integrar cambios. Los
contratos son deliberadamente conservadores y sólo activan requisitos de juego
cuando aparecen en los criterios de aceptación. Una corrección posterior añade
una revisión al DAG y métricas, en lugar de borrar la evidencia previa.
