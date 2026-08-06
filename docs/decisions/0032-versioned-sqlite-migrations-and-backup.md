# ADR 0032: Migraciones versionadas y backup de SQLite (P3.2)

- Estado: aceptada
- Fecha: 2026-08-06

## Contexto

Antes de P3.2, `Database.create_all()` sólo sabía crear tablas faltantes
(`Base.metadata.create_all`) y, para `work_items`, correr una tupla fija de
`ALTER TABLE ... ADD COLUMN` en un orden implícito
(`_WORK_ITEM_COLUMN_MIGRATIONS`/`_ensure_work_item_columns`). Sin
`PRAGMA user_version` ni ningún otro versionado: cada arranque reinspeccionaba
`PRAGMA table_info(work_items)` para decidir qué faltaba. Sin backup previo:
un fallo a mitad de los `ALTER TABLE` (disco lleno, proceso matado) podía
dejar la tabla en un estado intermedio sin ningún registro de qué esperar ni
cómo volver atrás. `PLANS.md` P3.2 pide cerrar exactamente esto antes de
importar proyectos reales valiosos, donde una corrupción no es sólo un test
que falla.

## Decisión

### `PRAGMA user_version` + lista de pasos, no Alembic

SQLite expone un entero de 32 bits en el header pensado para esto. Se
reemplaza la tupla implícita por `migrations.STEPS`: una lista ordenada de
`MigrationStep(version, column, definition, backfill)`, una por cada columna
que `work_items` ganó alguna vez (`git log --follow` sobre `tables.py`
confirma exactamente 3 commits, todos `ADD COLUMN`, nunca un rename/drop, y
sólo esa tabla). `CURRENT_SCHEMA_VERSION` es la versión del último paso.

No se adoptó Alembic: esta base es SQLite-only por diseño explícito
(Postgres queda en P5), y su historial completo de cambios de schema son 6
`ADD COLUMN`. El costo real no es la dependencia sino la superficie
operativa nueva — un `env.py` adaptado a `package-dir = {"" = "backend"}` y
a `Settings.resolved_database_url()`, más un `stamp head` sobre la
`runtime/agentarium.db` que ya está viva en producción. Mismo criterio que
P3.1a/P3.1b.

**Disparador explícito de revisión:** el día que haga falta renombrar o
borrar una columna, o soportar un segundo dialecto (Postgres, P5), reabrir
esta decisión — una capa propia deja de ser defendible en cualquiera de esos
dos casos.

### `user_version == 0` es legacy/ambiguo, nunca se asume un prefijo

Toda base real hoy (producción, cada benchmark, cada base de un
desarrollador) ya pasó por `_ensure_work_item_columns()` alguna vez y
probablemente ya tiene la mayoría o la totalidad de los 6 pasos aplicados —
pero `user_version` nunca se escribió, así que las lee todas como `0` por
igual. `migrations.pending_steps(connection, version)` resuelve esto sin una
función de "bootstrap" separada: cuando `version >= CURRENT_SCHEMA_VERSION`
confía en el número guardado (un solo `PRAGMA user_version`, sin tocar el
schema — el camino rápido de cualquier arranque normal); en cualquier otro
caso — incluido `version == 0` — vuelve a comprobar cada paso contra el
schema real vía `PRAGMA table_info`. Una base nueva de verdad (`work_items`
no existía antes de este `create_all()`) se marca directo en
`CURRENT_SCHEMA_VERSION`, sin correr nada.

**Corrección encontrada antes de escribir ningún test, no en producción:**
el primer diseño asumía que una base legacy sólo podía tener aplicado un
*prefijo* de `STEPS` (los primeros N, nunca uno del medio faltando). Es
falso: `test_the_backfill_reaches_rows_identified_only_by_their_sharing_group`
(ya en `main` desde antes de P3.2) agrega `shared_component` a mano fuera de
orden para poder probar el backfill de `split_depth` de forma aislada. Bajo
la asunción de prefijo, `pending_steps` habría intentado reaplicar
`ALTER TABLE ... ADD COLUMN shared_component`, ya existente →
`duplicate column name`. Corregido antes de implementar nada más: cada paso
se comprueba de forma independiente (`MigrationStep.satisfied`), nunca se
confía en que "versión N" implica "los primeros N pasos y sólo esos". Esto
también deja el sistema seguro ante un crash a mitad de una migración: una
base que murió en el paso 4 de 6 es, para `pending_steps`, indistinguible de
una legacy — la próxima corrida vuelve a verificar cada paso contra la
realidad en vez de confiar ciegamente en el número que quedó escrito. Al
terminar de aplicar los pasos pendientes, `upgrade()` fija
`user_version = CURRENT_SCHEMA_VERSION` explícitamente en vez de confiar en
el número que dejó el último paso de la lista `pending`: si un paso tardío
(p. ej. la columna 6) ya estaba satisfecho y por eso no formaba parte de
`pending`, el último paso realmente aplicado puede tener una versión menor
a `CURRENT_SCHEMA_VERSION` — sin este stamp final la base quedaría
reportándose por debajo de su versión real.

**`satisfied()` verifica la postcondición de datos, no sólo la columna, en
pasos con backfill.** Una columna presente sin su backfill aplicado (un
`ALTER TABLE` a mano, o una corrida pre-P3.2 cuyo `ALTER` comiteó sin su
`UPDATE`) leía como "satisfecho" mirando sólo `PRAGMA table_info` —
saltándose el backfill para siempre, porque `pending_steps` nunca lo
volvería a intentar. `MigrationStep.postcondition` es el mismo predicado
`WHERE` del backfill expresado como `SELECT COUNT(*)`: satisfecho exige
columna presente **y** ese conteo en cero. Si el predicado referencia una
columna que otro paso todavía-pendiente es responsable de agregar (p. ej.
`shared_component` para el backfill de `split_depth`), `satisfied()` atrapa
el `OperationalError` resultante y lo trata como "no satisfecho" en vez de
propagar el error. `apply()` se volvió idempotente en simetría: si la
columna ya existe, saltea el `ALTER` (que fallaría con
`duplicate column name`) y va directo al backfill.
`test_bootstrap_still_backfills_split_depth_when_the_column_exists_without_its_data`
y `test_out_of_order_legacy_steps_still_reach_current_version`
(`backend/tests/test_schema_migration.py`) cubren ambas correcciones.

**Rechazo duro si `user_version > CURRENT_SCHEMA_VERSION`:** chequeo de sólo
lectura antes de **cualquier** mutación — incluido `Base.metadata.create_all()`
y la primera conexión SQLAlchemy misma. `upgrade()` abre la base con
`sqlite3` puro (no el engine) para leer `existed_before`/`user_version`,
precisamente porque la primera conexión del engine dispara
`PRAGMA journal_mode=WAL` en `_configure_sqlite_connection` — una escritura
real si la base todavía no estaba en WAL. Sólo después de que
`reject_if_too_new` aprueba la base se toca el engine, se corre
`create_all()` (que crearía de nuevo una tabla que un schema más nuevo
legítimamente borró o renombró) y se aplican pasos. Una versión vieja del
código no debe adivinar, crear ni tocar nada de una base que una versión más
nueva ya migró más allá de lo que este código conoce.
`test_a_too_new_database_missing_a_table_gets_zero_mutation` prueba
exactamente esta combinación: base demasiado nueva y con una tabla
faltante — la tabla debe seguir faltando después del rechazo.

### Backup y lock derivados del path real de cada base, nunca globales

Esta app ya corre varias bases SQLite reales en paralelo (la default, una
por suite de benchmark, una por test vía `tmp_path`) — un directorio de
backups o un lock compartido mezclaría backups de bases distintas y haría
que migrar la base de un test bloqueara, sin necesidad, a la base
principal. En cambio, dado el path resuelto de una base
(`.../algo/agentarium.db`), tanto el backup (`.../algo/backups/`) como el
lock (`.../algo/agentarium.db.lock`) se derivan de ese mismo path, como
siblings. `test_migration_backup.py` prueba explícitamente que dos bases
con `database_url` distintos no colisionan.

`FileLock` (ya dependencia del proyecto, mismo patrón que
`GitWorktreeIsolation._project_lock`) coordina procesos Agentarium, pero no
reemplaza la garantía transaccional de SQLite: hay una ventana real donde un
proceso lee la versión, espera el lock mientras otro migra, y sólo después
lo consigue. Por eso la versión se relee **después** de adquirir el lock, y
cada paso corre en su propia transacción explícita
(`BEGIN`/`ALTER`+backfill/`PRAGMA user_version=N`/`COMMIT`) sobre una
conexión `sqlite3` cruda con `isolation_level=None` — no la conexión
SQLAlchemy compartida, cuyo autobegin implícito con pysqlite es exactamente
el patrón que la documentación de SQLAlchemy señala como no confiable para
DDL transaccional real. `test_migration_backup.py` prueba la regresión
concurrente (dos hilos, misma base legacy, arrancando lo más simultáneo
posible): nunca `duplicate column name`, resultado idéntico a una corrida
secuencial.

Backup: `VACUUM INTO` condicional, sólo si la base ya existía y quedó algo
pendiente tras la reconciliación — nunca en `ensure_ready()` (rompería su
contrato de ser barato en casi toda invocación de CLI). Se valida
inmediatamente después (`PRAGMA integrity_check` + que las tablas de
referencia sean consultables) antes de confiar en él; si falla, se aborta
todo sin tocar la base real.

**Corrección de revisión: la validación del backup no compara contra el
estado posterior de la fuente.** El diseño original comparaba el conteo de
filas del backup contra un `SELECT COUNT(*)` de la fuente leído *después*
de `VACUUM INTO`. `VACUUM INTO` toma una foto fija en un instante; la
fuente sigue viva y mutable (otro proceso Agentarium, un request que la API
sigue procesando) — un write en esa ventana hace que un backup perfectamente
válido se vea "inválido" sin que tenga nada de malo. `_backup_tables_are_queryable`
reemplaza esa comparación por una validación autocontenida: sólo mira el
propio archivo de backup (las tablas de referencia existen y son
consultables), nunca la fuente. `test_a_concurrent_write_after_the_backup_snapshot_does_not_invalidate_it`
inyecta un write a la fuente en esa ventana exacta y confirma que el backup
se sigue aceptando.

**Bug real encontrado corriendo los tests, no asumido:** la validación
original asumía que `PRAGMA integrity_check` siempre devuelve una fila. Un
archivo que directamente no es una base SQLite (probado con
`test_invalid_backup_aborts_before_touching_the_real_database` y
`test_restore_refuses_an_invalid_backup_without_touching_the_live_database`)
hace que `sqlite3` levante `DatabaseError` en el `execute`, no que devuelva
una fila "corrupta". `_integrity_ok` atrapa ese error y lo trata como
inválido — encontrado por el primer test de restore fallando con un
traceback crudo en vez del `RestoreError` esperado.

### Restauración: operación real, no "copiar el archivo encima"

`agentarium db restore <backup>`: adquiere el mismo lock que usa
`upgrade()`; valida el backup (`integrity_check` + apertura real) antes de
tocar nada; confirma que ninguna otra conexión sigue usando la base activa
(ver más abajo); mueve la base actual a `<archivo>.failed-<timestamp>` en
vez de borrarla o pisarla; borra los sidecars `-wal`/`-shm` que puedan
quedar junto al path en vivo (un `VACUUM INTO` no los genera, pero la base
reemplazada sí pudo haberlos dejado, y conservarlos junto al archivo
restaurado arriesga que SQLite intente reproducir un WAL que no
corresponde); copia el backup validado; y vuelve a correr `integrity_check`
sobre el resultado antes de reportar éxito. `_service()`/`init` capturan
`MigrationFailedError`/`SchemaTooNewError`/`BackupValidationError` e
imprimen a stderr con salida no cero (códigos 6/7/9) en vez de un traceback
crudo; el mensaje de `MigrationFailedError` nombra el path exacto del
backup y el comando exacto de restauración.

**Corrección de revisión: el `FileLock` no prueba que ninguna conexión siga
usando la base.** El diseño original trataba adquirir el lock de migración
como "prueba" de que ningún otro proceso Agentarium tenía la base abierta.
Es falso: ese lock sólo serializa llamadas a `upgrade()`/`restore()` entre
sí. Un proceso ya corriendo (la API, un `agentarium project run`) que abrió
la base por el camino normal y nunca cerró su conexión no está, en ningún
momento posterior, esperando ese lock — `restore()` lo conseguiría de
inmediato y borraría `-wal`/`-shm`/copiaría el archivo debajo de una
conexión viva. `_ensure_no_active_connection` es la comprobación offline
mínima disponible sin cooperación del otro proceso: reescribe
`PRAGMA user_version` a su propio valor actual (una escritura real pero un
no-op semántico) para garantizar un frame de WAL fresco, y corre
`PRAGMA wal_checkpoint(TRUNCATE)`. Un checkpoint TRUNCATE sólo puede
completar del todo — reduciendo el WAL a cero frames — cuando ninguna otra
conexión sostiene una foto de lectura abierta ni un lock de escritura;
verificado empíricamente antes de confiar en el diseño: un lector con una
transacción abierta, incluso uno que no leyó nada nuevo desde una escritura
posterior, hace que el checkpoint vuelva con `busy=1` y 0 frames
checkpointeados de forma reproducible. Si `db_path` no es siquiera una base
SQLite válida (el escenario que `restore()` existe para recuperar), no hay
ninguna conexión WAL posible que proteger — ese caso se trata como seguro,
distinto de un `OperationalError` genuino (base bloqueada), que sigue
rechazando. `test_restore_refuses_when_another_connection_still_has_the_database_open`
prueba el caso real: una conexión `sqlite3` activa en modo WAL hace que
`restore()` se niegue sin tocar ningún archivo.

## Consecuencias

- `test_schema_migration.py` (ya en `main` desde antes de P3.2) sigue
  pasando sin cambiar ninguna de sus aserciones — sólo cambió el mecanismo
  interno que `Database.create_all()` delega, no su contrato público. Se le
  agregó una prueba con las 11 tablas en su forma original de `fd83772`
  para confirmar que las 10 que nunca cambiaron quedan intactas.
- Poda automática de backups viejos queda fuera de alcance a propósito — se
  conservan todos por default.
- El runbook Windows (`docs/guides/database-migrations-windows.md`) da por
  sentado este mecanismo; no describe pasos manuales alternativos.
- Reabrir esta decisión si aparece un rename/drop de columna o un segundo
  dialecto de base de datos (ver disparador arriba).
- **Ronda de revisión posterior a la primera versión de este PR** encontró
  y corrigió cuatro gaps antes de mergear, todos ya reflejados arriba y
  cubiertos por test: mutación (incluido `create_all()`) posible antes de
  `reject_if_too_new`; `satisfied()` mirando sólo presencia de columna en
  pasos con backfill en vez de la postcondición de datos; `restore()`
  tratando el `FileLock` como prueba de ausencia de conexiones activas; y
  una comparación de conteo de filas con TOCTOU contra el estado posterior
  de la fuente. Ninguno alteró el diseño de fondo (`user_version` + lista
  de pasos, backup por-path, restore real) — todos eran gaps de rigor en la
  primera implementación, no en la decisión misma.
