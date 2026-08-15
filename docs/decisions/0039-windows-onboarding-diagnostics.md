# ADR 0039: Onboarding Windows -- diagnóstico real y mensajes accionables (P4.5)

- Estado: aceptada
- Fecha: 2026-08-14

## Contexto

PLANS.md P4.5, último punto de P4: "Setup reproducible. Comprobación de
Ollama/modelos/configuración. Mensajes accionables para fallos frecuentes
de permisos/temp/worktree." A diferencia de P4.1-P4.4, no tiene una mitad
backend/frontend separable -- es enteramente backend/CLI/tooling/docs, así
que va en un solo PR.

La motivación es concreta: esta misma sesión (y sesiones previas)
tropezó tres veces con fallos de Windows que no dejaban rastro permanente
en el repo -- un permiso roto en `%TEMP%\pytest-of-<usuario>` que ya
"vivía sólo en contexto de sesión, se perdió" dos veces según memoria
previa; una carrera de caché de Vite que cuelga `npm test` sin ningún
timeout visible; y `git worktree add` fallando con
`fatal: '$GIT_DIR' too big` cuando `AGENTARIUM_WORKSPACE_ROOT` apunta a
una ruta profunda -- reproducido en verificación manual real de P4.2, no
sólo en teoría. `agentarium doctor` ya existía, pero no servía para
diagnosticar nada de esto.

## Decisión

### `agentarium doctor` deja de ser un volcado de JSON que nunca falla

Estado previo, verificado leyendo el código antes de tocarlo: `doctor`
reportaba `"workspace": str(project_root())` -- la raíz del repositorio,
**no** `settings.workspace_root` -- exactamente el valor que importa para
diagnosticar rutas de workspace profundas. Tenía su propia
`_ollama_status()`, una llamada `httpx` a mano que sólo devolvía
`"available"`/`"http_{code}"`/`"unavailable"`, divergente del
`ProviderRegistry` real que ya usa la web (`GET /api/providers`) y que sí
trae la lista de modelos efectivamente descargados. Siempre salía con
código 0, sin importar qué tan roto estuviera el entorno. Cero tests.

Rediseño: cada comprobación produce `pass`/`warn`/`fail` con una línea de
guía concreta cuando corresponde, y el comando sale con código
`_EXIT_DOCTOR_FAILED = 12` si alguna quedó en `fail`.

- **`git`/`node`/`npm` son obligatorios** (`fail` si faltan -- son los
  requisitos comprobados del propio README). **`ollama_client` sólo es
  obligatorio cuando Ollama es el proveedor activo** -- ausente cuando no
  se usa no es ni siquiera una advertencia. **Node además se valida por
  versión, no sólo por presencia** (`_node_check`, corrección de
  revisión): el README exige 22.13+, y una versión presente pero vieja
  falla en formas que no se parecen en nada a "Node no está instalado" --
  `setup.ps1` gana el mismo gate antes de `npm ci`, en el mismo lugar
  donde ya rechazaba un Python viejo.
- **Proveedor y modelo activos** (`provider_config`) replican
  exactamente la precedencia que ya usa `build_application()`
  (`ProviderSelectionStore` guardada, si no `settings.provider`/`model`)
  -- pero **sin pasar por `build_application()`/`RoleCatalog`**:
  `RoleCatalog.select_provider()` levanta un `ValueError` sin capturar
  cuando el proveedor no es `mock` y no hay modelo, y `doctor` tiene que
  seguir funcionando para diagnosticar exactamente esa configuración
  rota, no heredar su crash.
- **Alcanzabilidad y modelos reales** vía `ProviderRegistry.diagnostics()`
  (la misma fuente que ya usa la web) -- si el proveedor no es el activo,
  no alcanzable es sólo `warn`, nunca `fail`: no tiene sentido bloquear el
  diagnóstico por un proveedor que el usuario no eligió. **Cuando el
  proveedor activo está listo pero el modelo configurado no aparece en
  `diagnostic.models`, tanto el `detail` como el `hint` nombran ese
  modelo** (corrección de revisión -- antes el `detail` era el mensaje
  genérico del registro, que no dice cuál modelo falta): el hint sugiere
  `ollama pull <modelo>` (o elegir uno ya instalado) para Ollama, y elegir
  uno de los IDs publicados para el servidor compatible con OpenAI --
  nunca la sugerencia de `ollama pull` cruzada al proveedor equivocado.
  `mock` queda explícitamente afuera de esta comprobación: no tiene
  semántica de modelos "descargados", así que un `AGENTARIUM_MODEL`
  residual en el entorno no puede disparar un falso `fail` ahí.
- **Longitud de `workspace_root`**: `warn` por encima de 100 caracteres.
  El umbral no es arbitrario -- ADR 0022 midió 194 caracteres funcionando
  y 230 fallando para la contabilidad interna de worktrees de git; 100 en
  la raíz deja el total real en ~169, con margen bajo 194.
- **Escritura real** sobre `workspace_root` y la *raíz* temporal del
  sistema (`crear archivo marcador → borrar`, nunca sólo mirar permisos)
  -- comprobación determinista, no sólo el heurístico de longitud de
  path. **Límite real, no cosmético**: `temp_write` prueba la raíz
  (`tempfile.gettempdir()`), no la subcarpeta específica
  `pytest-of-<usuario>` que es la que realmente queda con ACL rota en el
  síntoma documentado en `docs/guides/windows-setup.md` -- esa subcarpeta
  puede estar bloqueada aunque la raíz sea perfectamente escribible, así
  que un `[OK]` en `temp_write` **no** descarta ese síntoma específico.
  Ver "Limitaciones".

**`doctor` construye `Settings()` directo, nunca `get_settings()`.**
`get_settings()` es `@lru_cache` y llama `ensure_directories()` (mkdir de
workspace/DB/carpeta de selección de proveedor) -- si eso falla, `doctor`
explotaría antes de diagnosticar nada, exactamente lo opuesto de su
propósito. El mkdir que antes hacía `ensure_directories()` para el
workspace ahora vive dentro de la propia comprobación de escritura,
envuelto en su try/except -- un workspace que todavía no existe pero cuyo
padre sí es escribible da `pass` (se crea al vuelo, caso típico de
primera instalación), uno realmente sin permiso da `fail` limpio, nunca
una excepción sin capturar. Regresión dedicada para el primer caso.

### Mensaje accionable para `'$GIT_DIR' too big`

`GitWorktreeIsolation._run()` ya incluía el stderr real de git en su
`IsolationError`, pero sin traducir -- un usuario tenía que llegar hasta
el evento crudo `workspace_action_rejected` del proyecto para verlo, y
aun así el mensaje era críptico. Se agrega (nunca se reemplaza) una
segunda línea cuando el stderr contiene el substring literal
`'$GIT_DIR' too big`, nombrando la causa real y el fix
(`AGENTARIUM_WORKSPACE_ROOT` más corto). Se mantiene como
`IsolationError` -- verificado directamente que
`_candidate_failure_policy` (`orchestration/engine.py`) despacha
exclusivamente por `isinstance(exc, IsolationError)`, así que cambiar
sólo el mensaje no afecta retry/split ni ningún exception handler de
`api/app.py` (ninguno registra `IsolationError` ahí).

### `setup.ps1` corre `doctor` al final, sin abortar la instalación

Captura el exit code explícito (`$ErrorActionPreference` temporal a
`Continue` alrededor de la llamada, restaurado después) en vez de confiar
en el comportamiento por defecto de un ejecutable nativo con código
distinto de cero, que difiere entre versiones/hosts de PowerShell. Un
`doctor` en `FAIL` recién instalado (ej. proveedor mal configurado,
Ollama sin modelos) es esperable y **nunca** debe invalidar una
instalación que ya funcionó -- Ollama ya es opcional según el propio
README.

**Bug real encontrado en la verificación manual, no por `test.ps1`** (que
no ejecuta ningún `.ps1` -- estos scripts no tienen cobertura
automatizada en este repo, sólo verificación manual): la primera versión
capturaba `$DoctorExitCode` pero nunca reseteaba `$LASTEXITCODE` después
-- `Write-Host` no lo toca, así que el código de `doctor` (12) quedaba
siendo el último valor de `$LASTEXITCODE` visto por PowerShell, y
`setup.ps1` invocado como `& .\setup.ps1` terminaba heredando ese mismo
código como propio al llegar al final del script, exactamente lo que esta
sección existe para evitar. Confirmado en vivo forzando un `doctor` en
`FAIL` real: `setup.ps1` salía con código 12, no 0. Corregido con
`$global:LASTEXITCODE = 0` explícito después de capturar el código de
`doctor`, más un `exit 0` al final del script como garantía adicional.
Reverificado en vivo tras el fix: mismo `doctor` en `FAIL`, `setup.ps1`
ahora sale en 0 con la advertencia visible.

El script sigue terminando en éxito (exit 0) con una
`Write-Warning` visible si `doctor` encontró algo.

### Documentación nueva y una corrección real en la existente

`docs/guides/windows-setup.md` (nuevo) documenta, por primera vez en el
repo y no sólo en memoria de sesión, el fix de `$env:TMP`/`$env:TEMP`
para el permiso roto de pytest, y el procedimiento seguro para el cuelgue
de `npm test` -- identificar los procesos `node` de la corrida real
(arrancaron con `npm test`, CPU ~0% sostenido) antes de terminarlos, en
vez de recomendar matar todos los procesos `node.exe` de la máquina (eso
puede cortar otro proyecto corriendo en paralelo).

`docs/guides/ollama.md` tenía un paso genuinamente incorrecto: "Cambia
`provider` y `model` en `configs/roles/default.yaml`" no tiene ningún
efecto -- `build_application()` sobreescribe esos valores en cada
arranque desde `ProviderSelectionStore`/variables de entorno,
verificado leyendo el código antes de asumirlo. Corregido a apuntar al
panel de la interfaz o a `AGENTARIUM_PROVIDER`/`AGENTARIUM_MODEL`.

## Garantía real

`.\test.ps1` completo en verde: **545 passed + 1 skipped** (backend, +24
sobre la base de P4.4b, incluida la ronda de corrección de revisión) +
**46/46** (web, sin cambio -- P4.5 no toca ningún archivo de `app/`).
Ruff/MyPy limpios. `test_cli_doctor.py` cubre funciones puras sin red
(incluidas las 5 nuevas de `_node_check` que fijan el límite exacto
22.12.0=`fail`/22.13.0=`pass`, y las dos que fortalecen el mensaje de
modelo faltante para Ollama/OpenAI-compatible) más dos pruebas
`CliRunner` de punta a punta con URLs de proveedor apuntando a un puerto
cerrado local (sin depender de un Ollama real ni de timeouts); una
regresión nueva en `test_git_worktree_isolation.py` fuerza el stderr
exacto de git y confirma que el mensaje resultante nombra
`AGENTARIUM_WORKSPACE_ROOT`.

Verificación manual en esta máquina, contra el `provider-selection.json`
real (no un fixture): `doctor` leyó correctamente la selección guardada
real (`ollama`/`qwen2.5-coder:7b`) por encima del default `mock` de
variables de entorno, marcó `ollama` en `FAIL` porque el servidor
realmente no responde en este equipo ahora mismo, y `openai_compatible`
en `WARN` (no `FAIL`) por no ser el proveedor activo -- exactamente la
asimetría que el diseño pide. `provider_config` en `FAIL` confirmado con
un proveedor no-mock sin modelo, con el mensaje nombrando
`AGENTARIUM_MODEL`. Advertencia de ruta larga confirmada con una raíz de
110 caracteres. La comprobación de escritura confirmada creando de verdad
un workspace que no existía. `doctor` real (no un test) mostró `[OK]
node: v24.18.0` con el nuevo gate de versión activo.

El gate de Node en `setup.ps1` se verificó sin instalar una versión
vieja real: la misma expresión de comparación que usa el script
(`major -lt 22 -or (major -eq 22 -and minor -lt 13)`) se corrió aparte
contra strings literales (`21.9.0`, `22.12.0`, `22.13.0`, `22.14.0`,
`24.18.0`) y dio el resultado esperado en los 5 casos; el formato real de
`node --version` en esta máquina (`v24.18.0`) se confirmó aparte para
asegurar que `.TrimStart("v")` lo maneja bien.

El fallo real de `git worktree add` con
`'$GIT_DIR' too big` no se reprodujo de nuevo end-to-end esta sesión
(ya está cubierto por la regresión dedicada con el stderr real, y el
camino de despacho está confirmado sin cambios vía `isinstance`) -- ver
"Limitaciones".

## Limitaciones

`doctor`'s `temp_write` prueba la raíz temporal del sistema
(`tempfile.gettempdir()`), no la subcarpeta `pytest-of-<usuario>`
específica que queda con ACL rota en el síntoma que
`docs/guides/windows-setup.md` documenta -- esa subcarpeta puede estar
bloqueada de forma completamente independiente de que la raíz sea
escribible. Un `[OK]` en `temp_write` no descarta ese síntoma; la guía
sigue documentando únicamente el fix reactivo (redirigir `TMP`/`TEMP`
antes de correr `test.ps1`), no una detección proactiva confiable para
este caso puntual.

El fallo real de `git worktree add` con `'$GIT_DIR' too big` no se
reprodujo end-to-end contra un proyecto real en esta sesión (requeriría
crear y correr un proyecto completo contra una ruta de workspace
deliberadamente profunda) -- la cobertura viene de la regresión
determinista con el stderr real más la confirmación directa de que
`_candidate_failure_policy` no cambia de comportamiento.

Deliberadamente fuera de alcance, no descuido: el bug de codificación
UTF-8 en `agentarium benchmark run` con stdout redirigido sigue en
"Backlog consciente -- no empezar ahora" de PLANS.md, no es parte de los
tres bullets de P4.5. El `ValueError` sin capturar de
`RoleCatalog.select_provider()` (proveedor no-mock sin modelo) queda
detectado y advertido proactivamente por `doctor`, pero no corregido en
su origen -- tocar `RoleCatalog`/`build_application()` es un cambio más
grande del que esta fase, de polish/onboarding, necesita.

## Qué queda para P5

Con P4.5 cerrado, **P4 queda completo**: importar/exportar sin arriesgar
(P4.1/P4.2), reparar (P4.3), entregar/auditar (P4.4) y ahora un onboarding
diagnosticable en Windows (P4.5). El criterio de cierre de P4 en PLANS.md
-- tomar un repo pequeño existente, pedir un cambio, revisar/reparar el
resultado y exportarlo sin que Agentarium modifique el original de forma
implícita -- está cubierto por P4.1-P4.4; P4.5 es la capa de operabilidad
encima. P5 (extensibilidad) sólo entra por una limitación real observada
del MVP, no por curiosidad técnica -- ver "Gate pre-MVP" en PLANS.md.
