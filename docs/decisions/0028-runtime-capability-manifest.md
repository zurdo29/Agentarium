# ADR 0028: Manifiesto de capacidades del runtime como datos

- Estado: aceptada
- Fecha: 2026-08-04

## Contexto

ADR 0020 agregó una frase de prompt diciéndole al worker que un `.py`
ejecutado corre en un entorno aislado, sin red ni instalación de paquetes,
sólo biblioteca estándar. Verificado en vivo, inmediatamente después: el
modelo (qwen2.5-coder:7b) volvió a importar Flask en ambos reintentos,
byte a byte idéntico al candidato rechazado. Conclusión de esa ADR: "una
aclaración de prompt no alcanza para este modelo en este dominio."

`PLANS.md` (P2) pide el siguiente rung: un manifiesto estructurado —
Python, paquetes permitidos/disponibles, ejecutables, política de red —
incluido como **datos** en el contexto del planificador y del worker, no
sólo como prosa. Dividido en dos PR: **P2.1** (este documento — el
manifiesto y su inclusión) y **P2.2** (después — preflight de
imports/comandos y `unsupported_capability`, fallo temprano). El ítem 5
original (instalación con red, entorno por proyecto) sigue fuera de
alcance indefinidamente.

**Nota importante de honestidad, dicha explícitamente para que no se lea
como una promesa implícita:** P2.1 deja la infraestructura para poder
medir después si estructurar el dato cambia algo — no hubo ninguna
corrida real todavía, así que este PR no prueba comportamiento, sólo lo
deja medible. Tampoco prueba que aplicar una consecuencia mecánica ayude
— eso es P2.2. Por la misma razón que ADR 0020 no alcanzó, no hay
garantía de que datos estructurados sin consecuencia mecánica alguna sean
suficientes; sólo es el siguiente paso razonable antes de medirlo.

## Decisión

Nuevo módulo `backend/agentarium/execution/capabilities.py`:

```python
class RuntimeCapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    python_version: str
    executables_allowed: tuple[str, ...]
    executables_available: tuple[str, ...]
    third_party_packages_allowed: tuple[str, ...] = ()
    network_policy: Literal["deny"] = "deny"
```

Construido una única vez en `build_application()` y compartido — la misma
instancia fuente se serializa por separado (`.model_dump()`) en `plan`,
`plan_revision` y `work`, nunca reconstruida para cada uno.

### Fuentes de cada campo

- `python_version`: `sys.version.split()[0]`, el intérprete real en
  ejecución — misma técnica que `benchmarks/identity.py::capture_identity`,
  ya establecida en este codebase para el mismo tipo de hecho.
- `executables_allowed`/`executables_available`: derivados de
  `commands.allow` en `configs/policies/security.yaml` (tercera lectura
  independiente del mismo archivo — `SafeCommandExecutor` y
  `WorkspaceMaterializer` ya hacen cada uno la suya; es el idioma
  establecido para este archivo, no uno nuevo). Son dos afirmaciones
  distintas a propósito: `executables_allowed` es política (qué está
  permitido); `executables_available` es el subconjunto que además
  resuelve de verdad en esta máquina vía `shutil.which()` — un ejecutable
  permitido puede no estar instalado, y el manifiesto no debe afirmar
  disponibilidad que no verificó.
- `third_party_packages_allowed`: nueva clave `packages.allowed` en
  `security.yaml`, vacía hoy. `network_policy`: nueva clave `network` en
  el mismo archivo, `deny` hoy.

### Qué significa realmente `executables_allowed` — y qué no

`commands.allow` describe qué puede invocar **el propio pipeline de
Agentarium** contra una entrega: el intérprete que corre o chequea un
`.py`/`.js` entregado, git para aislamiento. **No es, y nunca fue, un
límite sobre qué podría hacer un `subprocess`/`os.system` dentro del
script entregado.** Ese límite no existe hoy en ningún nivel. Verificado
con grep completo del backend: `run_command` aparece una sola vez, como
string literal dentro de `allowed_tools` en `Orchestrator.plan_project`
(`engine.py:202`), sin ningún despachador — no existe un loop de
tool-calling en este codebase; el worker sólo devuelve `files[]`, nunca
ejecuta nada por su cuenta. El manifiesto declara la política de
Agentarium sobre su propia entrega, no un sandbox del código que el
modelo escribe.

### Por qué no hay un campo `packages_available` (lo que de verdad está
instalado)

Ya resuelto por ADR 0020, cita textual: *"Tampoco se enumeraron en el
prompt los paquetes de terceros que sí están instalados en el venv
compartido (p. ej. FastAPI): acoplaría el contenido generado a las
dependencias internas de Agentarium, que pueden cambiar."* `pyproject.toml`
real: fastapi, filelock, httpx, pydantic, pydantic-settings, PyYAML,
sqlalchemy, typer, uvicorn — todos importables hoy en el camino no aislado
de `validation.py` (ver más abajo), por accidente de implementación, nunca
una garantía deliberada. `third_party_packages_allowed` declara sólo
política, jamás lo que resulta estar instalado por otra razón.

### `network_policy: Literal["deny"]`, no `network: str`

Mismo patrón que `BenchmarkRunRecord.schema_version: Literal[2]`
("pinned, not merely defaulted"). Además refuerza la regla de `AGENTS.md`
de no ampliar acceso de red sin aprobación explícita: ampliar la política
más adelante exige tocar el tipo en código (visible en revisión), no sólo
editar una línea de YAML sin que nadie lo note. `"deny"` en vez de
`"none"` a propósito: "none" sonaba a afirmación de aislamiento efectivo;
"deny" es la política declarada, sin afirmar cumplimiento técnico (ver
siguiente sección).

### Límite conocido, deliberado: declarado no es lo mismo que aplicado

Ni `third_party_packages_allowed=[]` ni `network_policy="deny"` están
hoy mecánicamente forzados en el camino real (no de benchmark) de
`SCRIPT_EXECUTION`. `execution/validation.py` invoca un `.py` entregado
como `[sys.executable, target.name, *args]` — **sin** las flags
`-E -s -S -B` que `benchmarks/functional.py::run_functional_check` sí usa
para sacar site-packages de `sys.path`. Un script entregado en el camino
real puede hoy `import fastapi` con éxito.

Sobre la red, la limitación es más profunda: ni siquiera las flags de
aislamiento que ya existen la cubren. Cita textual del docstring de
`benchmarks/functional.py` (líneas 8-14), que ya lo dice en lenguaje más
fuerte del que se había usado hasta ahora: *"It is not an OS-level
sandbox — the delivered script still runs as this user and could reach
absolute paths, the network or spawn processes. Nothing here adds
isolation, and nothing here should be described as if it did."* Las flags
`-E -s -S -B` bloquean imports de terceros; no tocan `socket` ni
`urllib.request`, ambos de la biblioteca estándar.

**Se decide, a propósito, no cerrar esta brecha en P2.1.** Mezclar
"declarar el dato" con "hacer cumplir el dato" en el mismo cambio
imposibilitaría medir después, con una corrida real, si un cambio de
comportamiento del modelo vino de que estructuró mejor la información (la
hipótesis que P2.1 prueba) o de que el validador empezó a rechazar algo
que antes aceptaba (un mecanismo distinto). P2.1 no crea esta brecha —
la frase de prompt de ADR 0020, ya en producción, hacía la misma
afirmación falsa; P2.1 reestructura una afirmación que ya era falsa, no
vuelve falsa una que era cierta. Queda como su propio ítem futuro,
distinto de P2.2 (que es preflight + `unsupported_capability`, no esto):
hacer que `SCRIPT_EXECUTION` cumpla mecánicamente lo que el manifiesto
declara.

### Choque de nombre con `RuntimeCapabilities` (ADR 0004)

`backend/agentarium/api/schemas.py` ya define `class RuntimeCapabilities`
— el contrato de ADR 0004 (modo del runtime: `simulation`/
`artifact_only`/`workspace`, booleans de `model_inference`/
`project_files`/etc., expuesto a la interfaz). Concepto no relacionado:
estado del orquestador para la UI, no datos para el LLM. Para evitar
confundir grep e imports con el mismo nombre corto para dos cosas
distintas, la clase de esta ADR se llama **`RuntimeCapabilityManifest`**.

### Dónde se incluye

`ContextBuilder.__init__` gana `capabilities: RuntimeCapabilityManifest`
como parámetro requerido (sin default — `repository`, el parámetro
hermano, tampoco lo tiene; un default acá sería una segunda fuente de
verdad silenciosa compitiendo con `security.yaml`). `operational()`
(payload de `work`) y `Orchestrator.plan_project`/la revisión de plan
(payload de `plan` y `plan_revision`, vía `self.memory.capabilities` — la
misma instancia fuente, serializada por separado en cada payload con
`.model_dump(mode="json")`, no tres copias independientes que podrían
divergir) ganan la clave `runtime_capabilities`. **No** se agrega a
`brief` (alcance/entregables, nunca enfoque de implementación) ni a
`decompose` (reparación tras agotar intentos, no nombrado en el ítem 2 de
`PLANS.md` — agregarlo sería alcance de más para este PR).

Las frases de prompt existentes de ADR 0020 (`_OPERATION_INSTRUCTIONS["work"]`)
se **reescriben** para apoyarse en `SOLICITUD.payload.runtime_capabilities`
en vez de mantenerse al lado de una prosa nueva — mantener las dos
duplicaría la misma afirmación sin poder atribuir después un cambio de
comportamiento a una causa concreta, y tiene costo real de tokens (ADR
0014: el worker ya está acotado a 6000 tokens de salida por
truncamiento). Se agrega una frase nueva a `_OPERATION_INSTRUCTIONS["plan"]`,
que hoy no dice nada de esto. `PLANNING_PROMPT_VERSION`,
`WORKSPACE_PROMPT_VERSION` y `PLAN_REVISION_PROMPT_VERSION` suben — ADR
0003: "los cambios de contrato requieren una nueva versión de prompt y
fixtures", y un payload nuevo es cambio de contrato aunque no se toque
una sola frase de instrucción. Consumidor real, aunque no en el
orquestador en vivo: `benchmarks/runner.py::prompt_versions()` estampa
las cuatro constantes en cada `BenchmarkRunRecord`, y
`ledger.py::assert_comparable` las compara contra lo ya registrado —
saltarse el bump abriría un agujero silencioso en ese mecanismo de
comparabilidad.

## Consecuencias

- El manifiesto es real, compartido entre `plan`/`plan_revision`/`work`,
  y probado de punta a punta (no sólo `render_prompt` en aislamiento) vía
  el fixture `service` real.
- Sigue sin haber ninguna garantía de que el modelo atienda el dato —
  exactamente lo que P2.1 existe para poder medir después, sin mezclar
  con enforcement todavía.
- La brecha "declarado vs. aplicado" en `SCRIPT_EXECUTION` (paquetes y
  red) queda documentada, no resuelta — candidato a un futuro punto
  propio, distinto de P2.2.
- `expected_artifacts`-como-metadato (P1.3d) y este manifiesto comparten
  el mismo principio: nunca declarar como dato de contexto algo que no se
  puede respaldar con una fuente determinística real.
