# ADR 0016: Brecha de ejecución en entregas tipo herramienta de script

- Estado: aceptada
- Fecha: 2026-07-29

## Contexto

Se probó un dominio nuevo (no juego, no aplicación web) siguiendo la
recomendación pendiente en `PLANS.md`: una herramienta CLI en Python que
procesa un CSV de gastos de ejemplo, valida filas, calcula totales por
categoría y mes, y genera un informe Markdown y un resumen JSON. Se ejecutó el
mismo objetivo con qwen3:4b, qwen3:8b y qwen2.5-coder:7b vía la API real.

Con qwen3:4b el proyecto terminó `completed` con las seis tareas aprobadas por
tester y revisor. La comprobación independiente de los archivos integrados
mostró que no existe ningún script ejecutable: `docs/README.md` documenta
`python expenses.py`, pero ese archivo no existe en ningún lado del
workspace. `summary.json` tiene la clave `"2024-01"` repetida cuatro veces
(JSON válido, pero cada entrada se sobrescribe entre sí), por lo que no hay
totales por mes reales. El revisor semántico marcó como `true` "Los totales
de gasto por categoría y por mes deben ser calculados correctamente" y
"Documentar cómo ejecutarla y reproducir el resultado" sin que ninguna de las
dos afirmaciones fuera cierta.

Con qwen3:8b sí se generó un script real (`expenses_tool.py`) con lógica
genuina de validación y acumulación de totales, integrado por la tarea
"Desarrollo de la herramienta". Pero una tarea posterior y redundante del
mismo DAG ("Generación del resumen en JSON") escribió su propio
`summary.json` con datos que no coinciden ni en cantidad de filas ni en
categorías con el `expenses.csv` real del proyecto — son cifras plausibles
pero fabricadas, no la salida real de ejecutar `expenses_tool.py`. El proyecto
terminó `failed` por una tarea distinta que agotó intentos contra la
compuerta de candidato idéntico (ADR 0014), no por esta inconsistencia, que
ninguna compuerta detectó.

En ambos casos el gate técnico fijo (`python_syntax`, `json_syntax`,
`workspace_inventory`) pasó porque sólo verifica que el texto sea sintaxis
válida, nunca que el programa se ejecute ni que su salida coincida con otros
artefactos declarados como su resultado. El revisor semántico, cuyo trabajo es
precisamente detectar esto, tampoco lo hizo.

## Decisión

Se añade un perfil de validación fijo `SCRIPT_EXECUTION` (paralelo a
`WEB_APPLICATION`, que sí ejecuta comportamiento vía `web_smoke.py`) en
`ValidationProfileExecutor`. Se activa por señales conservadoras de
`acceptance_criteria` y `expected_outputs` de la tarea ("python", "script",
"linea de comandos", "command line", "terminal", "consola"), normalizadas sin
tildes, igual que `_web_contract_flags`. Cuando se activa:

- Si la entrega no incluye ningún archivo `.py`, la compuerta falla de
  inmediato con evidencia explícita ("no incluye ningún archivo fuente .py"),
  sin necesidad de ejecutar nada. Esto captura el caso qwen3:4b.
- Si incluye uno o más archivos `.py`, cada uno se ejecuta con
  `SafeCommandExecutor` (`python <archivo>`, cwd en su propio directorio,
  `stdin=DEVNULL` para que un `input()` falle rápido en vez de colgar,
  timeout de 20s como el resto de los perfiles) y debe salir con código 0.

El alcance es deliberadamente el de "en la misma entrega": esta compuerta
compara la existencia y el éxito de ejecución de un script contra los
criterios de la MISMA tarea que lo declara. No intenta reconstruir qué
archivos son "salida" de un script para diferenciarlos de archivos de entrada,
ni compara la salida real de un script contra artefactos escritos por una
tarea distinta y posterior del mismo DAG — eso habría requerido heurísticas
de clasificación entrada/salida no lo bastante generales para justificarse
todavía. Por eso el caso qwen3:8b (script real, pero un `summary.json`
fabricado por una tarea posterior y distinta) no queda cubierto por esta
versión; ver "Consecuencias".

`item.expected_outputs` ahora se pasa a `ValidationProfileExecutor.validate`
además de `item.acceptance_criteria`, porque en la evidencia real la frase
que activa la señal ("Herramienta de línea de comandos en Python") vivía en
`expected_outputs` de la tarea de cierre de ADR 0012, no en sus
`acceptance_criteria`.

`VALIDATION_CONTRACT_VERSION` avanza a `profiles-v6` siguiendo el mismo
precedente de ADR 0014, para que reintentos en curso no hereden como
requisito acumulado un fallo producido por la activación de un perfil nuevo.

Riesgo de seguridad aceptado explícitamente: esta es la primera vez que
Agentarium ejecuta código generado por el modelo en vez de sólo analizarlo
estáticamente (`web_smoke.py` nunca ejecuta JavaScript real). El proceso corre
dentro del worktree aislado y desechable de la evaluación (se descarta de
todas formas al terminar), con el mismo `SafeCommandExecutor` ya usado para
git y los chequeos de sintaxis: sin `shell=True`, cwd confinado al workspace,
entorno filtrado sin credenciales, y ahora también `stdin` cerrado. No hay
sandboxing de red ni de sistema de archivos más allá de eso — el mismo nivel
de confianza que ya existe hoy en la materialización de archivos del worker.

## Consecuencias

Un proyecto que reclama una herramienta ejecutable y no incluye ningún
archivo fuente, o cuyo script falla en tiempo de ejecución, ya no puede
terminar `completed`. El proyecto `CSV gastos - qwen3-4b` (workspace
`9d87210d-bdae-4c46-9563-f02fa6fa7da1`) queda como evidencia visible del
estado anterior a esta compuerta, igual que `Inventario local - prueba
genérica final` quedó para el sobreajuste que motivó ADR 0012: no debe
"arreglarse" aprobando manualmente ni regenerando el proyecto.

Sigue sin cubrirse el caso en que un script real existe pero una tarea
distinta y posterior del mismo DAG fabrica un artefacto de salida que no
proviene de ejecutarlo (caso qwen3:8b). Corregir eso de forma general
requeriría comparar salida real de ejecución contra artefactos de otras
tareas del mismo proyecto, con una noción confiable de qué archivo es
"entrada" y cuál es "salida" — queda como trabajo futuro explícito en
`PLANS.md`, no como algo ya resuelto.
