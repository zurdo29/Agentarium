# ADR 0036: Exportar cambios de forma auditable sin tocar el origen (P4.2)

- Estado: aceptada
- Fecha: 2026-08-08

## Contexto

PLANS.md P4.2: patch y/o branch exportable, nunca sobrescribir el repo
origen silenciosamente, resumen de archivos/tests/validaciones asociado a
la entrega. P4.1 (ADR 0035) dejó `Project.imported_commit` persistido con
ese único propósito futuro declarado en su propio comentario. El hecho que
hace viable esta fase sin inventar nada: cada work item aceptado ya corre
`git merge --no-ff --no-edit` sobre `main` en el repo propio del proyecto
(`GitWorktreeIsolation.integrate()`), así que `main` ya tiene una historia
real y auditable -- exportar es leer esa historia con `git format-patch`/
`git bundle`, nunca reconstruirla a mano.

## Decisión

`base = project.imported_commit` si el proyecto fue importado; si es
greenfield, la raíz del propio repo (`git rev-list --max-parents=0 main`).
Un solo rango exclusivo `base..main` sirve para los tres caminos reales de
construcción (import git, import carpeta plana, greenfield), verificado
con `git merge-base --is-ancestor` antes de usarlo en vez de asumirlo.

Dos artefactos, un solo módulo nuevo (`isolation/export.py`,
`ProjectExporter`, mismo idioma de dos niveles que `import_source.py`):
**patch** (`git format-patch --binary <base>..main --stdout`, un solo
archivo mbox que `git am` aplica commit por commit) y **bundle**
(`git bundle create changes.bundle <base>..main`, portable, cuyo
prerequisito es exactamente `base` -- lo que permite `git fetch` desde un
clon real del origen sin que Agentarium toque la red). El stdout de
`format-patch --binary` se captura como bytes crudos sin decodificar
(`_run_git_bytes_checked`, comparte spawn con `_run_git`/`_run_git_checked`
vía un `_spawn_git` interno): el helper genérico decodifica con
`errors="replace"`, aceptable para texto de diagnóstico de git pero que
corrompería en silencio un hunk binario armado en base85 si algún byte no
fuera UTF-8 válido.

`imported_source_path` gana un único uso nuevo, puramente defensivo: el
destino de exportación se compara (nunca se lee/escribe) contra ese valor
persistido para rechazar un destino que caiga dentro del repositorio
original -- sin este chequeo, nada impedía apuntar `destination` a la
misma ruta que se usó para importar y sobreescribir ahí `changes.patch`/
`summary.*` sin darse cuenta. `imported_commit` sigue siendo lo único que
participa en el cálculo del rango.

**Corrección de diseño, encontrada en revisión antes de implementar:**
`GitWorktreeIsolation._project_lock` se libera apenas termina el `async
with` de `integrate()`, *antes* de que `engine.py` escriba el evento
`change_set_integrated` con el `integration_commit` real -- una ventana
angosta donde `main` ya avanzó pero la base de datos todavía no lo sabe.
`ApplicationService.export_project()` lee los eventos de integración
*antes* de llamar al exportador y le pasa el último `integration_commit`
conocido como parámetro plano; `export()`, ya bajo un `project_lock()`
público nuevo (único cambio aditivo a `git_worktree.py` en toda la fase)
y con el `HEAD` real recién leído, rechaza si no coinciden. Sólo puede
fallar hacia el lado seguro -- `HEAD` se lee bajo el lock, los eventos se
leyeron antes de tomarlo -- nunca acepta un snapshot incoherente.

**Segunda corrección de la misma revisión:** publicación fail-safe.
`export()` genera patch/bundle bajo el lock pero escribe en una carpeta de
*staging* oculta y nunca la publica; `ApplicationService` escribe
`summary.json`/`summary.md` en esa misma carpeta y sólo entonces llama
`publish()` (rename atómico al nombre público) -- cualquier excepción
intermedia (típicamente la escritura del resumen, que necesita datos de la
base que el exportador nunca toca) dispara `discard()`. `project_exported`
sólo se emite después de un `publish()` exitoso.

Alcance deliberado: cada exportación cubre siempre el rango completo desde
`base`, nunca "desde la última exportación" -- sin columna nueva, sin
migración. `SafeCommandExecutor` no se reutiliza, mismo motivo que
`ImportSource` en ADR 0035: su chequeo de `cwd`-dentro-de-`workspace_root`
es correcto para su propósito pero una categoría distinta a la de export
(el `cwd` de cada comando sí está dentro de `workspace_root`; lo que está
fuera es sólo el argumento de salida). Resumen (`services/export_summary.py`,
`build_export_summary`/`render_export_summary_markdown`) reutiliza
`project_detail()`'s misma fuente de datos ya persistida y cruza cada
`change_set_integrated` contra el `git log` real del rango exportado
(`consistency.matches_git_history`) -- eso es lo que lo hace auditable en
vez de un volcado sin verificar.

## Garantía real

Verificado con 19 tests contra repos git reales (`test_export.py`, sin
mocks de git) y, además, a mano con un repositorio real de este equipo,
no sólo fixtures: un repo original de dos commits importado vía
`agentarium project import`, un proyecto greenfield real corrido con el
proveedor mock hasta completar tres work items reales, exportado vía
`agentarium project export`. El patch se aplicó con `git am` en un clon
fresco checkouteado en el commit base -- el árbol resultante
(`HEAD^{tree}`) es idéntico byte a byte al árbol real de `main` (no una
comparación de SHA de commit: `git am` linealiza cada patch como un commit
nuevo, que nunca puede reproducir el SHA de un merge `--no-ff` aunque el
contenido sea idéntico). El bundle se `fetch`eó en un clon separado del
propio repo del proyecto y el SHA del ref resultante coincidió
exactamente con el `main` real. El repositorio original quedó byte a byte
idéntico (`git status`/HEAD) antes y después de todo el proceso. Los dos
rechazos limpios se confirmaron a mano, no sólo por test: un proyecto
importado sin nada integrado (bloqueado por la compuerta de autoridad de
P3.4/ADR 0034, que sigue vigente sin cambios) rechaza con "rango vacío", y
un proyecto recién creado nunca corrido rechaza con "todavía no tiene un
repositorio propio" -- ambos con el código de salida 11 de la CLI.

## Limitaciones

Sin tracking incremental "desde la última exportación", por diseño: una
segunda exportación reproduce el mismo conjunto completo otra vez. Sin
ruta de descarga HTTP (`FileResponse`) -- el backend escribe directo en
la ruta que el usuario entrega, mismo espíritu que el campo de texto de
P4.1. La API de exportación hereda la misma limitación que ADR 0035
declaró para el import: presupone el binding local/loopback actual;
exponerla más allá exige agregar una frontera de autenticación/autoridad
que hoy no existe. El chequeo de alineación git/DB sólo puede rechazar y
pedir reintentar -- un fallo permanente de escritura del lado de la base
de datos (no una demora normal) dejaría ese chequeo fallando para siempre
en ese proyecto; no se construyó una reparación para ese caso extremo en
esta fase, sólo se lo nombra honestamente.

## Qué queda para P4.3

Esta fase no toca el centro de reparación (ver tareas fallidas, reintentar
sin perder linaje, exponer evidencia). P4 como conjunto sigue abierto:
P4.1 y P4.2 resuelven "importar sin arriesgar" y "exportar de forma
auditable", pero el criterio de cierre de P4 también exige "revisar/
reparar" (P4.3) y "entrega y auditoría" (P4.4).
