# Agentarium — estado y hoja de ruta

Este documento es la guía operativa para continuar el proyecto. No es una
bitácora de sesiones: el detalle histórico y las decisiones ya cerradas viven
en `docs/decisions/`. Si una investigación no cambia la arquitectura, debe
quedar en el commit, el issue o el informe de benchmark correspondiente, no
crecer indefinidamente aquí.

> **Dónde estamos (4 de agosto de 2026).** P0, P1.1, P1.1b y P1.2a siguen
> cerrados. **P1.2 (la matriz 3×3×3) ya se ejecutó completa: 27/27 corridas
> registradas**, pero su criterio de calidad ("cero falsos `completed`") **no
> se cumplió**: el informe automático señala 4/27; la adjudicación manual
> (`benchmarks/results/p1-baseline-2026-08/findings.md`) confirma 3 y
> reclasifica 1 como falso negativo del propio validador (exigía un
> encabezado que el `goal` del caso nunca pidió). El resultado real es 3/27,
> igual sigue sin ser cero. Por eso P1.2 queda como medición completa, no
> como fase cerrada, y **P1 sigue abierto**. P1.3 quedó dividido en cuatro PR
> independientes (P1.3a–P1.3d) para cerrar mecánicamente las causas
> observadas. **Las cuatro ya cerraron.** P1.3a instrumentó tiempo de
> cola vs. tiempo de generación con evidencia sintética, sin Ollama real:
> `asyncio.wait_for` envolvía la espera del semáforo de concurrencia además
> de la llamada real, y ahora un `agent_run` que falle por timeout puede
> decir cuál fue — pero **todavía no se cambió ni se calibró ningún
> `timeout_seconds`**, y sigue sin saberse si `qwen3:8b` de verdad supera
> 300s generando o si el tiempo se va en cola en una corrida real. P1.3b
> hizo que cada `expected_output` genere su propio criterio de aceptación
> determinista — probado hasta el revisor: el criterio derivado llega al
> payload real y un resultado `false` bloquea la aprobación mecánicamente,
> sin necesitar Ollama. P1.3c le dio a `SCRIPT_EXECUTION` un contrato de
> ejecución declarado (entrypoint, argumentos, existencia del artefacto
> declarado) para que un work item que lo tenga deje de correr a ciegas sin
> argumentos, con evidencia sintética de la forma exacta del bug real de
> `csv_expenses_cli` — pero **hoy ningún work item puede declarar ese
> contrato todavía**: el contrato del LLM de planificación deliberadamente
> no lo expone (ver ADR 0027), así que el mecanismo queda probado de punta a
> punta pero inerte en cualquier corrida real. P1.3d construyó la
> trazabilidad `goal` → validador completa de los tres casos y encontró un
> segundo hallazgo real además del ya conocido (`library_api_sqlite` pedía
> documentación que el `goal` nunca comunicaba al modelo) — ambos casos se
> corrigieron extendiendo el `goal`, no aflojando validadores.
> **P1.3 en conjunto queda cerrado. Decisión: pasar directo a P2 sin
> remedir P1 con una suite nueva, por ahora.** Razón: P1.3c deja
> `SCRIPT_EXECUTION` con un contrato de ejecución declarado pero
> deliberadamente inerte en corridas reales (nada lo puede poblar
> todavía); gastar horas de matriz ahora no cerraría esa incertidumbre
> concreta. Se acepta como límite conocido y se continúa con P2. **P2
> también se divide en dos PR: P2.1 (manifiesto de capacidades del
> runtime, incluido como datos en el contexto del planificador y del
> worker) ya cerró; P2.2 (preflight de imports/comandos +
> `unsupported_capability`) queda para después.** Remedir P1 sigue como
> opción abierta, no descartada, para cuando haga falta comparar contra
> una baseline real. Detalle completo en "P1.2 — la matriz", la sección
> "P1.3" más abajo, la sección "P2" y `findings.md`. No cambiar prompts ni
> casos de la suite
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
PR independientes)**. **P1.2 ya midió (27/27), pero su criterio de calidad
no se cumplió** (3/27 falsos `completed` confirmados tras adjudicación
manual). **Las cuatro P1.3a–d ya resolvieron o documentaron
explícitamente cada causa observada** (timeout instrumentado, criterios
derivados de `expected_outputs`, contrato de ejecución declarado para
`SCRIPT_EXECUTION`, trazabilidad `goal` → validador). P1 en conjunto sigue
abierto hasta decidir, en otra sesión, si remedir con una suite nueva o
pasar directo a P2 con las capacidades ya conocidas — regla 6 (WIP
limitado) sigue permitiendo esto porque es un único P1, no uno nuevo.

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
P1.3a (cerrado) ya instrumentó esto; falta una corrida real que use ese
dato antes de calibrar nada.

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
independientes entre sí (ninguna bloquea a otra). **P1.3a, P1.3b y P1.3c ya
cerraron** (ver abajo). El prerrequisito para cualquier trabajo futuro de
calibrar un timeout por modelo queda cumplido (P1.3a), pero esa
calibración en sí **todavía no es un punto propio de P1.3 y no se ha
iniciado** — sigue sin saberse si hace falta. El siguiente paso dentro de
P1.3 es **P1.3d**.

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

##### P1.3b — `expected_outputs` no se traduce en acceptance criteria exigibles — CERRADO (3 de agosto de 2026)

Un work item podía listar un entregable en `expected_outputs` (p. ej. el
Markdown de uso) sin que ningún `acceptance_criteria` lo forzara, y el
revisor semántico nunca lo evaluaba. Confirmado en 2 de los 3 falsos
`completed` de `csv_expenses_cli` (rep 2 y 3 — rep 1 falló únicamente por el
bug de agregación de P1.3c, sin relación con documentación faltante).

- `Orchestrator._with_expected_output_criteria` (nuevo): cada
  `expected_output` único genera su propio criterio determinista — "El
  entregable esperado existe y está completo: `<output>`" — sin intentar
  adivinar si otro criterio ya lo cubre por coincidencia de texto. Reutiliza
  `_unique_planning_entries` para el merge, así que aplicarla dos veces
  sobre una lista ya derivada nunca duplica nada.
- Aplicada en los tres sitios donde un `WorkItem` recibe
  `acceptance_criteria`: el plan inicial (criterios del modelo más los
  derivados de `task.expected_outputs`); las hijas de `_attempt_split` (la
  porción heredada del padre por posición sigue intacta — "la palabra del
  padre, nunca la del modelo" de ADR 0026 — más un criterio nuevo por cada
  `expected_output` propio de la subtarea); y la consolidación (re-derivada
  defensivamente sobre lo que ya trae el padre, no sólo copiada).
- No se tocan tareas ya persistidas: la derivación vive en el orquestador,
  no en el modelo de dominio `WorkItem`, así que sólo corre cuando una tarea
  nueva se construye.

**Criterio de salida cumplido, con evidencia sintética — sin Ollama real:**
12 pruebas deterministas nuevas, más 2 preexistentes actualizadas.

- Función pura (`test_expected_output_criteria.py`): un output, varios
  outputs, duplicados (no duplica el criterio), un output en prosa,
  idempotencia (aplicar dos veces no cambia el resultado), no adivina
  cobertura por texto libre, preserva los criterios del modelo.
- El plan inicial real (proveedor mock) deja el criterio derivado en cada
  tarea, junto a los del modelo.
- **Llega al revisor, y un resultado `false` bloquea la aprobación:** una
  tarea aislada con un único criterio (el derivado) y el revisor forzado a
  rechazarlo — el string exacto aparece en el payload que recibe el rol
  revisor, y el veredicto mecánico (`_merge_review_fragments`, ya
  existente, sin cambios) nunca aprueba mientras ese criterio quede en
  `false`. El proyecto no completa alrededor de eso.
- Split (`test_task_splitting.py`): las hijas heredan la palabra exacta del
  padre y además reciben su propio criterio derivado por su nuevo
  `expected_output`; una hija que declara el mismo output dos veces no
  duplica su criterio.
- Consolidación: no duplica lo que el padre ya traía derivado.
- 2 pruebas preexistentes (`test_decompose_criteria_mapping.py`)
  actualizadas: verificaban listas exactas de criterios en las hijas sin
  contar el nuevo derivado — una aserción desactualizada, no un fallo del
  cambio.

##### P1.3c — `SCRIPT_EXECUTION` acepta un contrato de ejecución declarado — CERRADO (3 de agosto de 2026)

El perfil interno ejecutaba cada script entregado sin los argumentos del
contrato real y sólo miraba el código de salida — no podía detectar una
salida numéricamente incorrecta, y un script que traga excepciones sin
`raise`/`sys.exit` siempre devuelve 0 pase lo que pase. Confirmado con el
bug real de las tres repeticiones de `csv_expenses_cli`/`qwen3:4b` en P1.2
(nombres de archivo hardcodeados en vez de `sys.argv`, excepción tragada
sin salida de error) — ver ADR 0027 y `findings.md`.

- `ScriptExecutionContract` (nuevo, `domain/models.py`): `entrypoint`,
  `args`, `produces` opcional (sólo existencia, no comparación de valores —
  no hay fixture oculto para un proyecto real de usuario, mismo motivo por
  el que ADR 0016 dejó fuera de alcance comparar salida entre tareas del
  DAG). `WorkItem` gana `execution_contract: ScriptExecutionContract | None`.
- `ValidationProfileExecutor.validate` usa el contrato cuando está presente:
  busca el `.py` entregado cuyo path coincide con el `entrypoint`
  declarado; si hay `produces` declarado, lo borra primero del directorio
  de la corrida (mismo principio que `_prepare_run` de `FunctionalCheck` —
  "la entrega no se lleva el crédito por un artefacto que ya traía
  puesto") y sólo después invoca el script con los `args` reales, en vez de
  a ciegas. Sin coincidencia (incluido el caso de cero `.py` entregados),
  falla de inmediato nombrando el entrypoint — nunca cae en silencio al
  modo ciego. **El contrato es la única autoridad una vez presente:**
  ningún otro `.py` de la misma entrega se ejecuta como `SCRIPT_EXECUTION`
  (sigue recibiendo `PYTHON_SYNTAX` igual que siempre) — correr a ciegas un
  módulo auxiliar válido para importar pero no pensado para correr solo lo
  rechazaría sin razón. Sin contrato, cero cambios de comportamiento.
- **Deliberadamente no se tocó** `planning/contracts.py`
  (`TaskProposal`/`SubtaskProposal`): agregar el campo ahí para que el LLM
  lo llene habría repetido el fallo que ADR 0020 y ADR 0024 ya confirmaron
  en vivo, dos veces, con `owned_paths`/`shared_component`. Consecuencia
  documentada, no un descuido: **hoy nada construye un `WorkItem` con
  `execution_contract`** — el mecanismo consumidor queda real, persistido y
  probado de punta a punta (`engine.py` lo pasa en la llamada real a
  `validate`), pero inerte en cualquier corrida en vivo hasta que exista una
  decisión aparte sobre cómo poblarlo.
- `VALIDATION_CONTRACT_VERSION` se queda en `profiles-v6` — a diferencia de
  ADR 0016, este cambio no altera el resultado de ningún work item
  construible hoy, así que no hay requisito obsoleto del que proteger a un
  reintento.

**Revisión antes de mergear encontró dos huecos de diseño reales**, ambos
corregidos en la misma PR antes de cerrar: (1) `produces` sólo comprobaba
existencia *después* de correr, así que un archivo preexistente (entregado
junto al script, o remanente de un intento anterior) hacía pasar a un
script que no hacía nada — corregido borrándolo antes de ejecutar. (2) el
diseño original corría además, a ciegas, cualquier otro `.py` de la
entrega que no coincidiera con el `entrypoint` — corregido para que el
contrato sea la única autoridad una vez presente, sólo se ejecuta el
entrypoint declarado.

**Criterio de salida cumplido, con evidencia sintética — sin Ollama real:**
22 pruebas deterministas nuevas (verificado con
`pytest backend --collect-only -q`, contando parametrización), más una
lista de columnas ya parametrizada extendida en una línea.

- `test_domain_validation.py` (+10 casos, 6 funciones — una parametrizada
  en 4 paths que escapan y otra en 2): `entrypoint` en blanco, con `../`,
  absoluto o con letra de unidad, y sin sufijo `.py` rechazados; `args` con
  entrada en blanco rechazado; `produces` con path que escapa rechazado;
  defaults (`args=[]`, `produces=None`) cuando se omiten.
- `test_validation_profiles.py` (+8, las 4 pruebas `SCRIPT_EXECUTION`
  preexistentes quedan intactas): argumentos declarados sí se usan (la
  forma exacta del bug de `sys.argv` ignorado); `produces` declarado que
  nunca aparece falla aunque el código de salida sea 0 (el caso concreto de
  la excepción tragada); **un `produces` preexistente en la entrega no basta
  para pasar** — se borra antes de ejecutar, y un script vacío sigue
  fallando; `entrypoint` que no coincide con ningún `.py` entregado falla
  nombrándolo, sin caer a modo ciego; mismo camino con cero archivos `.py`
  entregados; varios `.py` entregados, **sólo el entrypoint declarado se
  ejecuta** — el otro (que falla deliberadamente si corre) no aparece en
  absoluto entre los resultados; un contrato declarado sin ninguna palabra
  clave de prosa igual activa el perfil; `entrypoint` en un subdirectorio
  resuelve `produces` relativo a ese subdirectorio, no a la raíz del
  proyecto.
- `test_schema_migration.py` (+3): `execution_contract_json` sumado a la
  lista ya parametrizada de columnas que deben existir tras migrar una base
  legacy; +2 pruebas de round-trip real vía `Repository`/`Database` en
  `tmp_path` (un `WorkItem` con contrato sobrevive guardar y releer; una
  fila sin contrato carga `execution_contract` como `None`).
- `test_script_execution_contract_integration.py` (nuevo, +1): la única
  prueba que atraviesa el camino real completo —
  `Orchestrator._execute_work_item` → `WorkItem.execution_contract` →
  `validate(execution_contract=...)` —, no sólo una llamada directa al
  validador. `WorkItem` con contrato creado a mano (nada en el camino de
  planificación puede producir uno todavía) y persistido vía
  `service.repository`; el proveedor mock se intercepta sólo para la
  operación `work` de esa tarea (para controlar el contenido exacto de
  `tool.py`) y para capturar el payload real que le llega al tester en la
  operación `test`, confirmando ahí que el `SCRIPT_EXECUTION` real usó los
  `args` declarados y satisfizo `produces` — sin tocar Ollama.

##### P1.3d — trazabilidad `goal` → validador de los tres casos — CERRADO (3 de agosto de 2026)

El validador de `architecture_document` ("sección de decisiones de diseño")
exigía un encabezado dedicado que el `goal` del caso nunca pedía
literalmente, y produjo el falso negativo confirmado de P1.2: una entrega
de qwen3:8b (rep 2) con contenido de decisiones+alternativa completo y
correcto, sólo organizado bajo el encabezado de "Componentes" en vez del
suyo propio (ver `findings.md`). Al construir la tabla completa apareció un
**segundo hallazgo real**, no señalado antes: en `library_api_sqlite`, el
validador "documentación de los endpoints" no tenía ninguna base en el
`goal` — sólo aparecía en `expected_artifacts`, campo puramente descriptivo
que `runner.py` nunca pasa al modelo (`create_project(run.case.goal,
title)`, sólo `goal`). El requisito no fue comunicado al modelo, así que un
fallo en ese validador no podía atribuírsele justamente.

**Decisión: en ambos casos el requisito medido era real e intencional —
sólo que nunca se comunicó al modelo. El arreglo es extender el `goal`
para pedirlo explícitamente, no debilitar el validador.** Debilitar la
exigencia de encabezado en `architecture_document` habría reintroducido el
riesgo que el propio caso ya documentaba: el proveedor mock, que repite el
enunciado del objetivo, aprobaría un artefacto sin estructura real
(confirmado por `test_the_architecture_case_rejects_a_document_that_only_echoes_the_goal`,
que sigue pasando con el `goal` nuevo). `expected_artifacts` queda anotado
en `contracts.py` como lo que es: metadato para humanos, nunca mostrado al
modelo, incapaz de justificar un validador por sí solo.

Ambos casos suben de `schema_version: 1` a `2` porque cambia lo que miden.
`csv_expenses_cli` no se tocó — ya estaba totalmente trazable.

**Tabla `validador → frase del goal que lo justifica` — cero entradas
huérfanas:**

**`architecture_document`** (v2):

| Validador | Frase del `goal` |
|---|---|
| `file_exists` — Existe el documento en Markdown | "Crear un documento de arquitectura en Markdown" |
| `file_matches` — Hay una sección dedicada a los componentes | "secciones Markdown dedicadas a Componentes principales…" |
| `file_matches` — Hay una sección de decisiones de diseño | "…Decisiones de diseño…" |
| `file_matches` — Hay una alternativa considerada presentada en una lista o tabla | "presenta cada decisión y al menos una alternativa considerada mediante una lista o tabla" |
| `file_matches` — Hay un glosario con términos definidos | "…y Glosario." |
| `file_absent` — no entrega código ejecutable | "No incluyas código ejecutable ni archivos Python." |

**`csv_expenses_cli`** (v2, sin cambios en esta PR):

| Validador | Frase del `goal` |
|---|---|
| `file_exists` — entrypoint declarado por el contrato | "El programa debe llamarse exactamente `expenses.py`" |
| `file_exists` — documentación de uso en Markdown | "Incluye también un documento Markdown con las instrucciones de uso" |
| `file_matches` — el script lee un CSV de verdad | "lea un archivo CSV de gastos" |
| `file_matches` — la documentación explica cómo ejecutarlo | "instrucciones de uso" + comando exacto dado en el `goal` |
| `functional` — procesa el CSV fixture y produce el resumen correcto | comando, columnas de entrada y claves de salida, todo literal en el `goal` |

**`library_api_sqlite`** (v2):

| Validador | Frase del `goal` |
|---|---|
| `file_exists` — código Python de la API | "Crear una API REST en Python" |
| `file_matches` — la persistencia usa SQLite de verdad | "con persistencia en SQLite" |
| `file_matches` — se declara la validación del préstamo | "validar que un libro prestado no se pueda volver a prestar hasta que sea devuelto" |
| `file_exists` — existe el documento Markdown solicitado | "Incluye además documentación de los endpoints en un archivo Markdown" (nuevo) |

**Efecto secundario real, encontrado y corregido en la misma PR:**
`ledger.py::assert_comparable` compara explícitamente
`case_schema_version` de cada registro del ledger contra la versión del
caso recién cargado — aparte de la comparación de `runtime_identity`.
Subir la versión de `architecture_document` habría hecho que
`test_benchmark_identity.py` disparara `SuiteDrift` en varias pruebas por
una razón ajena a lo que cada una intentaba probar (sus registros de
ejemplo hardcodeaban `case_schema_version=1`). Corregido con un helper
(`_architecture_record`) que deriva la versión del caso realmente cargado
en vez de un literal fijo, aplicado en los 5 sitios que comparan contra
`_case()`, más el doble de ejecución de
`test_weights_that_change_between_runs_abort_the_matrix`.

**Criterio de salida cumplido:** tabla completa arriba, cero huérfanos.
`test_benchmarks.py`: la prueba que simula al proveedor mock repitiendo el
`goal` (antes mantenía una copia manual del texto viejo) ahora usa
`case.goal` directamente — no puede volver a desincronizarse del YAML; +2
pruebas nuevas confirman que el `goal` de cada caso nombra literalmente lo
que su validador corregido comprueba. `test_benchmark_identity.py`: 27/27
en verde tras la corrección de `case_schema_version` descrita arriba. No
se corrió la matriz, no se tocó Ollama, no se modificó el informe de P1.2
(`report.md`/`report.json`/`findings.md` intactos — el falso negativo fue
real bajo el `goal` anterior y así queda documentado, no borrado).

**Criterio de salida de P1.3 en conjunto: cumplido — las cuatro PR
(P1.3a/b/c/d) mergeadas.** Queda decidir, en otra sesión, si remedir con
una suite nueva o pasar directo a P2 con las capacidades ya conocidas.

### P2 — contrato real de capacidades del runtime

**Esfuerzo estimado:** 1–2 PR, 2–3 sesiones. Dividido en **P2.1**
(manifiesto + inclusión como datos) y **P2.2** (preflight +
`unsupported_capability`), misma disciplina de una hipótesis por PR que
P1.3a–d. El ítem 5 original (instalación con red, entorno por proyecto)
sigue fuera de alcance indefinidamente — para el MVP no se instalan
paquetes dinámicamente durante una tarea: mezcla ejecución con red y
autoridad, y complica mucho el aislamiento.

##### P2.1 — manifiesto de capacidades del runtime como datos — CERRADO (4 de agosto de 2026)

ADR 0020 agregó una frase de prompt avisando al worker que su sandbox es
stdlib-only, sin red — verificado en vivo, no cambió nada (el modelo
volvió a importar Flask, byte a byte idéntico). P2.1 prueba el siguiente
rung: los mismos hechos como datos estructurados, no sólo prosa.

- `RuntimeCapabilityManifest` (nuevo, `execution/capabilities.py`):
  `python_version` (intérprete real, `sys.version.split()[0]`, misma
  técnica que `identity.py`); `executables_allowed`/`executables_available`
  (de `commands.allow` en `security.yaml` — "permitido por política" y
  "de verdad resuelve en esta máquina vía `shutil.which`" son
  afirmaciones distintas a propósito); `third_party_packages_allowed`
  (nueva clave `packages.allowed`, vacía hoy); `network_policy:
  Literal["deny"]` (nueva clave `network`, no `str` simple — mismo
  patrón que `BenchmarkRunRecord.schema_version: Literal[2]`, y ampliarlo
  después exige tocar el tipo en código, no sólo una línea de YAML; en
  prompt, `network_policy` se describe siempre como política declarada
  que debe respetarse, nunca como aislamiento o acceso de red efectivo).
  Profundamente inmutable: todo campo colección es `tuple[str, ...]`, no
  `list[str]` — `frozen=True` por sí solo sólo bloquea reasignar el
  atributo, no `.append()` sobre una lista. Un `model_validator` rechaza
  cualquier `RuntimeCapabilityManifest` donde `executables_available` no
  sea subconjunto de `executables_allowed`. Una única instancia
  construida en `build_application()`, compartida entre `plan`,
  `plan_revision` y `work` — nunca reconstruida por separado, sólo
  serializada por separado en cada payload.
- **`executables_allowed` no es un sandbox del código entregado**: describe
  qué invoca el propio pipeline de Agentarium contra la entrega, nunca un
  límite sobre lo que un `subprocess` dentro del script podría hacer (ese
  límite no existe en ningún nivel hoy — `run_command` es código muerto,
  sin despachador, confirmado por grep completo).
- **Límite conocido, documentado a propósito, no resuelto:** ni
  `third_party_packages_allowed=[]` ni `network_policy="deny"` están
  mecánicamente forzados hoy en el camino real de `SCRIPT_EXECUTION`
  (`validation.py` invoca sin las flags `-E -s -S -B` que sí usa
  `functional.py`). Cerrar esa brecha en el mismo PR habría impedido medir
  después si un cambio de comportamiento vino del dato estructurado o de
  que el validador empezó a rechazar algo distinto — se deja como ítem
  futuro propio, no P2.2. Detalle completo, con citas textuales, en ADR
  0028.
- Las frases de prompt de ADR 0020 se **reescribieron** (no se agregaron
  al lado) para apoyarse en `SOLICITUD.payload.runtime_capabilities`.
  `plan_revision` gana su propia frase explícita: una reasignación de
  tareas al resolver un conflicto no puede asumir una capacidad fuera de
  lo declarado. `PLANNING_PROMPT_VERSION`, `WORKSPACE_PROMPT_VERSION` y
  `PLAN_REVISION_PROMPT_VERSION` subieron (esta última dos veces, dentro
  del mismo PR sin mergear, para que la cadena versión↔contenido nunca
  quede desalineada) — un payload nuevo es cambio de contrato (ADR 0003),
  con consumidor real en `benchmarks/runner.py::prompt_versions()`/
  `ledger.py` aunque no en el orquestador en vivo.

**Criterio de salida cumplido, con evidencia sintética — sin Ollama real:**
pruebas deterministas nuevas en `test_runtime_capabilities.py` (política
real produce `third_party_packages_allowed=[]`/`network_policy="deny"`/
ejecutables ordenados; `executables_available` es subconjunto de
`executables_allowed`, determinista vía `shutil.which` interceptado;
claves nuevas ausentes defaultean bien; `network_policy` inválido
rechazado, justificando el `Literal`), `test_operational_context.py`
(`operational()` incluye el manifiesto) y `test_planning_prompts.py`
(payload real de `plan` y de `plan_revision`, vía el fixture `service`
real, cargan la **misma instancia fuente serializada** — no dos copias
independientes que podrían divergir), más un test nuevo del validador de
subconjunto (`executables_available` con un elemento fuera de
`executables_allowed` se rechaza). `.\test.ps1` completo en verde.

**Fuera de alcance a propósito:** sin enforcement, sin preflight, sin
`unsupported_capability`, sin instalación, sin Ollama, sin matriz nueva,
sin tocar `"brief"` ni `"decompose"`.

##### P2.2 — preflight de imports + `unsupported_capability`

**Esfuerzo estimado:** 1 PR, 1 sesión. **Entregado — sólo imports**, no
"imports/comandos" como decía el ítem original: "comandos" (llamadas
`subprocess`/CLI dentro del código entregado) se separó como su propio ítem
futuro (ver más abajo), en vez de quedar a medio cumplir detrás de un
encabezado que sugería lo contrario. Detalle completo, con citas textuales,
en ADR 0029.

**Diseño:**
- Perfil de validación nuevo `IMPORT_PREFLIGHT` en `ValidationProfileExecutor`
  (`backend/agentarium/execution/validation.py`): estático, vía `ast.walk`
  completo (no sólo el nivel superior del módulo — un import dentro de una
  función o detrás de un `try/except ImportError` de fallback sigue siendo
  un import no permitido), contra `sys.stdlib_module_names` ∪
  `third_party_packages_allowed` del manifiesto de P2.1, con exención sólo
  para imports relativos y para módulos que son otro archivo de la misma
  entrega (evita falsos positivos entre tareas hermanas del mismo
  worktree). Nunca ejecuta el script: si falla, `SCRIPT_EXECUTION` para ese
  script se salta (ya se sabe inútil). `VALIDATION_CONTRACT_VERSION` sube a
  `profiles-v7` (mismo precedente que ADR 0016: activar un perfil nuevo
  bumpea el contrato).
- En el orquestador (`engine.py`), un fallo de `IMPORT_PREFLIGHT` corta
  **antes** de llamar a TESTER y CRITICAL_REVIEWER — ambos se llamaban
  igual hasta ahora aunque la validación técnica ya hubiera fallado por
  cualquier motivo — y reusa la escalera de reintento existente
  (`_request_changes`: `CHANGES_REQUESTED` → `READY` si quedan intentos, si
  no `_on_attempts_exhausted`, mismo camino de split que cualquier otro
  agotamiento).
- Cortar antes de TESTER tiene un costo que había que resolver: el único
  canal real que informaba a un reintento qué falló antes
  (`retry_guidance.prior_validation_failures`) se alimenta de un
  `TestReport`, que sólo existe si TESTER fue llamado. Canal nuevo,
  independiente: un evento (`unsupported_capability_detected`,
  `list_events_for_work_item` nuevo en `repository.py`) que
  `ContextBuilder.operational()` lee a través de **todos** los intentos
  previos del mismo work item y expone como
  `retry_guidance.cumulative_rejected_imports`. `WORKSPACE_PROMPT_VERSION`
  sube a `workspace-v11` (payload de `work` gana esa clave nueva).
- `FailureCategory.UNSUPPORTED_CAPABILITY` (ya existía, para clasificar un
  `ModuleNotFoundError` real post-hoc) gana una firma nueva y distinta en
  `taxonomy.py` para este camino — decir "ModuleNotFoundError" sería falso
  cuando el script nunca llegó a ejecutarse.

**Límite conocido, aceptado a propósito:** detección estática vía AST no
cubre imports dinámicos (`importlib.import_module`, `__import__`) ni
nombres armados en runtime — igual que `network_policy` en ADR 0028, es una
consecuencia mecánica donde es barato aplicarla, no una garantía
exhaustiva.

**Criterio de salida cumplido, para imports estáticos cubiertos por el
preflight** (el límite de la entrada anterior aplica: no para
`importlib`/`__import__`): el caso `library_api_sqlite` ya no puede llegar
tarde a un `ModuleNotFoundError` por esa vía — o usa una capacidad
declarada o falla temprano (antes de tester/revisor) con una explicación
accionable (qué módulo, por qué, y acumulada a través de reintentos).

**Fuera de alcance, explícitamente separado de este ítem:** preflight de
"comandos" (llamadas `subprocess`/CLI dentro del código entregado) — choca
con un límite que ADR 0028 ya dejó escrito a propósito
(`executables_allowed` describe lo que el propio pipeline de Agentarium
invoca contra una entrega, no un sandbox para las llamadas `subprocess` del
código entregado, así que "comandos permitidos para código entregado"
sería un concepto nuevo, no una extensión de ese campo). Queda como ítem
futuro propio, sin fecha. Tampoco enforcement real de
`network_policy`/aislamiento de `SCRIPT_EXECUTION` (brecha de ADR 0028,
sigue sin resolver, sigue sin ser P2.2).

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
   independientes:** ~~P1.3a instrumenta `queue_wait` vs.
   `generation_time`~~, ~~P1.3b hace exigible `expected_outputs`~~,
   ~~P1.3c le da a `SCRIPT_EXECUTION` un contrato de ejecución declarado~~
   y ~~P1.3d audita los validadores del benchmark por
   sobre-especificación~~ — las cuatro entregadas. **Decidido: pasar
   directo a P2 sin remedir P1 con una suite nueva por ahora** (P1.3c
   queda deliberadamente inerte en corridas reales; remedir ahora no
   cerraría esa incertidumbre — límite conocido aceptado).
4. **PR de capacidades (P2), dos PR:** ~~P2.1 manifiesto del runtime
   incluido como datos~~ entregado. ~~P2.2 preflight de imports + fallo
   temprano por capacidad no disponible~~ entregado. "Comandos" (subprocess/CLI
   en código entregado) quedó fuera, separado como ítem futuro propio.

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
