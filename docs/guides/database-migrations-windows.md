# Migraciones y backup de SQLite (Windows)

Qué puede pasar al correr `agentarium init` (o arrancar la API) sobre una
base SQLite existente, y cómo recuperarse. Diseño completo en
[ADR 0032](../decisions/0032-versioned-sqlite-migrations-and-backup.md).

## Arranque normal

`agentarium init` migra la base automáticamente si hace falta. Si no hay
nada pendiente, no hace nada extra — es seguro correrlo en cada arranque.

## Si el arranque migra una base existente

Antes de tocar el schema, crea un backup validado junto a la base:

```
<carpeta-de-la-base>\backups\agentarium-pre-v<N>-<timestamp>.db
```

Se conservan todos; no hay poda automática. Borralos a mano cuando ya no
los necesites.

## Si la migración falla a mitad de camino

`agentarium init` termina con salida distinta de cero y un mensaje que
nombra el backup exacto. Primero reintentá — la migración es idempotente y
esto alcanza si la causa fue transitoria (disco lleno, permiso):

```powershell
agentarium init
```

Si eso no alcanza, restaurá el backup que indicó el mensaje de error:

```powershell
agentarium db restore "<carpeta-de-la-base>\backups\agentarium-pre-v<N>-<timestamp>.db"
```

`db restore`:

- adquiere el mismo lock que usa el arranque normal (evita que dos
  `restore`/`init` corran a la vez sobre la misma base);
- valida el backup antes de tocar nada;
- confirma que ninguna otra conexión siga usando la base activa -- **no** es
  el lock lo que garantiza esto (ver ADR 0032): cerrar sólo la terminal
  donde corriste `agentarium init` o `agentarium project run` no alcanza si
  el proceso de la API sigue corriendo en otro lado con la base abierta;
- conserva la base reemplazada como `<archivo>.failed-<timestamp>` — nunca
  la borra;
- limpia `-wal`/`-shm` viejos junto al archivo restaurado, para que SQLite
  no intente reproducir un WAL que no corresponde a la base nueva;
- vuelve a validar la base restaurada antes de reportar éxito.

Si `db restore` en sí falla, no toca la base activa. Motivos posibles:
backup inválido; el lock ocupado (otro `init`/`restore` corriendo en ese
instante); o una conexión activa detectada (la API, un
`agentarium project run`, u otro proceso con la base abierta) -- cerrá ese
proceso y reintentá.

## "La base de datos está en user_version=N, más nueva..."

Este código instalado es más viejo que la base. Actualizá Agentarium a una
versión que conozca `user_version=N` antes de abrir esta base — no se
escribió nada, es seguro reintentar después de actualizar.

## Códigos de salida (`init` y cualquier comando que toque la base)

| Código | Causa                                                          |
| ------ | --------------------------------------------------------------- |
| 6      | Migración falló a mitad de camino (backup nombrado en el mensaje) |
| 7      | La base es más nueva que este código instalado                  |
| 9      | El backup recién generado no pasó su propia validación          |
| 8      | `agentarium db restore` falló (backup inválido o lock ocupado)  |

## Ubicación por defecto

`runtime\agentarium.db`, relativo a la raíz del proyecto; configurable con
`AGENTARIUM_DATABASE_URL`. Backup y lock quedan siempre junto a la base
activa, nunca en una carpeta global: una base por suite de benchmark o por
`AGENTARIUM_DATABASE_URL` distinto tiene su propio `backups\` y su propio
`.lock`, sin interferir entre sí.
