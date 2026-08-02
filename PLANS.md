# Agentarium — estado y hoja de ruta

Este documento es la guía operativa para continuar el proyecto. No es una
bitácora de sesiones: el detalle histórico y las decisiones ya cerradas viven
en `docs/decisions/`. Si una investigación no cambia la arquitectura, debe
quedar en el commit, el issue o el informe de benchmark correspondiente, no
crecer indefinidamente aquí.

## Norte del producto

Agentarium debe convertir un objetivo claro en artefactos útiles, trazables y
reparables usando modelos locales o compatibles con OpenAI. Una ejecución puede
fallar, pero nunca debe:

- marcar como completa una entrega que no satisface su contrato;
- sobrescribir trabajo de otra tarea sin detectarlo;
- ocultar la causa del fallo o impedir reanudar desde el punto correcto;
- ampliar acceso a archivos, comandos, red o credenciales sin aprobación.

El objetivo del próximo ciclo no es añadir más roles. Es conseguir un MVP
local-first confiable para proyectos pequeños y demostrarlo con mediciones
repetibles.

## Foto actual — 2 de agosto de 2026

### Lo que ya funciona

- Brief estructurado, DAG validado y cinco roles con autoridad acotada.
- Proveedores `mock`, Ollama y OpenAI-compatible seleccionables en runtime.
- API FastAPI, CLI, interfaz web, SSE, métricas, aprobaciones y auditoría.
- Persistencia SQLite con WAL, `busy_timeout` y transiciones con CAS optimista.
- Un worktree y una rama efímera por intento; sólo se integra un candidato
  aprobado.
- Materialización protegida por rutas, límites, checksums y comandos permitidos.
- Tester técnico y revisor semántico separados.
- Perfiles objetivos para sintaxis, web y ejecución real de scripts.
- Reintentos, recuperación, rework, candidatos del operador y división de una
  tarea agotada en subtareas más consolidación.
- Detección reactiva de colisiones de archivos y contrato de propiedad
  (`owned_paths`, `shared_component`, `output_strategy`).
- Rutas efectivas resueltas de forma mecánica: `owned_paths` más las entradas
  de `expected_outputs` que son rutas relativas válidas, sin interpretar
  descripciones libres. Lo usan el preflight de plan, `decompose` y la
  compuerta reactiva.
- Identidad estable de criterios en `decompose` (`ac-1`, `ac-2`, …): el texto
  se copia del padre y una partición determinista cubre el reparto si el
  modelo rompe el mapeo.
- Subtareas que heredan el mismo archivo se encadenan en secuencia con `patch`
  en vez de sobrescribirse.
- Máximo una división automática por linaje (`split_depth` persistido); lo
  agotado falla y queda reparable a mano.
- Vista previa local aislada por proyecto.

### Evidencia disponible

- Última verificación registrada: Ruff y MyPy limpios; 280/280 pruebas backend
  en verde, sin `xfail` ni exclusiones; lint y pruebas web reverificadas con
  `.\test.ps1` completo.
- La concurrencia entre un `project run` y lecturas repetidas de
  `project status` se verificó con un modelo real sin nuevas transiciones
  inválidas.
- P0 se confirmó con tres corridas reales contra qwen2.5-coder:7b, una por
  fix: workspaces `8be5cde9`, `13ee7f71` (preflight detectando un solapamiento
  declarado sólo vía `expected_outputs`) y `75ae6456` (primera división que
  llegó a crear hijas, ambas `COMPLETED`). Las tres usaron **el mismo objetivo
  de biblioteca**, deliberadamente, para poder compararlas contra el baseline
  de ADR 0021/0023. Eso no viola la regla 5: no se repitió una corrida
  buscando una salida distinta, y cada resultado se registró como salió,
  incluidos los negativos.
- Los detalles y reproducciones están en ADR 0015–0026.

### Riesgos y límites actuales

| Área | Evidencia actual | Consecuencia |
|---|---|---|
| Propiedad de archivos | Resuelto en P0: la detección lee también `expected_outputs`. Confirmado en vivo en `13ee7f71` | El modelo sigue sin declarar `owned_paths`, pero ya no hace falta que lo haga |
| División de tareas | Resuelto en P0: reparto por ids con partición determinista de respaldo | Queda que el título de una hija puede no describir bien los criterios que le tocaron tras una partición |
| Calidad de la descomposición | El modelo repite el mismo candidato también a nivel de subtarea | Dividir reduce el alcance, no cambia esa conducta; un linaje agotado ahora para y espera intervención |
| Dependencias de ejecución | El worker puede elegir paquetes no disponibles en el sandbox | El fallo aparece tarde, después de gastar inferencias e intentos |
| Evaluación de modelos | La maquinaria de medición existe y es reproducible (P1.1); la matriz no se ha ejecutado | Sigue sin haber baseline: ninguna recomendación de modelo por rol todavía |
| Compuerta de comandos | `deny_tokens` hacía match de subcadena: `models.py`, `registry.py` y `format_helper.js` quedaban rechazados. Corregido a límite de palabra | Ya no bloquea nombres de archivo normales; conviene revisar la lista si se agregan tokens cortos nuevos |
| Mantenibilidad | `engine.py` tiene 2051 líneas y `app/page.tsx` 2207 | Cada cambio cruza demasiadas responsabilidades |
| Interfaz | Sólo hay dos pruebas de render/strings; no prueban interacciones reales | Reintentos, acciones y SSE pueden romperse sin señal temprana |
| Persistencia | Las columnas nuevas se migran manualmente desde `create_all()`, ahora con backfill y prueba sobre una DB antigua real | Funciona, pero cada columna nueva sigue necesitando su propio backfill escrito a mano |
| Distribución | La aplicación real es local; el frontend alojado cae a una demo | El modelo de distribución todavía es ambiguo |
| Uso sobre proyectos reales | Cada proyecto empieza en un repositorio vacío propio | Hoy sirve mejor para greenfield que para trabajo cotidiano existente |

## Diagnóstico

La base técnica va bien: los errores graves encontrados terminaron en
protecciones mecánicas, pruebas y auditoría. El problema no es falta de trabajo,
sino cómo se decide el siguiente trabajo.

Las últimas iteraciones mezclaron tres bucles distintos:

1. corregir infraestructura determinista;
2. intentar modificar el comportamiento de un modelo pequeño mediante prompts;
3. descubrir un problema nuevo durante una corrida que validaba el anterior.

Eso produjo buenos hallazgos, pero también varias repeticiones del mismo dominio
y un `PLANS.md` que duplicaba los ADR. A partir de ahora, los prompts se tratan
como una ayuda probabilística; las garantías importantes deben vivir en
contratos, resolutores, validadores o aislamiento.

## Reglas para evitar iteración circular

1. **Una hipótesis por PR.** Debe indicar señal observada, causa propuesta,
   cambio mínimo y criterio de salida.
2. **Primero una regresión determinista.** Si el fallo no puede reproducirse
   todavía, se instrumenta antes de tocar el comportamiento.
3. **Una sola corrida real de confirmación por fix.** Si aparece otra causa,
   se clasifica y vuelve al backlog; no se amplía el mismo PR.
4. **Máximo un intento de prompt-only por comportamiento.** Si no cambia la
   señal, se implementa una regla mecánica o se acepta como límite conocido.
5. **Sin fishing.** No repetir objetivos hasta obtener la salida que confirma
   una expectativa.
6. **WIP limitado.** Como máximo un P0 y un P1 abiertos al mismo tiempo.
7. **ADRs sólo para decisiones arquitectónicas.** Bugs y mediciones normales no
   necesitan un ADR nuevo.
8. **No añadir validadores por dominio** sin una prueba positiva y otra negativa
   en dominios distintos.
9. **No refactorizar y cambiar comportamiento en el mismo PR.** Primero se fija
   la conducta con pruebas; después se mueve código.

## Puertas de calidad

Un cambio no está terminado por pasar tests unitarios solamente.

| Tipo de cambio | Verificación mínima |
|---|---|
| Dominio, persistencia o estado | Ruff, MyPy, suite backend y regresión del caso exacto |
| Orquestación o aislamiento | Lo anterior más flujo vertical mock completo |
| Comportamiento dependiente del modelo | Lo anterior más una corrida real controlada y un resultado clasificado |
| Interfaz | Lint, build, pruebas web y al menos una prueba de interacción afectada |
| Esquema de base de datos | Migración hacia adelante sobre una DB existente y copia de respaldo |
| Seguridad o autoridad | Prueba negativa y aprobación explícita del usuario |

Una corrida real no tiene que acabar en `completed` para validar un fix. Basta
con demostrar que la señal concreta desapareció y que un eventual fallo nuevo
queda clasificado por separado.

## Hoja de ruta priorizada

El orden es deliberado. No comenzar una fase posterior porque resulte más
atractiva si la anterior no cumple sus criterios de salida.

### P0 — cerrar el bucle de planificación actual — CERRADO (2 de agosto de 2026)

**Esfuerzo real:** un solo PR con cuatro incrementos lógicos — los dos puntos
previstos, más dos causas que aparecieron en las corridas de confirmación. Cada
incremento tuvo su propia hipótesis, sus pruebas y su corrida, pero se
entregaron juntos. La regla 1 ("una hipótesis por PR") queda como norma para
adelante, no aplicada retroactivamente: dividir esto ahora sería reescribir
historia sin ganancia.

**Esfuerzo estimado:** 1–2 PR, 2–4 sesiones de trabajo.

1. Crear un único resolutor de rutas efectivas usado por plan, `decompose` y
   compuerta reactiva:
   - usar `owned_paths` cuando esté informado;
   - usar entradas de `expected_outputs` que sean rutas relativas válidas como
     fallback;
   - normalizar separadores y mayúsculas una sola vez;
   - no interpretar descripciones libres como rutas.
2. Reemplazar la cobertura textual de `_attempt_split` por identidad estable de
   criterios:
   - numerar los criterios del padre antes de pedir `decompose`;
   - exigir que cada criterio quede asignado exactamente una vez;
   - copiar al hijo el texto original desde el padre, sin confiar en una
     reescritura del modelo;
   - usar una partición determinista acotada como fallback si la propuesta no
     respeta el mapeo.
3. Añadir pruebas de plan y división para los dos fallos observados en
   `Biblioteca API v4`.
4. Hacer una única repetición controlada de ese escenario.

**Criterios de salida:**

- los solapamientos se detectan aunque `owned_paths` venga vacío y
  `expected_outputs` contenga `api.py`;
- una reformulación de un criterio no cancela una división válida;
- la corrida real ya no falla por esos dos motivos;
- cualquier causa nueva queda registrada como categoría, no corregida dentro
  del mismo PR.

**Resultado.** Los cuatro criterios se cumplieron.

| Criterio | Evidencia |
|---|---|
| Solapamiento con `owned_paths` vacío | `13ee7f71`: preflight detectó `api_endpoints.py` entre dos hermanas con `declared_via: ["expected_outputs"]` |
| Reformulación no cancela la división | `75ae6456`: primera división que creó hijas, ambas `COMPLETED`; el modelo repartió `ac-1`/`ac-2` bien al primer intento |
| La corrida ya no falla por esos motivos | Confirmado; los fallos restantes son de otra categoría |
| Causas nuevas clasificadas, no arrastradas | Dos: rechazo de candidato sin clasificar por tipo, y recursión de la división. Cada una fue su propio incremento, con su hipótesis y sus pruebas, dentro del mismo PR |

Detalle en ADR 0024 (rutas efectivas y encadenamiento), 0025 (clasificación
del fallo de un candidato en corregible / infraestructura / seguridad),
0026 (identidad de criterios) y la revisión de 2026-08-02 en ADR 0021
(`split_depth`).

Pendiente conocido, no bloqueante: la partición determinista de respaldo nunca
llegó a ejercitarse en vivo — el modelo no rompió el mapeo en la corrida de
confirmación. Sólo está cubierta por pruebas.

### P1 — benchmark reproducible y taxonomía de fallos

Dividido en dos entregas: **P1.1 (maquinaria, sin inferencia real)** y **P1.2
(la matriz 3×3×3)**. P1.1 está cerrada.

**Esfuerzo estimado:** 2 PR, 4–6 sesiones más tiempo de inferencia en segundo
plano.

#### P1.1 — maquinaria medible sin inferencia — CERRADO (2 de agosto de 2026)

- Tres casos versionados en `benchmarks/cases/*.yaml` con `schema_version`,
  objetivo, artefactos esperados y validadores declarativos que leen los
  archivos entregados, nunca el resumen del agente.
- Taxonomía centralizada en `agentarium/benchmarks/taxonomy.py`, con las nueve
  categorías fijas. Clasifica la **causa terminal**: un incidente del que la
  corrida se recuperó no es por qué terminó. Un proveedor que falla una vez,
  reintenta y muere en un candidato duplicado es `duplicate_candidate`. La
  detección de fallo de proveedor es estructural (evento `agent_run_failed` en
  el intento final de esa tarea), no por texto.
- `technical_result` y `semantic_result` se leen de los `TestReport` y
  `Review` persistidos —último veredicto por tarea, excluyendo padres
  cancelados por una división—, no del estado del proyecto. Una tarea puede
  pasar la compuerta técnica y fallar la semántica, y ahora se reporta así.
  Una compuerta que nunca corrió no cuenta como aprobada.
- Un proyecto que el orquestador declara `completed` pero cuyos archivos no
  pasan los validadores del caso **no** cuenta como `completed`: es el falso
  `completed` que el benchmark existe para contar. Se mantienen las nueve
  categorías (`technical_validation` + `false_completed`), sin inventar una
  décima, y `false_completed` viaja serializado dentro de cada registro.
- `benchmark run` y `benchmark report`, más `--dry-run` y `--rerun`.
- Ejecución reanudable: cada resultado se anexa a un ledger JSONL y las
  combinaciones ya registradas se omiten. Una suite **congela** las versiones:
  si cambia el `schema_version` de un caso o una versión de prompt, la corrida
  falla temprano pidiendo otra `--suite`, en vez de mezclar dos baselines en
  un mismo informe. Un ledger corrupto o de otra `schema_version` se reporta
  con número de línea en vez de ignorarse.
- Suite determinista completa sobre `mock`, incluidos un smoke
  1 caso × 1 modelo × 1 repetición y pruebas de los comandos reales con
  `CliRunner`.

**Criterio de salida cumplido:** el informe completo se genera desde el ledger
sin depender de inferencia real, y una segunda ejecución de la misma matriz no
repite nada.

**Dos hallazgos de las primeras corridas con `mock`:**

1. El caso CSV terminó `completed` sin entregar ningún script Python — falso
   `completed` detectado por los validadores. Con `mock` es lo esperable
   (nunca escribe código real), así que la categoría de una corrida mock no
   mide calidad; lo que valida es la maquinaria.
2. **Los validadores del caso de arquitectura pasaban trivialmente.** El
   artefacto del mock repite el enunciado, y el enunciado ya contiene
   "componentes", "alternativa" y "glosario": cualquier entrega que parafrasee
   el pedido aprobaba. Corregido a patrones **estructurales** —encabezado
   dedicado, ítem de lista que enuncia la alternativa— que no se obtienen
   parafraseando. Hay pruebas en ambas direcciones: un documento que sólo
   repite el objetivo falla; uno con secciones reales pasa. La lección aplica
   a cualquier caso futuro: un validador por presencia de palabra mide el
   enunciado, no la entrega.

#### P1.1b — validación funcional del benchmark — CERRADO (2 de agosto de 2026)

Los validadores de P1.1 comprobaban archivos y estructura, no comportamiento.
Un caso puede ahora declarar un bloque `functional` que ejecuta la entrega
contra un fixture conocido, y **es lo único que distingue código que funciona
de código convincente**.

- Fixture versionado en `benchmarks/fixtures/<case_id>/`.
- **Interfaz exacta e idéntica para todos los modelos**: el caso declara
  `entrypoint` (`expenses.py`), `args` como lista y `produces`. No hay
  descubrimiento por glob — el resultado no puede depender del orden de
  archivos de una entrega.
- Ejecución sin shell: `python -E -s -S -B expenses.py input.csv --output
  result.json`, argumentos estructurados. **No `-I`**: el modo aislado
  también saca del `sys.path` el directorio del propio script, así que una
  entrega bien organizada en `expenses.py` + `helpers.py` fallaba en el
  import en vez de en su lógica. `-E -s -S` ignora variables PYTHON*, el
  directorio de usuario y `site` completo, así que los `site-packages` no
  entran; `-B` no deja bytecode. Los imports locales funcionan, los de
  terceros no.
- Entrega y fixture se copian a un directorio desechable dentro de
  `workspace_root` antes de ejecutar; el `produces` declarado se borra allí,
  así que una entrega que trae la respuesta hecha no obtiene crédito.
- Comparación de JSON parseado, no de texto: el orden de claves y los espacios
  no son parte del contrato.
- Sólo biblioteca estándar, para no adelantar el manifiesto de capacidades de
  P2.
- El caso CSV sube a `schema_version: 2`; los otros dos siguen en 1, cada caso
  versiona por su cuenta.

**Frontera de confianza, sin exageraciones:** reutiliza exactamente la que ya
usa `SCRIPT_EXECUTION`. `SafeCommandExecutor` acota ejecutable, argumentos,
entorno y `cwd`, y no hay shell — pero **no es un sandbox de sistema
operativo**: el script entregado corre como este usuario y podría intentar
rutas absolutas, red o subprocesos. Este PR no agrega aislamiento ni debe
describirse como si lo hiciera; un sandbox real es otra decisión
arquitectónica.

**Pruebas:** positiva; salida incorrecta (un programa que agrupa por año en
vez de por mes: corre bien, produce JSON válido y está mal); programa que
crashea; entrega sin el entrypoint declarado (la forma exacta de ADR 0016);
entrega que trae `result.json` hecho; entrega repartida en módulos locales
(pasa) y entrega que importa un paquete de terceros instalado (falla);
timeout; ejecutable fuera de la allowlist; y token denegado en los
argumentos.

**Un fallo del validador nunca aborta la matriz.** Un `OSError` al copiar
la entrega, al leer un fixture o al arrancar el proceso queda registrado
como `infrastructure` en el ledger y la combinación siguiente continúa; el
bucle de `execute()` además atrapa cualquier excepción inesperada por
corrida. Con pruebas de ambas cosas.

#### P1.2a — identidad real de la suite — CERRADO (2 de agosto de 2026)

`prompt_versions` y `case_schema_version` congelan lo que *nosotros*
declaramos. No dicen nada del código que corrió, de las pesas detrás de un
nombre de modelo ni de la máquina. Un tag de Ollama es mutable: `ollama
pull` reemplaza las pesas sin que el nombre cambie, así que dos corridas
del "mismo modelo" pueden ser dos modelos distintos, y un baseline que los
mezcla no es un baseline.

Cada registro lleva ahora `runtime_identity` (commit de Agentarium más si
el árbol estaba sucio, versión de Python, plataforma, concurrencia y
versión de Ollama) y `model_digest` (el digest de las pesas que esa corrida
usó). La reanudación falla con `SuiteDrift` si cambia cualquiera de ellos;
los digests se comparan por `(proveedor, modelo)`, así que agregar un
modelo nuevo a la matriz no invalida los registros del anterior.

La identidad se congela **una vez por invocación**, antes de medir:
sondearla por corrida dejaría que el entorno cambie a mitad de matriz sin
que el ledger se entere. `benchmark run` se niega a medir con el árbol de
trabajo sucio (el commit no identificaría lo que corre) salvo
`--allow-dirty`, y en ese caso el registro queda marcado y nunca compara
igual contra una corrida limpia.

#### P1.2 — la matriz

**P1.1b ya está cerrada, así que la matriz está desbloqueada.** Ejecutar la matriz inicial de 3 casos × 3 modelos ×
3 repeticiones. Las 27 corridas deben ser automatizadas; no supervisadas manualmente una por una:

```powershell
.\.venv\Scripts\agentarium.exe benchmark run `
  --suite "p1-baseline-2026-08" `
  --model "ollama:qwen3:4b" `
  --model "ollama:qwen3:8b" `
  --model "ollama:qwen2.5-coder:7b" `
  --repetitions 3
.\.venv\Scripts\agentarium.exe benchmark report --suite "p1-baseline-2026-08"
```

Usar siempre una suite con fecha: congela casos y prompts, así que si algo
cambia a mitad de camino la corrida avisa en vez de mezclar baselines. Es
reanudable: si Ollama se cae o se interrumpe la corrida, volver a ejecutar el
mismo comando continúa donde quedó.

**Criterios de salida:**

- informe Markdown/JSON reproducible desde eventos persistidos;
- cero falsos `completed` en los validadores independientes;
- baseline de tasa de finalización, tiempo y causas de fallo;
- ninguna recomendación de modelo por rol antes de tener esos datos.

### P2 — contrato real de capacidades del runtime

**Esfuerzo estimado:** 1–2 PR, 2–3 sesiones.

Para el MVP no se instalarán paquetes dinámicamente durante una tarea. Esa
opción mezcla ejecución con red y autoridad, y complica mucho el aislamiento.

1. Crear un manifiesto estructurado de capacidades: Python, paquetes
   permitidos/disponibles, ejecutables y restricciones de red.
2. Incluirlo en el contexto del planificador y del worker como datos, no sólo
   como una frase de prompt.
3. Hacer preflight de imports/comandos antes de gastar tester y revisor.
4. Si falta una capacidad, terminar con `unsupported_capability` y una acción
   concreta: elegir stack permitido, configurar un entorno o solicitar
   aprobación.
5. Dejar la creación de un entorno por proyecto y la instalación con red como
   una mejora futura, siempre detrás de aprobación y allowlist.

**Criterio de salida:** el caso API nunca llega tarde a un
`ModuleNotFoundError`; o usa una capacidad declarada o falla temprano con una
explicación accionable.

### P3 — reducir el coste de cada cambio

**Esfuerzo estimado:** 3 PR, 4–6 sesiones. Sin cambios funcionales mezclados.

1. Dividir `orchestration/engine.py` por responsabilidades ya cubiertas:
   planificación, ejecución, evaluación y recuperación/división.
2. Dividir `app/page.tsx` en cliente API/hooks, dashboard, proyecto,
   aprobaciones y drawer de tarea.
3. Generar o validar los tipos TypeScript desde OpenAPI para no mantener a mano
   contratos que ya existen en Pydantic. Incluir en UI `owned_paths`, estrategia
   y categoría de fallo.
4. Adoptar migraciones versionadas antes de añadir más columnas. Incluir backup
   automático de SQLite previo a migrar.
5. Añadir pruebas de interacción con API mock para crear, ejecutar, pausar,
   reintentar y resolver una aprobación.
6. Proponer CI para Windows con `.\test.ps1`; modificar CI/CD requiere aprobación
   explícita según `AGENTS.md`.

**Criterios de salida:** ningún módulo de orquestación o componente principal de
UI concentra todo el flujo; los contratos frontend/backend se comprueban en
build; una DB anterior se actualiza sin perder datos.

### P4 — convertirlo en una herramienta de uso cotidiano

**Esfuerzo estimado:** 3–5 PR, después de medir P1.

1. Permitir iniciar un proyecto desde una carpeta o repositorio existente:
   importar una copia de sólo lectura, trabajar en el workspace aislado y
   exportar un patch o una rama; nunca escribir el origen por defecto.
2. Añadir un centro de reparación que muestre categoría de fallo, criterio
   afectado, diferencia respecto al intento anterior y siguiente acción.
3. Exportar entrega, reporte de pruebas y auditoría desde la interfaz.
4. Añadir historial comparativo de intentos y modelos usando las métricas del
   benchmark.
5. Simplificar onboarding de Windows hasta un flujo comprobable de instalación,
   diagnóstico, selección de modelo y primer proyecto.

**Criterio de salida:** una persona puede tomar un proyecto pequeño real,
ejecutar Agentarium, entender un fallo, corregir/reintentar y exportar el
resultado sin consultar SQLite ni depender del CLI para operaciones normales.

### P5 — extensibilidad, sólo después del MVP

Departamentos, plugins, políticas editables, LangGraph, ejecución distribuida,
PostgreSQL, multiusuario y despliegue remoto quedan fuera del camino crítico.
Sólo se prioriza uno cuando el benchmark y el uso real demuestren qué cuello de
botella resuelve.

## Lo que no se hará ahora

- Más intentos de convencer a qwen2.5-coder mediante texto para que use
  `owned_paths` o evite librerías concretas.
- Nuevos dominios ad hoc fuera de los tres casos versionados.
- Instalación automática con red dentro del sandbox.
- Selección distinta de modelo por rol basada en impresiones.
- Reescritura completa del orquestador o migración a LangGraph.
- Departamentos o marketplace de plugins antes de que el flujo base sea útil.
- Backend remoto para el frontend alojado. Hasta una decisión explícita, la
  aplicación real es local y el sitio alojado es sólo demostración.

## Próximas tres entregas

1. ~~**PR 1 — planificación mecánica:** rutas efectivas más IDs de criterios y
   sus regresiones.~~ Entregado, ver P0.
2. ~~**PR 2 — medición:** casos versionados, taxonomía y `benchmark report`.~~
   Entregado como P1.1; queda ejecutar la matriz (P1.2).
3. **PR 3 — capacidades:** manifiesto del runtime y fallo temprano por capacidad
   no disponible.

No empezar la modularización grande antes de que PR 2 congele el
comportamiento que se debe preservar.

## Runbook de Windows

Instalación y verificación completa:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\test.ps1
```

Para usar objetivos con acentos desde consola:

```powershell
chcp 65001 > $null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\agentarium.exe project create "Objetivo en español"
```

Si Ollama no responde:

```powershell
ollama list
ollama serve
```

Si `pytest` falla en los tests que usan worktrees, revisar `TMP`/`TEMP`: git
tiene un límite propio de longitud de path para su contabilidad de worktrees
(`fatal: '$GIT_DIR' too big`, ADR 0022) que `core.longpaths` no cubre. El
directorio temporal tiene que ser **corto**:

```powershell
$env:TMP = "C:\Users\$env:USERNAME\agtmp"
$env:TEMP = $env:TMP
```

Inicio y parada local:

```powershell
.\dev.ps1
.\stop.ps1
```

## Reglas de entrega y handoff

- Revisar `git status` antes de modificar y preservar cambios ajenos.
- No modificar `.openai/hosting.json`, credenciales, CI/CD ni autoridad de red
  sin aprobación.
- Ejecutar `powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1`
  antes de integrar.
- Mantener tester técnico y revisor semántico separados.
- No integrar un candidato porque su resumen afirme que funciona; inspeccionar
  el artefacto y su evidencia.
- Registrar una decisión en `docs/decisions/` sólo si cambia arquitectura,
  seguridad, persistencia o contrato público.
- Actualizar este archivo al cerrar una fase, no después de cada experimento.

## Referencias

- Arquitectura: `docs/architecture/overview.md`
- Contratos de artefactos: `docs/schemas/artifacts.md`
- Resiliencia de roles: ADR 0015
- Ejecución real de scripts: ADR 0016
- Consistencia entre dependencias: ADR 0017
- Separación de contexto interno: ADR 0018
- Colisiones de archivos: ADR 0019
- Capacidades del sandbox: ADR 0020
- División de tareas: ADR 0021
- Concurrencia y recuperación: ADR 0022
- Propiedad explícita de archivos: ADR 0023
- Rutas efectivas y encadenamiento de subtareas: ADR 0024
- Clasificación del fallo de un candidato: ADR 0025
- Identidad de criterios en `decompose`: ADR 0026
