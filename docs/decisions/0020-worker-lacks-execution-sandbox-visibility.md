# ADR 0020: El worker no sabe que el sandbox de ejecución no instala paquetes

- Estado: aceptada
- Fecha: 2026-07-30

## Contexto

Tercer dominio de prueba (no web, no CLI de un archivo, no documento): API REST
en Python para una biblioteca (libros, autores, préstamos, persistencia
SQLite), contra qwen2.5-coder:7b vía la API real.

El worker generó `app.py` con Flask y Flask-SQLAlchemy — la elección idiomática
para "API REST en Python". `SCRIPT_EXECUTION` (ADR 0016) lo ejecutó de verdad y
falló correctamente: `ModuleNotFoundError: No module named 'flask'`. La
compuerta funcionó como debía; el problema es anterior a la compuerta.

El worker no recibe ninguna señal de que el script correrá en un entorno
aislado sin `pip install` ni red, ni de qué paquetes ya están disponibles en el
venv del proyecto. Irónicamente, FastAPI sí está instalado (es dependencia del
propio backend de Agentarium), pero el modelo no tiene forma de saberlo.

Los reintentos 2 y 3 no lo corrigieron: el candidato fue byte-for-byte
idéntico al rechazado ambas veces, cortado mecánicamente sin gastar tester ni
revisor. El modelo agotó los 3 intentos sin cambiar de enfoque — evidencia
real para el punto ya identificado en `PLANS.md` sobre mejorar la estrategia
de reparación cuando un candidato se repite en vez de corregirse.

A diferencia de ADR 0016 (nadie ejecutaba el script) y ADR 0018 (fuga de
contexto interno), esto no es específico de un dominio: cualquier objetivo que
combine `SCRIPT_EXECUTION` con la elección natural de una librería de terceros
(Flask/FastAPI para APIs, `requests` para HTTP, `pandas` para datos, etc.)
pisa el mismo problema.

## Decisión

Se agregó una frase explícita a la instrucción de `work` en `llm/prompts.py`:
un archivo `.py` sujeto a ejecución corre en un entorno aislado sin red y sin
instalación de paquetes, sólo puede usar la biblioteca estándar de Python
(ejemplos: `sqlite3`, `http.server`, `json`, `csv`, `argparse`), y si el
objetivo exige explícitamente una librería de terceros como parte de la
entrega, el worker debe declararlo como limitación en vez de producir un
script que no puede ejecutarse. `WORKSPACE_PROMPT_VERSION` avanza a
`workspace-v6`, mismo precedente que ADR 0014/0016/0018.

No se agregó un paso de `pip install` real antes de `SCRIPT_EXECUTION`: eso
ampliaría el acceso de red del ejecutor, fuera del alcance permitido sin
aprobación explícita del usuario. Tampoco se enumeraron en el prompt los
paquetes de terceros que sí están instalados en el venv compartido (p. ej.
FastAPI): acoplaría el contenido generado a las dependencias internas de
Agentarium, que pueden cambiar.

## Consecuencias

Es una instrucción de prompt, no una compuerta mecánica: no hay garantía de
que un modelo la respete siempre, igual que ADR 0018.

**Verificado en vivo y refutado.** Se recreó el mismo objetivo (workspace
`f3dd4e9f-2962-4761-a83d-5fac57576ff7`) contra qwen2.5-coder:7b inmediatamente
después del cambio. El worker volvió a importar Flask y a declararlo en
`requirements.txt` en ambos intentos generados (attempt-1 y attempt-2,
idéntica elección de librería en los dos). La aclaración de prompt no cambió
la decisión del modelo ni una vez. En esta corrida el DAG generado por el
planificador agrupó los criterios de aceptación de forma distinta a la
primera corrida y `SCRIPT_EXECUTION` no llegó a activarse (ninguna frase
disparadora en `acceptance_criteria`/`expected_outputs` de esa tarea), así
que no se pudo re-observar el `ModuleNotFoundError` puntual — pero la
pregunta que motivó el fix ("¿deja el modelo de usar librerías de terceros al
avisarle del sandbox?") queda respondida: no.

Conclusión, tal como preveía este documento: la causa pasa de "hace falta
aclarar el prompt" a "una aclaración de prompt no alcanza para este modelo en
este dominio". Las opciones reales que quedan, ninguna aplicada todavía:

1. Instalar dependencias declaradas (`requirements.txt`) antes de
   `SCRIPT_EXECUTION` en un entorno realmente aislado — implica ampliar
   acceso de red del ejecutor, fuera de alcance sin aprobación explícita del
   usuario (regla en `AGENTS.md`).
2. Aceptar como límite conocido y documentado: objetivos que combinan
   `SCRIPT_EXECUTION` con la elección idiomática de un framework de terceros
   no son alcanzables con el sandbox actual, sea cual sea el prompt.
3. Probar si un modelo distinto (qwen3:8b, qwen3:4b) sí respeta la
   aclaración — no probado aún; la comparación de modelos por rol sigue sin
   evidencia suficiente (ver `PLANS.md`).

No se implementó ninguna de las tres sin decisión explícita del usuario.
