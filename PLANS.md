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
  herramienta ejecutable.
- Tester técnico y revisor semántico con la compuerta técnica como fuente de
  verdad.
- Reintentos, recuperación de candidatos, revisiones posteriores y candidatos
  presentados por un operador sin saltarse las validaciones.
- Vista previa local aislada por proyecto.

La última verificación completa pasó con:

- Ruff sin errores.
- MyPy sin errores en 47 archivos.
- 115 pruebas backend.
- Lint y build de la interfaz.
- 2 pruebas de la interfaz renderizada.

Sólo quedan avisos de deprecación de dependencias; no hay fallos conocidos en la
suite.

## Cómo se ha venido trabajando

El ciclo usado hasta ahora es:

1. Dar a Agentarium una solicitud real sin ayudar al planificador.
2. Observar brief, DAG, intentos, eventos, candidatos, tester y revisor.
3. Comparar el producto integrado con la solicitud mediante comprobaciones
   independientes.
4. Corregir la causa general, no el proyecto de ejemplo.
5. Añadir pruebas positivas y negativas, especialmente con otro dominio.
6. Registrar las decisiones arquitectónicas y ejecutar `.\test.ps1`.

No se considera éxito que una demo “se vea bien”. Deben coincidir objetivo,
alcance, contratos de tareas, archivos, comportamiento y evidencia.

## Aprendizajes recientes

El primer juego sirvió para crear validaciones objetivas, pero una prueba
posterior con una aplicación de inventario reveló sobreajuste y falsos éxitos.
Las correcciones generales resultantes son:

- El DAG debe cubrir literalmente entregables, alcance y criterios de éxito del
  brief. Si el modelo omite algo, se agrega una tarea terminal de contrato.
- Una ambigüedad no bloqueante no puede reemplazar el objetivo principal.
- Los contratos de videojuegos sólo se activan con contexto explícito de juego.
  “Navegación por teclado” en un formulario usa reglas de accesibilidad, no
  movimiento de un jugador.
- Las aplicaciones comunes pueden activar contratos reutilizables para CRUD,
  búsqueda, filtros, confirmación de acciones destructivas, estado vacío,
  localStorage, importación/exportación JSON y guía de uso.
- Los ids HTML duplicados y los controles sin nombre accesible se rechazan.
- Las entregas integradas tienen mayor presupuesto de contexto para no truncar
  JSON. Ollama informa explícitamente cuando alcanza el límite.
- Un reintento con archivos idénticos al candidato rechazado se corta antes de
  gastar worktree, tester y revisor, aunque el resumen afirme que fue corregido.
- Si la compuerta técnica ya falló, la revisión semántica se acota y el resto de
  criterios queda conservadoramente en falso.

Las decisiones correspondientes están en ADR 0012, 0013 y 0014.

Se ejecutó la primera prueba de dominio no-juego/no-web (herramienta CLI que
procesa un CSV de gastos), comparando qwen3:4b, qwen3:8b y qwen2.5-coder:7b con
el mismo objetivo vía la API real. Resultados:

- Se reprodujo y corrigió un bug real: un timeout del tester o el revisor
  (`RoleExecutionError`) no estaba capturado dentro de `_evaluate_candidate`
  (a diferencia de la llamada al worker, que sí lo estaba). Tumbaba la petición
  `/run` con un 500 y, como `asyncio.gather` no cancela tareas hermanas cuando
  una levanta una excepción, otra tarea lista del mismo ciclo seguía mutando
  estado en segundo plano tras la respuesta fallida — eso producía además un
  409 espurio al cambiar de proveedor justo después. Ver ADR 0015.
- Hallazgo más importante: **ninguna compuerta ejecutaba el artefacto que
  afirma ser un programa.** Con qwen3:4b el proyecto terminó `completed` con
  las seis tareas aprobadas, pero no existía ningún script Python en el
  resultado integrado (el README documentaba `python expenses.py`, que no
  existe) y `summary.json` tenía la clave de mes repetida cuatro veces, así
  que no había totales reales por mes. Tester y revisor aprobaron ambos
  criterios de todas formas. Con qwen3:8b sí se generó un script real y
  funcional, pero una tarea posterior del mismo DAG escribió su propio
  `summary.json`/`report.md` con cifras fabricadas que no coinciden con el
  CSV real del proyecto — nadie ejecutó el script para comparar. Corregido
  parcialmente: ver ADR 0016 (implementado — perfil `SCRIPT_EXECUTION`, cubre
  el caso qwen3:4b; el caso qwen3:8b de inconsistencia entre tareas queda
  documentado como pendiente, no resuelto).
- Señal comparativa entre modelos: qwen3:4b completó las seis tareas en un solo
  intento cada una (pero con la salida hueca de arriba); qwen3:8b y
  qwen2.5-coder:7b, al recibir `changes_requested`, repitieron el candidato
  byte-por-byte en el siguiente intento con más frecuencia (activando la
  compuerta de ADR 0014 y agotando presupuesto sin una segunda entrega real).
  Con un solo run por modelo no alcanza para generalizar esto; hace falta
  repetir con más objetivos antes de sacar una conclusión sobre qué modelo usar
  por rol.

## Proyectos de auditoría que quedan visibles

`Inventario local - prueba genérica final` terminó con seis tareas completadas y
una revisión fallida. Ese estado es intencional y correcto: el modelo afirmó
haber agregado edición, borrado, confirmación, estado vacío y guía, pero repitió
los mismos archivos. Antes de las mejoras habría mostrado 100 %; ahora conserva
el fallo y su evidencia.

`CSV gastos - qwen3-4b` (workspace `9d87210d-bdae-4c46-9563-f02fa6fa7da1`)
terminó `completed` sin que exista un script ejecutable real; ver ADR 0016.
Queda como evidencia de la brecha de ejecución que existía antes de esa
compuerta, análoga al caso de inventario. Verificado manualmente: correr el
nuevo perfil `SCRIPT_EXECUTION` contra este mismo workspace ahora lo rechaza.

No volverlos verdes desactivando contratos ni aprobando manualmente. Pueden
usarse para comprobar una futura estrategia de reparación.

## Próximos pasos recomendados

Los pasos originales 1 y 4 ya se ejecutaron (dominio CSV, 3 modelos) y el
hallazgo principal que produjeron (ADR 0016) ya está implementado. En este
orden:

1. Repetir la comparación de modelos con al menos otro objetivo de dominio no
   web antes de sacar conclusiones sobre calidad/reintentos por modelo; un solo
   run por modelo no alcanza para generalizar el patrón de "repetir candidato
   idéntico" observado con qwen3:8b y qwen2.5-coder:7b. De paso, confirmar que
   `SCRIPT_EXECUTION` no genera falsos positivos en un dominio que no reclame
   ser una herramienta ejecutable (prueba negativa de dominio, disciplina de
   ADR 0013).
2. Extender ADR 0016 al caso qwen3:8b todavía sin cubrir: un script real existe,
   pero una tarea distinta y posterior del mismo DAG fabrica un artefacto de
   salida que no proviene de ejecutarlo. Requiere una noción confiable de qué
   archivo es "entrada" y cuál es "salida" entre tareas del mismo proyecto;
   no hacerlo con heurísticas frágiles.
3. Mejorar la estrategia de reparación cuando una tarea amplia repite un
   candidato: dividir la corrección en subtareas pequeñas en vez de regenerar
   todo el producto.
4. Después avanzar hacia departamentos, plugins y políticas de autoridad
   administrables.

El `RoleExecutionError` sin capturar en `brief`/`plan` (antes pendiente en
ADR 0015) ya se corrigió: `plan_project` ahora usa `_fail_planning` para
marcar el proyecto `failed` en vez de tumbar la petición.

## Reglas para el próximo agente

- Preservar el worktree sucio y no modificar cambios ajenos.
- No modificar `.openai/hosting.json`, credenciales, CI/CD ni autoridad de red.
- No añadir reglas específicas de un dominio sin una prueba negativa en otro.
- No integrar un candidato porque su resumen diga que funciona.
- Mantener al tester técnico y al revisor semántico separados.
- Registrar decisiones arquitectónicas nuevas en `docs/decisions/`.
- Ejecutar antes de entregar:

  `powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1`
