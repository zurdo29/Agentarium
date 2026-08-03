# Hallazgos — suite `p1-baseline-2026-08`

Este documento es la adjudicación **manual** sobre el resultado **automático**
de `report.md` / `report.json` (mismo directorio). Esos dos archivos son la
salida histórica y no se editan a mano — son lo que la maquinaria de P1.1/
P1.1b/P1.2a calculó, y siguen siendo la fuente de verdad de *qué corrió y qué
categoría le asignó el clasificador*. Este archivo agrega la capa de juicio
humano que la maquinaria no puede hacer por sí sola: si un `false_completed`
señalado por los validadores es en verdad un fallo real, o si el validador se
equivocó. Nada de esto vino de re-correr ninguna corrida — es inspección
read-only de la base de datos de la corrida, los workspaces y el código.

## Adjudicación de los 4 falsos `completed`

El informe automático marca 4/27. La adjudicación manual es **3 confirmados,
1 falso negativo del validador**:

| Caso | Modelo | Rep. | Veredicto automático | Adjudicación manual |
|---|---|---|---|---|
| `csv_expenses_cli` | `qwen3:4b` | 1 | falso `completed` | **Confirmado** — bug real de agregación |
| `csv_expenses_cli` | `qwen3:4b` | 2 | falso `completed` | **Confirmado** — documentación ausente |
| `csv_expenses_cli` | `qwen3:4b` | 3 | falso `completed` | **Confirmado** — ambos: bug + documentación |
| `architecture_document` | `qwen3:8b` | 2 | falso `completed` | **Falso negativo del validador** |

### Los 3 confirmados (`csv_expenses_cli`/`qwen3:4b`)

Los tres `expenses.py` entregados ignoran `sys.argv` por completo — hardcodean
`input_file='input.csv'` / `output_file='result.json'` en vez de leer los
argumentos del contrato (`python expenses.py input.csv --output result.json`).
Los tres envuelven la lógica en `except Exception as e: print(...)` sin
`sys.exit`, así que el proceso siempre devuelve código 0 sin importar qué
pase. El perfil interno `SCRIPT_EXECUTION`
(`backend/agentarium/execution/validation.py:210-219`) ejecuta el script
**sin argumentos** y sólo mira el código de salida — no puede detectar nada
de esto, y de todos modos nunca ejecuta contra el fixture oculto del caso
(sólo el validador *funcional* del benchmark lo hace, y por diseño el modelo
nunca lo ve).

- **Rep 1:** bug real de agregación. `monthly_totals[month] = sum(...)`
  sobreescribe entre fechas del mismo mes en vez de acumular
  (`por_categoria` sí acumula bien con `+=`, por eso esa clave sale
  correcta). Verificado a mano contra el fixture: coincide exactamente con
  "el valor de la última fecha de ese mes".
- **Rep 2 y 3:** el plan (`decompose`) nunca creó una tarea de
  documentación. En rep 3 el Markdown queda listado en `expected_outputs`
  de la tarea de consolidación pero **no** en su `acceptance_criteria` — el
  revisor semántico nunca lo evaluó porque `expected_outputs` no se traduce
  en un check exigible.

Es inconsistencia real del modelo combinada con un gap estructural del
orquestador (sin oráculo de valores correctos; `expected_outputs` no fuerza
acceptance criteria) — no un defecto del harness de medición. Es exactamente
el falso `completed` que P1.1/P1.1b existen para contar.

### El falso negativo (`architecture_document`/`qwen3:8b` rep 2)

El `goal` del caso (`benchmarks/cases/architecture_document.yaml`) pide:

> "describe los componentes principales, las decisiones de diseño con al
> menos una alternativa considerada para cada una, y un glosario de los
> términos del dominio."

Eso es un requisito de **contenido**. El validador que falló
("Hay una sección de decisiones de diseño") exige además **estructura**: un
encabezado Markdown dedicado que matchee
`^#{1,6}[ \t]+.*(decisi[oó]n|dise[nñ]o)`. El propio comentario del caso YAML
explica por qué existe ese requisito estructural — defender contra un mock
(u otro modelo) que aprueba repitiendo el enunciado sin organizar nada — pero
el `goal` en sí **nunca pidió un encabezado separado** para las decisiones.

La entrega de `qwen3:8b` (`arquitectura.md`) tiene el contenido completo: 4
componentes, cada uno con su alternativa descartada explícita bajo
"**Alternativa Considerada**:", y un glosario bajo su propio encabezado. Lo
que hace distinto a este documento es que puso las decisiones **dentro** de
"## Componentes Principales" en vez de bajo un encabezado propio. El plan
interno incluso tenía una work item dedicada ("Detallar Decisiones de Diseño
con Alternativas") con acceptance criterion de contenido — cumplido — y el
revisor semántico la aprobó correctamente sobre esa base.

**Conclusión:** la entrega satisface el objetivo real del caso. El validador
exige una forma más estricta que la que el `goal` pide, y aquí rechazó una
entrega válida. Esto no descuenta nada de la calidad de `qwen3:8b` en esta
corrida puntual, y **no** implica que el revisor semántico interno necesite
una señal de estructura — el contenido era correcto, no había nada que el
revisor tuviera que atrapar. El punto de acción real es auditar el validador
mismo (ver P1.3d en `PLANS.md`).

**Recuento de falsos `completed` reales tras la adjudicación: 3/27, no 4/27.**
Sigue sin ser cero — el criterio de calidad de P1.2 sigue sin cumplirse.

## Diagnóstico corregido: `provider_failure` de `library_api_sqlite`/`qwen3:8b`

El hallazgo original (ver historial de PR #5) decía: *"el timeout fijo de
300s de `implementation_worker` corta a `qwen3:8b` porque necesita más de
300s para generar."* Esa conclusión iba más allá de lo que la evidencia
sostiene. Corrección:

```python
# backend/agentarium/agents/roles.py:91-94
response = await asyncio.wait_for(
    self.scheduler.run(lambda: provider.generate(request, definition)),
    timeout=definition.timeout_seconds,
)
```

```python
# backend/agentarium/execution/scheduler.py:22-30
async def run(self, call):
    self.queued += 1
    async with self._semaphore:      # <- espera de cola, cuenta contra el mismo timeout
        self.queued -= 1
        self.active += 1
        ...
        return await call()          # <- generación real
```

`asyncio.wait_for` empieza a contar **antes** de que la tarea entre al
semáforo, no desde que arranca `provider.generate()`. `model_concurrency`
(= `runtime_identity.concurrency`) es **1** en toda la suite —
`backend/agentarium/services/application.py:441`. En el proyecto
`45a0895a-b263-49d2-b6cf-df4ca907d8c4`, 4 tareas `implementation_worker`
quedaron listas al mismo tiempo; sólo una (`57eadd81`) llegó a ejecutar
`provider.generate()` y terminó en 293s con tokens reales
(`resource_usage_json`: `prompt_characters: 11455`). Las otras tres murieron
con `TimeoutError` a los ~300s, **con `prompt_characters: 0` las tres**.

Se revisó si ese `0` es evidencia de que nunca salieron de la cola. **No lo
es**: en el camino de fallo (`roles.py:144-150`), `ResourceUsage` se
construye sin pasar `prompt_characters`/`response_characters` en absoluto, así
que da `0` por *default* sin importar si la tarea nunca llegó a
`provider.generate()` o si llegó, mandó el prompt, y fue cancelada a mitad
de la respuesta. La telemetría actual no distingue ambos casos.

**Conclusión honesta: no sabemos si la inferencia sola de `qwen3:8b` supera
300s, o si el tiempo se fue mayormente en cola detrás del semáforo con
concurrencia 1.** Ambas explicaciones son consistentes con los datos que
hay. La tasa de completion 0% de `qwen3:8b` sigue sin ser comparable con los
otros modelos, pero por una razón más precisa: falta instrumentación, no
sólo falta calibración. Ver P1.3a en `PLANS.md` — separar `queue_wait_ms` de
`generation_ms` es un prerrequisito para calibrar cualquier timeout por
modelo, no un paso posterior.

## Qué cambia esto en la hoja de ruta

Ver la sección "P1.3" de `PLANS.md`: dividida en P1.3a–P1.3d, cada una su
propio PR. P1.3a (instrumentación de tiempos) bloquea a cualquier trabajo
futuro de calibración de timeout por modelo. P1.3d ya no pide que el revisor
semántico entienda de estructura — pide auditar los validadores del
benchmark por sobre-especificación respecto al `goal`, que es la causa real
del falso negativo de este documento.
