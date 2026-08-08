# ADR 0035: Importar un proyecto existente sin escribir en el original (P4.1)

- Estado: aceptada
- Fecha: 2026-08-08

## Contexto

PLANS.md P4.1: importar un proyecto existente sin poner en riesgo el
original. Hasta ahora todo proyecto era greenfield
(`ApplicationService.create_project`); ningún código referenciaba una
ruta fuera de `workspace_root`. P3.4 (ADR 0034) dejó preparado
`Project.imported: bool` -- la frontera de ejecución fail-closed ya
depende de que este campo esté bien poblado, y esta fase es la que lo
puebla de verdad.

## Decisión

El destino de la importación es el mismo lugar que ya usa un proyecto
greenfield (`workspace_root/<project_id>/project`), así que
`GitWorktreeIsolation`/`SafeCommandExecutor`/la compuerta de P3.4 siguen
funcionando sin ningún cambio de código una vez que la copia existe.
`source_path` y `workspace_root` se resuelven a su ruta real y se
rechaza el import si uno es ancestro/descendiente del otro, en
`inspect()` y de nuevo en `import_into()`. `source_path` debe llegar
absoluto a la API (`InspectImportRequest`/`ImportProjectRequest`); una
ruta relativa ahí es ambigua y se rechaza en el validador antes de tocar
el filesystem.

Caso git: todo comando contra el origen usa `--no-optional-locks`
(cero bytes escritos, no sólo cero bytes salvo el cache de stat).
Un origen sucio o con submódulos se rechaza directo. El snapshot
(HEAD + limpio) se captura antes del clone y se vuelve a leer del origen
inmediatamente después; si difiere, el destino recién creado se borra
por completo y se rechaza -- esto detecta y rechaza una inconsistencia,
no vuelve atómica la operación de clonar. `git clone --no-local` nunca
hardlinkea con el original, en cualquier volumen; el diseño no depende
de la versión de git ni de su historial de CVEs para esa garantía.
`--no-checkout` seguido de `git config core.autocrlf/core.longpaths` y
recién después `checkout -f HEAD` es la secuencia que de verdad aplica
esa config antes de materializar el árbol de trabajo -- confirmado a
mano que pasarla como `-c` sólo al `clone` no sobrevive al checkout
interno que el propio `clone` ya hizo. `origin` se elimina del destino
al terminar.

Caso carpeta plana (sin `.git`): cualquier symlink/junction/reparse
point encontrado rechaza el import explícitamente, nunca se salta en
silencio. Escaneo completo antes de copiar nada; el primer archivo
ilegible aborta y limpia el destino parcial. Luego `git init` + la misma
config + un commit inicial.

Cada llamada git que estas dos rutas necesitan que tenga éxito (clone,
config, checkout, init, add, commit, rev-parse, remote, branch) pasa por
`_run_git_checked`, que revisa el código de salida y levanta
`ImportSourceError` con el mensaje real de git -- antes ninguna llamada
revisaba el código de salida, así que un fallo real de git dejaba el
destino a medio construir y el *siguiente* comando de la secuencia
fallaba varios cuadros después con una excepción de bajo nivel sin
relación aparente, en vez de un rechazo limpio. Encontrado a mano
importando un repo real desde una ruta larga de este equipo: `git
clone` falló con "Filename too long" (un path de 266 caracteres bajo
Windows), el código lo ignoró, y `git config` reventó después contra un
directorio que nunca llegó a existir. `-c core.longpaths=true` se pasa
también directo en la propia invocación de `clone` -- a diferencia de
`core.autocrlf` (que sólo afecta al árbol de trabajo, diferido por
`--no-checkout`), el propio clonado ya escribe objetos/paquetes de
nombre largo antes de que exista destino contra el cual correr `git
config`.

`Project` gana `imported_source_path: str | None` e
`imported_commit: str | None` (`MigrationStep` nuevas, sin backfill).
`ApplicationService.import_project` genera el `project_id`, corre la
importación completa contra él, y sólo si termina sin error persiste la
fila -- un fallo a mitad de camino nunca deja un `Project` con
`imported=True` sin contenido real en disco. CLI
(`agentarium project import`) es async-envuelto como `run`, no síncrono
como `create`, porque esta operación shell-ea; resuelve la ruta a
absoluta (`resolve_path=True`) para que el usuario pueda escribir una
relativa desde su propia terminal, y traduce `ImportSourceError` a un
mensaje limpio con código de salida propio en vez de un traceback. La UI
es un campo de texto (el navegador no puede entregar una ruta absoluta
real): escribir ruta → inspeccionar → vista previa → confirmar con un
goal → importar.

## Garantía real

El origen sólo se lee -- durante inspección y durante el import, que
incluye releerlo justo después del clone para detectar un cambio a
mitad de camino -- y nunca se escribe. Una vez que `import_into()`
retorna, Agentarium no vuelve a tocar el origen: sólo usa su copia.
Verificado a mano, no sólo con fixtures de test: importando un
repositorio real de tres commits, `git status`/`git log`/HEAD del
origen quedan byte a byte idénticos antes y después, y una tarea real
corrida contra el proyecto importado termina bloqueada por la compuerta
de P3.4 (evento `imported_project_execution_blocked`) exactamente como
promete ADR 0034.

## Limitaciones

Una carpeta plana no tiene ningún concepto de snapshot transaccional --
a diferencia del caso git (HEAD + limpio, verificable antes/después), una
modificación concurrente durante la copia no tiene mecanismo de
detección en esta fase. Los submódulos se detectan y se rechazan, no se
resuelven -- resolverlos exigiría acceso de red, que este mecanismo no
introduce bajo ninguna circunstancia. No hay selector de carpeta nativo.
La API de importación (`/api/projects/import[/inspect]`) presupone el
binding local/loopback actual de Agentarium: cualquier ruta absoluta
que el proceso backend pueda leer es candidata a import, sin ninguna
frontera de autenticación/autoridad propia más allá de eso. Exponerla
más allá de loopback exige agregar esa frontera antes de aceptar
tráfico remoto.

## Qué queda para P4.2

`imported_commit` se captura y persiste pero esta fase no lo usa para
nada más que mostrarlo -- P4.2 (exportar cambios) es quien lo necesita,
para producir un diff limpio contra el commit real del origen.
