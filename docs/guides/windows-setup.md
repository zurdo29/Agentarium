# Instalación y diagnóstico en Windows

Qué comprueba `agentarium doctor`, cómo leer su salida, y qué hacer con
los fallos frecuentes de esta plataforma que no tienen mensaje propio en
otro lado. Diseño completo en
[ADR 0039](../decisions/0039-windows-onboarding-diagnostics.md).

## Instalación normal

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

`setup.ps1` instala dependencias y, al final, corre `agentarium doctor`
automáticamente -- su resultado es informativo, nunca aborta una
instalación que ya terminó bien. Si termina con una advertencia, seguí
leyendo acá.

## `agentarium doctor`

```powershell
.\.venv\Scripts\agentarium.exe doctor
```

Imprime una línea por comprobación: `[OK]`, `[WARN]` o `[FAIL]`, con una
segunda línea `-> ...` cuando hay una acción concreta que tomar. Sale con
código distinto de cero sólo si algo quedó en `[FAIL]` -- un `[WARN]`
nunca corta el comando.

Comprueba, entre otras cosas:

- `git`/`node`/`npm` (obligatorios -- son los requisitos comprobados del
  README) y `ollama_client` (sólo obligatorio si Ollama es el proveedor
  activo).
- El proveedor y modelo activos (`provider_config`) -- un proveedor
  distinto de `mock` sin modelo configurado es un `[FAIL]` acá, antes de
  que se convierta en un traceback en el primer comando real.
- Alcanzabilidad y modelos disponibles de Ollama/el servidor compatible
  con OpenAI -- el proveedor que no estás usando sólo da `[WARN]`, nunca
  `[FAIL]`.
- Longitud de `AGENTARIUM_WORKSPACE_ROOT` (ver más abajo) y permisos de
  escritura reales sobre el workspace y la carpeta temporal del sistema.

## Si `git worktree add` falla con `fatal: '$GIT_DIR' too big`

Pasa cuando `AGENTARIUM_WORKSPACE_ROOT` apunta a una ruta profunda en
Windows. `core.longpaths` (ya configurado en todo el repo) sólo cubre el
checkout del árbol de trabajo -- **no** cubre la contabilidad interna de
git para worktrees (`.git/worktrees/<nombre>/gitdir`), que tiene su
propio límite bastante más chico. Detalle completo en
[ADR 0022](../decisions/0022-concurrency-and-state-consistency.md).

`agentarium doctor` ya advierte esto de forma proactiva
(`workspace_path` en `[WARN]`) antes de que pase; si igual ocurre durante
una corrida real, el evento `workspace_action_rejected` del proyecto
ahora incluye el mismo mensaje.

**Fix**: configurá `AGENTARIUM_WORKSPACE_ROOT` a una ruta corta, por
ejemplo:

```powershell
$env:AGENTARIUM_WORKSPACE_ROOT = "C:\agentarium-ws"
```

## Si `.\test.ps1` falla con decenas de `PermissionError: [WinError 5]`

Síntoma: la suite de pytest falla en el *setup* de casi todos los tests
que usan el fixture `tmp_path`, con
`Acceso denegado: 'C:\Users\<usuario>\AppData\Local\Temp\pytest-of-<usuario>'`.
No está relacionado a ningún cambio de código -- la carpeta base que
pytest usa para `tmp_path` quedó en un estado que ni siquiera deja leer
sus propios permisos (causa exacta no confirmada: posible bloqueo de
otro proceso o antivirus). `icacls` sobre esa carpeta también falla.

**Fix**: no tocar ni borrar esa carpeta (podría pertenecer a otro
proceso). En cambio, redirigir `TMP`/`TEMP` a una carpeta nueva y corta
antes de correr `test.ps1`:

```powershell
$env:TMP = "C:\Users\<usuario>\pytmp"
$env:TEMP = "C:\Users\<usuario>\pytmp"
.\test.ps1
```

(creá esa carpeta primero si no existe).

`agentarium doctor` comprueba escritura real sobre la raíz temporal del
sistema (`temp_write`), pero **eso no garantiza detectar este síntoma
específico**: el problema está en la subcarpeta `pytest-of-<usuario>`,
que puede tener un ACL rota o heredada aunque la raíz temporal en sí siga
siendo perfectamente escribible -- `temp_write` crea y borra un archivo
directo en la raíz, nunca dentro de esa subcarpeta en particular. Si
`doctor` da `[OK]` en `temp_write` pero igual ves este error al correr
`test.ps1`, el fix reactivo de arriba (redirigir `TMP`/`TEMP`) sigue
siendo el camino -- no hay comprobación proactiva confiable para este
caso puntual todavía.

## Si `npm test` se cuelga sin avisar

Síntoma: el build de `npm test` termina, algunos archivos de test
imprimen resultado, y después no pasa nada más -- sin error, sin
timeout. Confirmá que es esto (no que está "lento") revisando el CPU de
los procesos `node` dos veces con ~15 segundos de diferencia; si no se
mueve, es esto.

Causa: cada archivo de test corre en su propio proceso y levanta su
propio servidor Vite (`tests/support/dom-setup.mjs`); con
`node_modules/.vite` en frío, varios procesos compiten por el mismo
directorio de cache y dejan carpetas `deps_temp_*` huérfanas de una
corrida anterior que nunca se cerró bien.

**Fix**: identificá los procesos `node` realmente relacionados con esta
corrida antes de tocar nada -- **no mates todos los procesos `node.exe`
de la máquina**, podés cortar otro proyecto que esté corriendo en
paralelo:

```powershell
# 1. Listá los procesos node con su línea de comando completa
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Select-Object ProcessId, CreationDate, CommandLine |
    Format-Table -AutoSize

# 2. Los que pertenecen a la corrida colgada arrancaron cuando corriste
#    `npm test` y siguen vivos con CPU ~0% (confirmalo):
#    Get-Process -Id <PID> | Select-Object CPU
#    (repetir ~15s después; si no cambió, está colgado)

# 3. Cerrá sólo esos PIDs
Stop-Process -Id <PID1>,<PID2> -Force
```

Después, borrá el cache huérfano y reintentá:

```powershell
Remove-Item -Recurse -Force node_modules\.vite\deps_temp_*
npm test
```

## Códigos de salida de `agentarium doctor`

| Código | Causa                                          |
| ------ | ----------------------------------------------- |
| 0      | Todas las comprobaciones en `OK` o `WARN`        |
| 12     | Al menos una comprobación quedó en `FAIL`        |
