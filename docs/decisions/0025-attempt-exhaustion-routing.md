# ADR 0025: Qué clase de fallo puede activar una división

- Estado: aceptada
- Fecha: 2026-08-02

## Contexto

Pedido explícito del usuario como "P0.1" tras aprobar el mecanismo de ADR
0024, sobre la base de un hallazgo que yo había reportado al cerrar ADR 0024:
que el candidato repetido byte-a-byte no llegaba a `_on_attempts_exhausted` y
por lo tanto la división de ADR 0021 nunca se activaba.

**Ese diagnóstico era incorrecto y hay que dejarlo escrito.** Al investigarlo
para implementarlo, el código y los datos dicen otra cosa:

1. La ruta autónoma (`Orchestrator._execute_work_item`, `engine.py:476`)
   **siempre** llamó a `_on_attempts_exhausted` al agotar intentos, incluido
   el caso del candidato repetido. No había nada que centralizar ahí.
2. Las dos líneas que yo había citado como "fallan directo"
   (`engine.py:556` y `engine.py:633`) están en `re_evaluate_artifact` y
   `evaluate_operator_candidate` — entradas manuales del operador, que no
   participan del bucle autónomo y nunca dividieron.
3. En la corrida de ADR 0024 (`8be5cde9-f1aa-4824-a41f-20a98596dd23`)
   `_attempt_split` **sí corrió** en las tres tareas fallidas: hay una corrida
   de `technical_manager` con operación `decompose` para cada una.

El error de lectura tuvo dos causas concretas, ambas mías: el script con el
que inspeccioné los eventos filtraba las corridas de `technical_manager` sin
querer, y una segunda corrida de `decompose` que aparecía en la traza y me
confundió era en realidad **mi propia sonda** de verificación, no del
orquestador.

**La causa real por la que no hubo división** es un paso más adelante: el
modelo devolvió una subtarea con `expected_outputs: []`, que viola el
`min_length=1` de `SubtaskProposal` (ADR 0021), así que
`DecomposeProposal.model_validate` levantó `ValidationError` y `_attempt_split`
devolvió `False`. Ejemplo textual de la corrida:

```json
{"subtasks": [
  {"title": "Implement API Endpoints", "expected_outputs": ["api_code.py"],
   "acceptance_criteria": ["API responds correctly to all requests"]},
  {"title": "Database Operations", "expected_outputs": [],
   "acceptance_criteria": ["Database operations are successful"]}
]}
```

Aun así el punto 2 del pedido del usuario sigue siendo un problema real e
independiente del diagnóstico equivocado, y es lo que este ADR corrige: la
ruta autónoma capturaba `(InvalidPlan, WorkspaceRejected, IsolationError)` en
un solo bloque y las trataba **todas igual**. Una violación de sandbox y un
fallo de disco podían reintentarse tres veces y después disparar una división
— responder con un cambio de planificación a algo que ninguna subtarea puede
arreglar.

## Decisión

Clasificar el fallo de un candidato en tres clases, por tipo de excepción, sin
inspeccionar mensajes.

`execution/workspace.py` gana dos subclases de `WorkspaceRejected` (que sigue
existiendo y sigue siendo la clase base que ven todos los llamadores
actuales):

- `WorkspaceSecurityRejected` — el contenido propuesto intentó salir de su
  sandbox: path que escapa del directorio autorizado, symlink cruzado,
  destino fuera del scope del proyecto, identificadores que no son componentes
  simples.
- `WorkspaceInfrastructureRejected` — falló la máquina, no la propuesta: el
  archivo no se pudo instalar (`OSError`), o un archivo ya integrado falló la
  verificación de checksum.
- `WorkspaceRejected` a secas queda para los límites de contenido (ningún
  archivo, demasiados archivos, archivo demasiado grande).

`Orchestrator._candidate_failure_policy(exc) -> (may_retry, may_split)`:

| clase | reintenta | divide |
| --- | --- | --- |
| `InvalidPlan` (artefacto malformado, path inseguro en la propuesta, candidato repetido, colisión de paths) | sí | sí |
| `WorkspaceRejected` (límites de contenido) | sí | sí |
| `WorkspaceInfrastructureRejected`, `IsolationError` | sí | **no** |
| `WorkspaceSecurityRejected` | **no** | **no** |

El razonamiento es que dividir una tarea es una respuesta de *planificación*:
sólo tiene sentido cuando el contenido propuesto estuvo mal de una forma que
un alcance más chico podría arreglar. Una violación de límite no se reintenta
porque reintentarla repite la violación; un fallo de infraestructura sí se
reintenta porque puede ser transitorio, pero la tarea nunca fue demasiado
amplia.

El path inválido generado por el modelo (`workspaces/<project_id>/api.py`, el
caso real de la corrida de ADR 0024) es `InvalidPlan` desde el validador de
`WorkspaceFileProposal`, o sea **corregible** — coincide con el ejemplo que
dio el usuario.

## Lo que se verificó explícitamente

- **El intento no se incrementa dos veces**: `increment_attempt` corre una
  sola vez, al principio de `_execute_work_item`; ninguna de las tres ramas
  nuevas lo vuelve a tocar. Test: una corrida que termina en división deja
  `attempt_count == 3` y las hijas nacen en 0, con su propio presupuesto.
- **El worktree rechazado se descarta antes de crear las hijas**: el
  `discard` ya estaba al principio del bloque `except`, antes de la llamada a
  `_on_attempts_exhausted`. Test con espías sobre `isolation.discard` y
  `repository.add_work_item` que compara el orden real. Se descubrió al
  escribirlo que el candidato repetido se rechaza **antes** de
  `isolation.prepare()`, así que en ese caso no hay worktree que descartar; el
  test usa un rechazo de `stage()` (posterior a `prepare`), que es la única
  forma en que el orden es observable.

## Pruebas

`backend/tests/test_attempt_exhaustion_routing.py`, 9 tests:

- clasificación en el origen (`_contained_file` levanta
  `WorkspaceSecurityRejected`, que sigue siendo un `WorkspaceRejected`), y la
  tabla de `_candidate_failure_policy` completa;
- candidato repetido en el último intento → división real, tarea `CANCELLED`,
  hijas creadas;
- path inválido generado por el modelo (la forma exacta de la corrida de ADR
  0024) → división;
- violación de seguridad → `FAILED` **con dos intentos todavía disponibles**,
  sin hijas y sin consumir el presupuesto restante;
- fallo de infraestructura → reintenta mientras queden intentos, y al agotarse
  termina en `FAILED` sin dividir;
- orden de descarte del worktree y conteo de intentos.

Suite completa: 153/153 en verde. Ruff y mypy limpios.

## Verificación en vivo

Corrida única, mismo objetivo de biblioteca y mismo proveedor
(qwen2.5-coder:7b), workspace `13ee7f71-5b5c-4c4c-951a-c238a19befda`.
Terminó `failed` al 66,7% (6/9 tareas completadas).

**La condición de cierre se cumplió.** La tarea de cierre
(`40f6025e`, 7 criterios de aceptación) agotó sus 3 intentos con
`Retry candidate files are byte-for-byte identical to the previous rejected
candidate`; el evento `workspace_action_rejected` quedó registrado con
`{"may_retry": true, "may_split": true}`, y hay una corrida real de
`technical_manager` con operación `decompose` para esa tarea en ese intento.
El candidato repetido activa `_attempt_split` en vivo, verificado sobre datos
de la corrida y no por inferencia.

Los 8 rechazos de la corrida llevaron la metadata de enrutado y los 8 se
clasificaron como corregibles (`InvalidPlan`: contenido con placeholders,
colisiones de path, candidato repetido). **Las ramas de seguridad e
infraestructura no se ejercitaron en vivo**: no ocurrió ninguna violación de
sandbox ni ningún fallo de disco en esta corrida, así que esas dos filas de la
tabla siguen cubiertas sólo por tests. Es lo esperable —son casos que no se
pueden provocar sin fabricarlos— pero hay que decirlo.

Otras tres tareas también agotaron intentos y **no** llamaron al rol
`decompose`, correctamente: tenían un solo criterio de aceptación cada una, y
`_attempt_split` corta antes de gastar una llamada al modelo cuando no hay
nada que repartir (guarda de ADR 0021).

**La división igual no llegó a crear hijas**, y esta vez por un motivo
distinto al de la corrida anterior: no hubo ninguna subtarea con
`expected_outputs: []`; falló el chequeo de cobertura de criterios. El modelo
propuso subtareas sobre "Instalar dependencias" (`requirements.txt`),
"Configurar entorno de desarrollo" (`README.md`) e "Implementar la API REST",
cuyos criterios no comparten texto con ninguno de los 7 criterios originales
("Listar libros", "Agregar libros", …), así que
`original_criteria.issubset(covered_criteria)` dio falso. Es el límite de
matching textual exacto de ADR 0021, ya documentado en ADR 0023 y confirmado
otra vez acá — no es de este ADR.

## Consecuencias

- Una violación de sandbox ahora es terminal e inmediata, en vez de gastar
  tres intentos y potencialmente generar subtareas. Es el cambio de
  comportamiento más visible.
- La división queda reservada para fallos de contenido, que es lo único que
  una descomposición puede arreglar.
- **Dos bloqueos distintos, ninguno corregido acá, que impiden que una
  división llegue a crear hijas con un modelo chico**. Se observó uno en cada
  corrida real, así que hay que tratarlos como igual de probables:
  1. `expected_outputs: []` en una subtarea contra el `min_length=1` de
     `SubtaskProposal` (corrida `8be5cde9`). Rellenarlo desde el padre, igual
     que ADR 0024 rellena `owned_paths`, sería consistente con la línea de
     "capa mecánica debajo".
  2. El chequeo de cobertura de criterios por matching textual exacto
     (corrida `13ee7f71`). Es el más difícil de los dos: exigirle a un modelo
     chico que repita literalmente los criterios del padre es la misma clase
     de dependencia de compliance que ADR 0020/0023/0024 ya mostraron que no
     se sostiene.

  Ambos son decisiones de contrato, fuera del alcance pedido para este ADR.
- Las dos entradas manuales del operador (`re_evaluate_artifact`,
  `evaluate_operator_candidate`) siguen sin clasificar el fallo: una violación
  de seguridad ahí todavía reintenta. Nunca dividieron, así que el riesgo que
  motivó este ADR no aplica; queda como inconsistencia menor documentada.
- Lección de proceso, más importante que el código: **reporté un diagnóstico
  equivocado con confianza porque la herramienta con la que miré los datos
  filtraba de más**. La regla de `PLANS.md` de verificar en vivo funcionó —
  lo que falló fue no verificar la *herramienta de inspección* antes de sacar
  una conclusión de su silencio. Ausencia de evidencia en un query propio no
  es evidencia de ausencia.
