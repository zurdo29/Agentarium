# Agentarium — estado y hoja de ruta

Este documento es la guía operativa para continuar el proyecto. No es una
bitácora de sesiones: el detalle histórico y las decisiones ya cerradas viven
en `docs/decisions/`. Si una investigación no cambia la arquitectura, debe
quedar en el commit, el issue o el informe de benchmark correspondiente, no
crecer indefinidamente aquí.

> **Dónde estamos (3 de agosto de 2026).** P0, P1.1, P1.1b y P1.2a siguen
> cerrados. **P1.2 (la matriz 3×3×3) ya se ejecutó completa: 27/27 corridas
> registradas**, pero su criterio de calidad ("cero falsos `completed`") **no
> se cumplió**: el informe automático señala 4/27; la adjudicación manual
> (`benchmarks/results/p1-baseline-2026-08/findings.md`) confirma 3 y
> reclasifica 1 como falso negativo del propio validador (exigía un
> encabezado que el `goal` del caso nunca pidió). El resultado real es 3/27,
> igual sigue sin ser cero. Por eso P1.2 queda como medición completa, no
> como fase cerrada, y **P1 sigue abierto**. P1.3 quedó dividido en cuatro PR
> independientes (P1.3a–P1.3d) para cerrar mecánicamente las causas
> observadas. **P1.3a ya cerró** (instrumentó tiempo de cola vs. tiempo de
> generación con evidencia sintética, sin Ollama real): `asyncio.wait_for`
> envolvía la espera del semáforo de concurrencia además de la llamada
> real, y ahora un `agent_run` que falle por timeout puede decir cuál fue
> — pero **todavía no se cambió ni se calibró ningún `timeout_seconds`**, y
> sigue sin saberse si `qwen3:8b` de verdad supera 300s generando o si el
> tiempo se va en cola en una corrida real. **El siguiente paso es P1.3b**
> (`expected_outputs` no exigible). Detalle completo en "P1.2 — la matriz",
> la sección "P1.3" más abajo, y `findings.md`. No cambiar prompts ni casos
> de la suite
> `p1-baseline-2026-08`: ya cumplió su propósito y queda congelada como
> registro histórico; cualquier remedición usa un nombre de suite nuevo.
> Resultados versionados en `benchmarks/results/p1-baseline-2026-08/`
> (`report.md`, `report.json`, `environment.md`, `findings.md`); el ledger y
> la DB de la corrida siguen sin versionar en `runtime/`.

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

## Foto actual — 3 de agosto de 2026

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

- Última verificación registrada: Ruff y MyPy limpios; 294/294 pruebas backend
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
- P1.2 corrió la matriz completa 3×3×3 contra la suite `p1-baseline-2026-08`
  (27/27 registradas, HEAD `2b4ecb2`, árbol limpio): 3/27 `completed` reales
  (11.1%), 4/27 falsos `completed` según el informe automático, 3/27 tras
  adjudicación manual (el 4º es un falso negativo del validador). Detalle e
  inspección de causas en "P1.2 — la matriz" y en
  `benchmarks/results/p1-baseline-2026-08/` (`report.md`, `report.json`,
  `environment.md`, `findings.md`, versionados; el ledger y la DB de la
  corrida siguen en `runtime/`, sin versionar).

### Riesgos y límites actuales

| Área | Evidencia actual | Consecuencia |
|---|---|---|
| Propiedad de archivos | Resuelto en P0: la detección lee también `expected_outputs`. Confirmado en vivo en `13ee7f71` | El modelo sigue sin declarar `owned_paths`, pero ya no hace falta que lo haga |
| División de tareas | Resuelto en P0: reparto por ids con partición determinista de respaldo | Queda que el título de una hija puede no describir bien los criterios que le tocaron tras una partición |
| Calidad de la descomposición | El modelo repite el mismo candidato también a nivel de subtarea | Dividir reduce el alcance, no cambia esa conducta; un linaje agotado ahora para y espera intervención |
| Dependencias de ejecución | El worker puede elegir paquetes no disponibles en el sandbox | El fallo aparece tarde, después de gastar inferencias e intentos |
| Evaluación de modelos | Matriz ejecutada (27/27, P1.2): 11.1–22.2% de completion real, 3/27 falsos `completed` confirmados tras adjudicación manual. `qwen3:8b` en 0%, pero no se sabe si por generación lenta o por cola del semáforo de concurrencia (timeout envuelve ambas, ver P1.3a) | Todavía ninguna recomendación de modelo por rol — la tasa de `qwen3:8b` no es comparable hasta instrumentar y, si hace falta, calibrar |
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

### P1 — benchmark reproducible y taxonomía de fallos — ABIERTO

Dividido en entregas: **P1.1 (maquinaria, sin inferencia real)**, **P1.1b
(validación funcional)**, **P1.2a (identidad de la suite)**, **P1.2 (la
matriz 3×3×3)** y **P1.3a–P1.3d (cerrar las causas que P1.2 encontró, cuatro
PR independientes)**. Las primeras tres están cerradas. **P1.2 ya midió
(27/27), pero su criterio de calidad no se cumplió** (3/27 falsos
`completed` confirmados tras adjudicación manual), así que P1 en conjunto
sigue abierto hasta que P1.3a–d resuelvan o acepten explícitamente las
causas observadas — regla 6 (WIP limitado) sigue permitiendo esto porque es
un único P1, no uno nuevo.

**Esfuerzo estimado:** 4 PR más para P1.3a–d, 4–6 sesiones más tiempo de
inferencia ya gastado en segundo plano (P1.2 no se re-corre).

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

La identidad se congela **una vez por invocación** como referencia, y la
versión y el digest de Ollama se **revalidan antes de cada corrida**: un
`ollama pull` a mitad de matriz aborta con `SuiteDrift` en vez de quedar
registrado como si nada hubiera cambiado. La deriva nunca se degrada a un
dato `infrastructure`; detiene la matriz.

Antes de empezar, si falta el digest de cualquier modelo pedido —Ollama
apagado o tag no instalado— la corrida falla temprano en vez de
descubrirlo en la corrida 14.

`ollama_version` se compara **sólo** para registros que usaron Ollama y
cuyo objetivo sigue en la matriz; el resto de la identidad se compara
siempre. Si no, una suite medida enteramente con `mock` y Ollama apagado
(`ollama_version: null`) se rompía al reanudarla con Ollama encendido,
aunque Ollama nunca participó.

Medir con el árbol de trabajo sucio se rechaza sin excepción: un booleano
no distingue dos árboles sucios distintos, así que dos mediciones así
compararían iguales midiendo código diferente. No hay `--allow-dirty`.

`platform` es `Sistema-Arquitectura` (`Windows-AMD64`): identifica la
**plataforma, no la máquina**. Dos hosts distintos con el mismo SO y
arquitectura producen la misma cadena; un parche del SO no invalida un
baseline, mudarse de SO o arquitectura sí. El ledger pasa a
`schema_version: 2`.

#### P1.2 — la matriz — MEDICIÓN COMPLETA (3 de agosto de 2026); criterio de calidad no cumplido

Las 27 corridas (3 casos × 3 modelos × 3 repeticiones) están hechas y
registradas contra la suite `p1-baseline-2026-08`. La maquinaria (P1.1,
P1.1b, P1.2a) se comportó como se diseñó: informe reproducible desde el
ledger, cero drift, cero corridas perdidas. **Lo que no se cumplió es el
criterio de calidad de la propia matriz**: el informe automático señala 4 de
27 corridas como falsos `completed`; la adjudicación manual confirma 3 (ver
"Causas inspeccionadas a fondo" más abajo). De cualquiera de las dos formas,
no es cero. Eso no cierra P1.2 como fase exitosa; la deja como
medición completa con un resultado real y abre P1.3 para cerrar
mecánicamente las causas antes de tocar prompts, casos o empezar P2. La
suite `p1-baseline-2026-08` queda congelada como registro histórico de este
resultado — no se reutiliza para remedir después de un cambio; eso exige un
nombre de suite nuevo.

Las secciones de aislamiento y comandos de abajo quedan como referencia
reproducible de cómo se midió, no como un "próximo paso" pendiente.

**Se ejecutó en dos tandas, en la misma suite.** Primero 9 —los tres casos
contra `qwen3:4b`, el modelo más liviano— y sólo después las 18 restantes.
Así, un problema de infraestructura, de staging, del contrato funcional o de
persistencia habría aparecido antes de gastar horas con los modelos grandes.
No apareció ninguno: lo que la tanda 2 sí mostró fue la señal de calidad de
abajo, más una pregunta abierta sobre el timeout del modelo más grande —
todavía no se sabe si hace falta calibrarlo o si el problema es otro (ver
"Causas inspeccionadas" más abajo y P1.3a).

##### Aislamiento (obligatorio, en la misma sesión de PowerShell)

La matriz crea un proyecto real y un workspace por corrida. Van a una base y a
un workspace propios para no mezclarse con los proyectos de auditoría ni
inflar `runtime/agentarium.db`.

```powershell
$env:AGENTARIUM_DATABASE_URL = "sqlite:///runtime/benchmarks/p1-baseline-2026-08/agentarium.db"
$env:AGENTARIUM_WORKSPACE_ROOT = "C:\Users\Renzo\agbench\workspaces"

New-Item -ItemType Directory -Force `
  "runtime\benchmarks\p1-baseline-2026-08", `
  "C:\Users\Renzo\agbench\workspaces" | Out-Null
```

El workspace va **fuera del repositorio y con path corto** a propósito: cada
corrida crea worktrees de git, y con paths profundos reaparece el
`fatal: '$GIT_DIR' too big` de ADR 0022.

##### Tanda 1 — 9 corridas

```powershell
.\.venv\Scripts\agentarium.exe benchmark run `
  --suite "p1-baseline-2026-08" `
  --model "ollama:qwen3:4b" `
  --repetitions 3
```

Verificado con `--dry-run` sobre el commit `6d11fd5` antes de correr:
planificó exactamente 9 corridas, congeló el digest de `qwen3:4b` y salió con
código 0. La tanda real corrió sobre `2b4ecb2` (un commit de docs encima,
árbol limpio; no invalida nada porque el ledger todavía no tenía registros).

Informe provisional tras la tanda 1, mismo comando que para el informe final:

```powershell
.\.venv\Scripts\agentarium.exe benchmark report --suite "p1-baseline-2026-08"
```

**Lo que se revisó antes de seguir a la tanda 2**, y nada más: que las nueve
quedaron registradas, que las categorías de fallo eran plausibles y no todas
`infrastructure`, y **si había falsos `completed`.** Los había — 3 de 9, los
tres en `csv_expenses_cli`. Eso no detuvo la matriz: un resultado malo es un
dato del baseline, y la regla de no cambiar nada a mitad de camino se
respetó. Detalle de esos 3 en "Causas inspeccionadas" más abajo.

##### Tanda 2 — las 18 restantes

Mismo nombre de suite, misma base, mismo workspace, misma sesión de variables:

```powershell
.\.venv\Scripts\agentarium.exe benchmark run `
  --suite "p1-baseline-2026-08" `
  --model "ollama:qwen3:4b" `
  --model "ollama:qwen3:8b" `
  --model "ollama:qwen2.5-coder:7b" `
  --repetitions 3
.\.venv\Scripts\agentarium.exe benchmark report --suite "p1-baseline-2026-08"
```

Las 9 ya hechas se omiten solas. Agregar modelos no invalida los registros
anteriores: los digests se comparan por `(proveedor, modelo)`.

##### Lo que puede detener la matriz, y qué significa

| Código | Motivo | Qué hacer |
|---|---|---|
| 3 | deriva: cambió el commit, el runtime, la versión de Ollama o el digest de un modelo | usar `--suite` con nombre nuevo; la suite anterior ya no es comparable |
| 4 | árbol de trabajo sucio | commitear primero — no hay `--allow-dirty`, y un booleano no distingue dos árboles sucios |
| 5 | falta el digest de un modelo | `ollama serve` y `ollama pull <modelo>` |

Es reanudable ante cualquier interrupción: volver a ejecutar el mismo comando
continúa donde quedó. **No** hay que borrar el ledger para reintentar. (Las
27 corrieron sin interrupciones — esto quedó sin ejercitarse en vivo esta
vez, igual que la partición determinista de respaldo de P0.)

##### Resultados (27/27)

| Modelo | Corridas | Completadas | Tasa | Falsos `completed` | Segundos (media) |
|---|---|---|---|---|---|
| `qwen2.5-coder:7b` | 9 | 1 | 11.1% | 0 | 263 |
| `qwen3:4b` | 9 | 2 | 22.2% | 3 | 252 |
| `qwen3:8b` | 9 | 0 | 0.0% | 1 | 868 (hasta 1823s en una corrida) |

Categorías sobre las 27: `duplicate_candidate` 9, `technical_validation` 7,
`completed` 3, `path_conflict` 2, `planning_contract` 2, `provider_failure`
2, `semantic_rejection` 2. Ninguna `infrastructure`; ninguna
`unsupported_capability` — ver la expectativa fallida más abajo.

Los 4 falsos `completed` que señala el informe **automático** —
**adjudicación manual en
`benchmarks/results/p1-baseline-2026-08/findings.md`: 3 confirmados, 1 falso
negativo del validador**. `report.md`/`report.json` no se editan para
reflejar esto: quedan como salida histórica de la maquinaria; la
adjudicación vive aparte, en `findings.md`.

| Caso | Modelo | Rep. | Validador que falló | Adjudicación |
|---|---|---|---|---|
| `csv_expenses_cli` | `qwen3:4b` | 1 | procesa el CSV fixture y produce el resumen correcto | Confirmado |
| `csv_expenses_cli` | `qwen3:4b` | 2 | documentación Markdown (ausente) | Confirmado |
| `csv_expenses_cli` | `qwen3:4b` | 3 | documentación Markdown + procesamiento del CSV | Confirmado |
| `architecture_document` | `qwen3:8b` | 2 | sección de decisiones de diseño | Falso negativo del validador |

##### Causas inspeccionadas a fondo (read-only, sin re-correr nada)

Detalle completo con cita de código en `findings.md`. Resumen:

**Los 3 confirmados (`csv_expenses_cli`/`qwen3:4b`):** los tres `expenses.py`
entregados ignoran `sys.argv` por completo (hardcodean
`input.csv`/`result.json`) y envuelven la lógica en `except Exception:
print(...)` sin `sys.exit`, así que el proceso siempre devuelve código 0. El
perfil interno `SCRIPT_EXECUTION` (`backend/agentarium/execution/validation.py`)
corre el script sin argumentos y sólo mira el código de salida — no puede
detectar nada de esto, y de todos modos nunca ejecuta contra el fixture
oculto del caso. Rep 1: bug real de agregación (`monthly_totals[month] =
sum(...)` sobreescribe entre fechas del mismo mes en vez de acumular). Rep 2
y 3: el plan (`decompose`) nunca creó una tarea de documentación — en rep 3
el Markdown ni siquiera queda como acceptance criterion de la tarea de
consolidación, sólo como `expected_outputs`, que no se traduce en un check
exigible.

**El falso negativo (`architecture_document`/`qwen3:8b` rep 2):** el `goal`
del caso pide describir componentes, decisiones de diseño con alternativa, y
un glosario — un requisito de **contenido**. El validador que falló exige
además un encabezado Markdown dedicado a "decisión"/"diseño", algo que el
`goal` nunca pidió (ese requisito estructural existe para que un mock no
apruebe repitiendo el enunciado, según el propio comentario del caso YAML —
no porque el objetivo pida secciones separadas). La entrega tiene el
contenido completo — cada componente con su alternativa descartada, glosario
propio — sólo que las decisiones quedaron dentro de "## Componentes
Principales" en vez de bajo su propio encabezado. El revisor semántico
interno la aprobó correctamente; no había nada que debiera atrapar. El punto
de acción es auditar el validador, no el revisor (ver P1.3d).

**Los 2 `provider_failure` de `library_api_sqlite`/`qwen3:8b` — diagnóstico
corregido:** `asyncio.wait_for(timeout=300)` en `roles.py:91-94` envuelve
`scheduler.run(...)`, que primero espera un semáforo de concurrencia
(`model_concurrency: 1` en toda la suite) y **después** llama al proveedor.
El timeout cuenta desde antes de entrar a la cola, no desde que arranca la
generación. En el proyecto `45a0895a`, de 4 tareas `implementation_worker`
listas al mismo tiempo, sólo 1 llegó a ejecutar `provider.generate()` (293s,
con tokens reales); las otras 3 murieron a los ~300s con `TimeoutError`. Se
revisó si eso prueba que nunca salieron de la cola: **no lo prueba** — en el
camino de fallo, `ResourceUsage` nunca registra `prompt_characters`, así que
da 0 tanto si la tarea nunca llegó a generar como si llegó y fue cortada a
mitad de respuesta. **No sabemos, con la telemetría actual, si la inferencia
sola de `qwen3:8b` supera 300s o si el tiempo se va mayormente en cola.**
P1.3a instrumenta esto antes de calibrar nada.

**Criterios de salida — resultado real, no reinterpretado:**

| Criterio | Resultado |
|---|---|
| Informe Markdown/JSON reproducible desde el ledger | Cumplido |
| Baseline de tasa de finalización, tiempo y causas de fallo | Cumplido — tabla arriba |
| Ninguna recomendación de modelo por rol antes de tener datos | Cumplido — no se recomienda ningún modelo todavía; la tasa de `qwen3:8b` no es comparable hasta P1.3a |
| Cero falsos `completed` en los validadores independientes | **No cumplido — 3/27 confirmados tras adjudicación manual** (el informe automático señala 4; el 4º es un falso negativo del validador, ver arriba). Sigue abierto como criterio de calidad; P1.3 cierra las causas mecánicas conocidas, no persigue "cero" a fuerza de repetir corridas (violaría la regla 5, sin fishing) |

**Expectativa que no se cumplió — el caso de biblioteca:** se anticipaba que
`library_api_sqlite` fallara sobre todo por `unsupported_capability` (el
modelo eligiendo Flask, ADR 0020). **No ocurrió ni una vez en 27 corridas** —
la categoría no aparece en el informe. Las causas reales fueron
`semantic_rejection`, `technical_validation`, `duplicate_candidate`,
`planning_contract` y los dos `provider_failure` de timeout ya descritos.
Información real para P2 igual, pero distinta de la anticipada: el límite de
capacidades no fue lo que más pesó esta vez.

#### P1.3 — cerrar mecánicamente las causas que P1.2 encontró — cuatro PR independientes

Cuatro causas quedaron identificadas en la inspección read-only de P1.2
(detalle arriba, en "Causas inspeccionadas a fondo", y completo en
`findings.md`). Cada una es su propio PR con su propia hipótesis y su propio
criterio de salida (regla 1) — no se mezclan en un solo cambio (regla 9).
Ninguna se resuelve "probando con otro prompt" (regla 4) ni persigue "cero
falsos `completed`" repitiendo corridas (regla 5). P1.3a–d son
independientes entre sí (ninguna bloquea a otra). **P1.3a ya cerró** (ver
abajo); el prerrequisito para cualquier trabajo futuro de calibrar un
timeout por modelo queda cumplido, pero esa calibración en sí **todavía no
es un punto propio de P1.3 y no se ha iniciado** — sigue sin saberse si
hace falta. El siguiente paso dentro de P1.3 es **P1.3b**.

##### P1.3a — instrumentar `queue_wait` vs. `generation_time` — CERRADO (3 de agosto de 2026)

`roles.py:91-94` envolvía en un mismo `asyncio.wait_for` la espera del
semáforo de `ResourceScheduler` (`model_concurrency`) y la llamada real al
proveedor. Con el `resource_usage_json` de entonces no se podía saber
cuánto de un `TimeoutError` era cola y cuánto generación — ver
`findings.md`.

- `ResourceUsage` gana `queue_wait_ms`/`generation_ms` (opcionales,
  `default=None`; un registro viejo sin estas claves deserializa con
  ambos en `None`, sin migración — es una columna `JSON`, no columnas
  fijas).
- `ResourceScheduler.run` acepta un `AttemptTiming` que marca cuándo entra
  a la cola, cuándo adquiere el semáforo y cuándo termina la llamada real,
  en el camino de éxito y en el de cancelación/fallo. `generation_ms`
  queda `None` si la tarea nunca adquirió el semáforo — no si tardó cero.
- Corregido de paso: `scheduler.queued` quedaba incrementado para siempre
  si una tarea era cancelada mientras todavía esperaba el semáforo.
- `RoleRunner.run` acumula ambos campos de todos los reintentos internos
  de un mismo `AgentRun`, persistidos en éxito y en fallo.

**Criterio de salida cumplido, con evidencia sintética — no con una corrida
real contra `qwen3:8b`:** 10 pruebas deterministas, sin Ollama, en
`backend/tests/test_scheduler.py`, `test_role_timing.py` y el nuevo
`test_agent_run_persistence.py`. Cubren: espera real detrás de otra
llamada; cancelación mientras espera (`generation_ms=None`); timeout
después de adquirir (la generación sí se registra); `queued`/`active`
vuelven a cero; éxito y fallo persistidos en `AgentRun`; reintentos
acumulando en vez de sobreescribir; **el caso exacto original desde
`RoleRunner`** (otra tarea ocupa el semáforo, `RoleRunner` agota su propio
timeout esperando, el `RoleExecutionError` resultante lleva `queue_wait_ms`
con `generation_ms is None`); y round-trip real por el repositorio/DB de
ambos campos, más la lectura de un `resource_usage_json` sin ellos.

**Todavía no se cambió ni se calibró ningún `timeout_seconds`.** Esto es
instrumentación pura: la próxima vez que la matriz corra con `qwen3:8b` (u
otro modelo grande) contra un caso pesado, el `agent_run` que falle va a
poder decir si el tiempo se fue en cola o en generación — hoy no puede.
Eso es lo que decide si calibrar el timeout es siquiera la corrección
correcta, y esa decisión queda fuera de P1.3a a propósito.

##### P1.3b — `expected_outputs` no se traduce en acceptance criteria exigibles

**Esfuerzo estimado:** 1 PR, 1–2 sesiones.

Un work item puede listar un entregable en `expected_outputs` (p. ej. el
Markdown de uso) sin que ningún `acceptance_criteria` lo fuerce, y el
revisor semántico nunca lo evalúa. Confirmado en 2 de los 3 falsos
`completed` de `csv_expenses_cli` (rep 2 y 3 — rep 1 falló únicamente por el
bug de agregación de P1.3c, sin relación con documentación faltante).

**Criterio de salida:** decisión explícita — o `decompose` deriva
automáticamente un AC exigible de cada `expected_output`, o se documenta
aquí como límite conocido con su razón.

##### P1.3c — `SCRIPT_EXECUTION` corre sin argumentos y sin oráculo

**Esfuerzo estimado:** 1 PR, 1 sesión.

El perfil interno (`backend/agentarium/execution/validation.py`) ejecuta el
script entregado sin los argumentos del contrato real y sólo mira el código
de salida — no puede detectar una salida numéricamente incorrecta, y un
script que traga excepciones sin `raise`/`sys.exit` siempre devuelve 0 pase
lo que pase. Es una limitación estructural conocida (el orquestador no tiene
el fixture oculto), no necesariamente un bug.

**Criterio de salida:** decisión explícita — `SCRIPT_EXECUTION` acepta un
**contrato de ejecución declarado** (entrypoint, argumentos, salida
esperada — análogo al bloque `functional:` de un caso de benchmark, P1.1b)
cuando el work item lo tiene, y lo usa para invocar el script en vez de
correrlo a ciegas sin argumentos. **No** una heurística de AST que intente
reconocer patrones de código "sospechosos": eso adivina intención y es
frágil por diseño (falsos positivos y negativos crecen con cada patrón
nuevo que el modelo invente). Si hoy ningún work item declara ese contrato,
se documenta como límite conocido en vez de improvisar una heurística.

##### P1.3d — auditar validadores del benchmark por sobre-especificación

**Esfuerzo estimado:** 1 PR, 1 sesión.

El validador de `architecture_document` ("sección de decisiones de diseño")
exigía un encabezado dedicado que el `goal` del caso nunca pidió, y produjo
el falso negativo de P1.2 (ver arriba y `findings.md`). No es un problema
del revisor semántico ni del orquestador — es el validador más estricto que
el objetivo real del caso.

1. Para cada validador estructural de los tres casos, construir
   **trazabilidad explícita `goal` → validador**: qué frase o cláusula
   literal del `goal` justifica esa exigencia. Un validador sin una frase
   del `goal` que lo respalde queda marcado como sobre-especificación por
   default, no como limitación aceptada tácitamente.
2. Ajustar el validador de `architecture_document`, o incorporar al `goal`
   del caso la exigencia de encabezado dedicado si se decide que es
   intencional (para que quede trazable como las demás).

**Criterio de salida:** una tabla `validador → frase del goal que lo
justifica` para los tres casos, sin entradas huérfanas salvo que queden
documentadas explícitamente aquí como sobre-especificación deliberada, con
su razón.

**Criterio de salida de P1.3 en conjunto:** las cuatro PR mergeadas, o cada
causa aceptada explícitamente como límite conocido con su razón en este
documento. Sólo después se decide si remedir con una suite nueva o pasar
directo a P2 con las capacidades ya conocidas.

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
   sus regresiones.~~ Entregado, ver P0 (PR #1).
2. ~~**PR 2 — medición:** casos versionados, taxonomía y `benchmark report`.~~
   Entregado como P1.1 (PR #2), más P1.1b (PR #3, validación funcional) y
   P1.2a (PR #4, identidad de la suite). ~~Queda ejecutar la matriz, P1.2.~~
   **Ejecutada: 27/27, criterio de calidad no cumplido (PR de medición
   P1.2).**
3. **P1.3a–P1.3d — cerrar las causas mecánicas que P1.2 encontró, cuatro PR
   independientes:** P1.3a instrumenta `queue_wait` vs. `generation_time`
   (prerrequisito de cualquier calibración de timeout); P1.3b hace
   exigible `expected_outputs`; P1.3c le da oráculo o límite documentado a
   `SCRIPT_EXECUTION`; P1.3d audita los validadores del benchmark por
   sobre-especificación respecto al `goal` (causa del falso negativo de
   `architecture_document`). Bloquean empezar P2 con datos limpios.
4. **PR de capacidades (P2):** manifiesto del runtime y fallo temprano por
   capacidad no disponible.

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
