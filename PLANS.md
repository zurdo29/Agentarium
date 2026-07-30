# Estado, forma de trabajo y próximos pasos

Este archivo es el punto de entrada para continuar Agentarium con otro modelo o
en otra sesión. Complementa `AGENTS.md`, la arquitectura en `docs/architecture/`
y las decisiones detalladas en `docs/decisions/`.

## Qué es Agentarium

Agentarium transforma un objetivo en un brief, un DAG de tareas y artefactos
verificables. El backend FastAPI coordina roles, SQLite, proveedores LLM,
worktrees, validaciones, testing y revisión. La interfaz permite crear y
ejecutar proyectos, observar eventos, métricas, artefactos y aprobaciones, y
abrir una vista previa segura de los resultados web.

El runtime local actual usa Ollama con `qwen3:4b` por defecto; `qwen3:8b` y
`qwen2.5-coder:7b` también están descargados localmente y disponibles para
comparar por rol. El proveedor mock sigue siendo la prueba determinista de
regresión.

Nota operativa: Ollama corre como proceso local aparte y puede caerse sin
avisar (pasó una vez en esta sesión, sin relación con el código). Si una
llamada a `/api/runtime/provider` devuelve 409 con un mensaje de
`ConnectTimeout`, no es un bug de Agentarium: correr `ollama list` para
confirmar, y si no responde, reiniciar con `ollama serve`.

## Estado actual

Ya están implementados:

- Planificación estructurada con prompts versionados y contratos Pydantic.
- Ejecución real con Ollama, además de mock y OpenAI-compatible.
- Persistencia SQLite, API, CLI, interfaz, SSE, métricas y recuperación.
- Materialización de archivos con límites, checksums y rutas protegidas.
- Un worktree y una rama efímera por intento; sólo se integra un candidato
  aprobado.
- Perfiles objetivos para sintaxis, estructura web y comportamientos exigidos,
  incluyendo ejecución real (no sólo sintaxis) de scripts que se declaran como
  herramienta ejecutable (`SCRIPT_EXECUTION`, ADR 0016).
- Tester técnico y revisor semántico con la compuerta técnica como fuente de
  verdad. El revisor ahora también recibe el contenido de los artefactos de
  dependencia y evalúa consistencia contra ellos cuando existen (ADR 0017).
- Rechazo mecánico de candidatos cuyas rutas de archivo colisionan con una
  tarea no relacionada del mismo proyecto (ADR 0019).
- Ningún timeout o fallo de proveedor (tester, revisor, director, gestor
  técnico) puede tumbar una petición HTTP sin controlar: todos degradan la
  tarea o el proyecto a un estado terminal auditable (ADR 0015).
- Reintentos, recuperación de candidatos, revisiones posteriores y candidatos
  presentados por un operador sin saltarse las validaciones.
- Vista previa local aislada por proyecto.
- Code Context Engine (CCE) conectado por MCP para búsqueda de código y
  memoria entre sesiones (`context_search`, `session_recall`, etc.); ver
  `CLAUDE.md`.

La última verificación completa pasó con:

- Ruff sin errores.
- MyPy sin errores en 47 archivos.
- 121 pruebas backend.
- Lint y build de la interfaz.
- 2 pruebas de la interfaz renderizada.

Sólo quedan avisos de deprecación de dependencias; no hay fallos conocidos en la
suite.

## Cómo se ha venido trabajando

El ciclo usado hasta ahora es:

1. Dar a Agentarium una solicitud real sin ayudar al planificador.
2. Observar brief, DAG, intentos, eventos, candidatos, tester y revisor.
3. Comparar el producto integrado con la solicitud mediante comprobaciones
   independientes (leer los archivos reales, no confiar en el status ni en el
   resumen del artefacto).
4. Corregir la causa general, no el proyecto de ejemplo.
5. Añadir pruebas positivas y negativas, especialmente con otro dominio.
6. **Verificar cada fix en vivo contra el modelo real que reveló el
   problema**, no sólo con tests sintéticos — varias veces esa verificación
   reveló un segundo bug (a veces en el propio fix recién hecho). Repetir
   hasta que una corrida completa salga limpia de ese problema específico, sin
   perseguir cosas indefinidamente: si aparece algo nuevo y claramente
   distinto, se documenta como hallazgo separado en vez de seguir iterando en
   el mismo hilo.
7. Registrar las decisiones arquitectónicas en `docs/decisions/` y ejecutar
   `.\test.ps1` antes de dar algo por cerrado.

No se considera éxito que una demo "se vea bien". Deben coincidir objetivo,
alcance, contratos de tareas, archivos, comportamiento y evidencia.

## Aprendizajes recientes

### Sobreajuste inicial (ADR 0012, 0013, 0014)

El primer juego sirvió para crear validaciones objetivas, pero una prueba
posterior con una aplicación de inventario reveló sobreajuste y falsos éxitos.
Las correcciones generales resultantes:

- El DAG debe cubrir literalmente entregables, alcance y criterios de éxito del
  brief. Si el modelo omite algo, se agrega una tarea terminal de contrato.
- Una ambigüedad no bloqueante no puede reemplazar el objetivo principal.
- Los contratos de videojuegos sólo se activan con contexto explícito de juego.
  "Navegación por teclado" en un formulario usa reglas de accesibilidad, no
  movimiento de un jugador.
- Las aplicaciones comunes pueden activar contratos reutilizables para CRUD,
  búsqueda, filtros, confirmación de acciones destructivas, estado vacío,
  localStorage, importación/exportación JSON y guía de uso.
- Un reintento con archivos idénticos al candidato rechazado se corta antes de
  gastar worktree, tester y revisor.
- Si la compuerta técnica ya falló, la revisión semántica se acota a 3
  criterios representativos.

### Dominio CSV: primera prueba no-web/no-juego (ADR 0015, 0016)

Comparando qwen3:4b, qwen3:8b y qwen2.5-coder:7b con el mismo objetivo
(herramienta CLI que procesa un CSV de gastos) vía la API real:

- **Bug de resiliencia (ADR 0015)**: un timeout del tester o el revisor
  (`RoleExecutionError`) no estaba capturado dentro de `_evaluate_candidate`,
  a diferencia de la llamada al worker. Tumbaba la petición `/run` con un 500
  y, como `asyncio.gather` no cancela tareas hermanas cuando una levanta una
  excepción, otra tarea lista del mismo ciclo seguía mutando estado en
  segundo plano tras la respuesta fallida — producía además un 409 espurio al
  cambiar de proveedor justo después. Corregido en las 4 llamadas a rol que
  lo necesitaban (tester, revisor inicial, revisor focalizado, y más tarde
  también `brief`/`plan` en `plan_project`, que tiene el mismo problema pero
  usa `_fail_planning` en vez de `_request_changes` porque todavía no existe
  un `WorkItem`).
- **Hallazgo principal (ADR 0016)**: ninguna compuerta ejecutaba el artefacto
  que afirma ser un programa. Con qwen3:4b el proyecto terminó `completed`
  con las seis tareas aprobadas, pero no existía ningún script Python en el
  resultado integrado (el README documentaba `python expenses.py`, que no
  existe) y `summary.json` tenía la clave de mes repetida cuatro veces. Tester
  y revisor aprobaron igual. Corregido con el perfil `SCRIPT_EXECUTION`:
  rechaza si los criterios reclaman herramienta ejecutable sin ningún `.py`
  declarado, y si existe, lo ejecuta de verdad exigiendo código de salida 0.
  Alcance deliberadamente limitado a "en la misma entrega" (un solo artefacto)
  — el caso de una tarea *distinta* fabricando salida sin ejecutar nada quedó
  fuera de este ADR y se resolvió más tarde por otra vía (ver ADR 0017/0019).

### Dominio documento de arquitectura: repetido 4 veces (ADR 0017, 0018, 0019)

Segundo objetivo deliberadamente sin código (documento de arquitectura en
Markdown para un sistema de reservas de restaurante), repetido varias veces
contra qwen2.5-coder:7b en particular porque fue el que reveló más problemas:

- **Confirmación real de la prueba negativa de ADR 0016**: `SCRIPT_EXECUTION`
  no se activó ni una vez en ninguna corrida de este dominio (verificado en
  eventos reales). La disciplina de activación conservadora se sostiene con
  evidencia de extremo a extremo, no sólo con el test unitario sintético.
- **Bug de consistencia entre tareas (ADR 0017)**: el revisor semántico nunca
  recibía el contenido de los artefactos de dependencia — sólo el worker lo
  veía. Una tarea que combinaba el trabajo de otras tres produjo un glosario
  distinto al que ya existía en un archivo separado, y el revisor aprobó
  ambos porque no tenía con qué comparar. Corregido: `_review_payload` ahora
  incluye `dependency_artifacts`, y se inyecta un criterio estándar
  (`DEPENDENCY_CONSISTENCY_CRITERION`) sólo cuando hay dependencias
  aprobadas.
- **Bug descubierto verificando el fix anterior**: `ContextBuilder.
  dependency_artifacts` exigía que `acceptance_criteria_addressed` del
  artefacto de dependencia fuera superconjunto exacto de los criterios
  propios de esa tarea. Un modelo real dejó ese campo vacío en una entrega ya
  aprobada, y el filtro la excluía en silencio — el fix de ADR 0017 ni
  siquiera llegaba a activarse. Corregido: sólo se excluye cuando hay una
  señal real de desajuste (criterios declarados que no comparten nada con la
  dependencia), no por una lista vacía.
- **Fuga de contexto interno (ADR 0018)**: el worker recibe `project.
  decisions` (el registro interno de Agentarium, p. ej. "Alcance inicial
  conservador... un único hito vertical") sin ninguna aclaración de que es
  meta-información del orquestador. qwen2.5-coder:7b lo copió literalmente
  como si fuera una decisión de arquitectura real — la tercera vez que se vio
  esa familia de fuga con el mismo modelo (antes como jerga suelta "Single
  Hito Vertical"). Corregido con una aclaración explícita en el prompt de
  `work`. Es una instrucción de prompt, no una compuerta mecánica: no hay
  garantía de que un modelo la respete siempre.
- **Bug mecánico y más grave (ADR 0019)**: dos tareas *sin relación de
  dependencia entre sí* declararon el mismo path (`docs/design.md`). Al
  integrarse ambas, la segunda sobrescribió el contenido de la primera sin
  ningún aviso — pérdida de datos silenciosa, no sólo inconsistencia
  semántica. A diferencia de ADR 0017, esto es determinista y no requiere
  juicio de un revisor. Corregido con `_colliding_dependency_paths`, que
  rechaza un candidato cuya ruta ya pertenece a una tarea no emparentada.
  **La primera versión de este fix tenía el mismo tipo de bug que ADR
  0015/0017 ya habían enseñado a buscar**: sólo miraba `dependency_ids`
  directas, y rechazó a la propia tarea de cierre de ADR 0012 porque esa
  tarea sólo depende directamente del último nodo del DAG, no de los nodos
  que originalmente crearon el archivo varios pasos atrás. Corregido a
  cierre transitivo completo (`_transitive_dependency_ids`), con un test que
  reproduce esa forma exacta de DAG (abuelo → padre → cierre).

Cada corrección de esta sección se verificó ejecutando el objetivo real contra
el modelo que reveló el problema, no sólo con `test.ps1`.

### Comparación de modelos (evidencia insuficiente todavía)

Con ambos dominios, en varias corridas: qwen3:8b tiende a ser el más completo
en contenido pero el más lento (hasta ~968s en el caso CSV, ~768s en el de
arquitectura); qwen3:4b suele ser el más rápido; qwen2.5-coder:7b fue el que
más problemas reveló (fuga de contexto interno recurrente, a veces responde en
inglés pese a un objetivo en español, repite candidato idéntico con más
frecuencia al recibir `changes_requested`). Con el número de runs hecho hasta
ahora **no alcanza para convertir esto en una recomendación de modelo por
rol** — hace falta repetir con más objetivos y contar ocurrencias, no
impresiones de una sola corrida.

## Proyectos de auditoría que quedan visibles

No volverlos verdes desactivando contratos ni aprobando manualmente. Sirven
para comprobar una futura estrategia de reparación y como evidencia de en qué
estado estaba el sistema antes de cada corrección.

- `Inventario local - prueba genérica final`: seis tareas completadas y una
  revisión fallida. El modelo afirmó haber agregado edición, borrado,
  confirmación, estado vacío y guía, pero repitió los mismos archivos.
- `CSV gastos - qwen3-4b` (workspace `9d87210d-bdae-4c46-9563-f02fa6fa7da1`):
  terminó `completed` sin que exista un script ejecutable real. Verificado:
  correr `SCRIPT_EXECUTION` contra este mismo workspace ahora lo rechaza.
- `Doc arquitectura - qwen2.5-coder-7b` (workspace
  `d4679178-dd67-4fb2-ae26-3d9ab5db6ed9`): terminó `completed` con dos
  glosarios inconsistentes entre sí. Evidencia del estado anterior a
  ADR 0017/0018/0019.

## Próximos pasos recomendados

Los dos dominios de prueba (CSV y documento de arquitectura) ya se ejecutaron
varias veces cada uno, y cada hallazgo real que produjeron (ADR 0015-0019) ya
está implementado, con tests, y confirmado en vivo contra el proveedor real
que lo reveló — no quedan hallazgos abiertos sin corregir de esta ronda. En
este orden:

1. Probar un tercer dominio distinto (ninguno de los dos ya usados) para ver
   si aparece una cuarta clase de problema, o si ADR 0015-0019 ya cubren lo
   esencial y lo que sigue es afinar en vez de encontrar bugs nuevos.
2. Mejorar la estrategia de reparación cuando una tarea amplia repite un
   candidato: dividir la corrección en subtareas pequeñas en vez de regenerar
   todo el producto. (Quedó identificado pero no diseñado ni implementado
   todavía.)
3. Recopilar más runs (≥3 por modelo, mismos objetivos) antes de convertir
   cualquier observación de calidad/velocidad por modelo en una preferencia
   de modelo por rol.
4. Después avanzar hacia departamentos, plugins y políticas de autoridad
   administrables.

## Reglas para el próximo agente

- Preservar el worktree sucio y no modificar cambios ajenos.
- No modificar `.openai/hosting.json`, credenciales, CI/CD ni autoridad de red.
- No añadir reglas específicas de un dominio sin una prueba negativa en otro.
- No integrar un candidato porque su resumen diga que funciona.
- Mantener al tester técnico y al revisor semántico separados.
- Registrar decisiones arquitectónicas nuevas en `docs/decisions/`.
- **No dar un fix por terminado sólo porque los tests pasan**: si es
  razonable, correrlo en vivo contra el escenario real que lo motivó. Varias
  veces en esta sesión esa verificación reveló un segundo bug, incluso en el
  propio fix recién escrito (ADR 0017 y ADR 0019 ambos tuvieron una primera
  versión incompleta que sólo se detectó así).
- Ejecutar antes de entregar:

  `powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1`
