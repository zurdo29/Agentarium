# ADR 0042: Claims efectivos confiables (post-Gate-MVP.3, 1 de 2)

- Estado: aceptada
- Fecha: 2026-08-18

## Contexto

Gate-MVP.3 (`benchmarks/results/gate-mvp3-textkit-slugify-2026-08/`,
`measurement_valid=true` / `candidate_passed=false`) localizó con precisión
por qué la frontera de escritura de Gate-MVP.1 no evitó que una tarea
escribiera el archivo de su hermana: **nunca se armó**.

El work item que integró declaró `owned_paths=[]` y
`expected_outputs=["Código modificado en \`textkit/slug.py\`"]`. Evaluado
contra el código real, no inferido:

```
merge_path_claims([], ['Código modificado en `textkit/slug.py`'])  ->  []
```

`_PATH_CLAIM_PATTERN` (`planning/contracts.py`) está anclado `^…$` y exige
un token único sin espacios. La prosa no matchea, el conjunto de claims
queda vacío y `_out_of_scope_paths` devuelve `set()` — la frontera
permisiva que ADR 0040 eligió a propósito para no castigar el patrón
dominante de entregables descritos en prosa.

Lo decisivo: **la medición original del mismo caso produjo
`expected_outputs=["textkit/slug.py"]`**, un path pelado contra el que el
gate sí habría disparado. Mismo goal, mismo modelo, misma configuración —
sólo cambió cómo el planificador deletreó el archivo. Un gate cuya
activación depende de qué formato eligió el LLM no es una garantía; es una
probabilidad.

## Decisión

Dos mecanismos, ninguno de los cuales toca prompts ni modelos.

### 1. Extracción conservadora de paths entre backticks

`implicit_path_claims` ahora, además del intento sobre la entrada completa
(**sin cambios**), escanea los segmentos entre backticks. Un token de ahí
cuenta como claim sólo si:

1. pasa `_PATH_CLAIM_PATTERN`;
2. pasa las validaciones de path relativo seguro ya existentes (nada
   absoluto, sin `:`, sin `..`, sin segmentos vacíos) — factorizadas en
   `_safe_relative_path` para que la ruta nueva no pueda divergir de la
   vieja;
3. **parece un archivo** (`_looks_like_a_file`): contiene `/` o su
   extensión está en `_FILE_SUFFIXES`.

**El punto 3 no es decorativo.** Verificado contra el patrón real:
`re.sub`, `str.strip` y `os.path` lo matchean. Sin ese filtro, un
`expected_outputs` que dijera "Usar \`re.sub\` para limpiar" pasaría de *sin
claims* (frontera desarmada) a *claims = {re.sub}* — armando la frontera
con un path inexistente y **rechazando la propia entrega legítima de la
tarea**. Eso es estrictamente peor que no detectar nada, así que el filtro
es parte del mecanismo, no un extra.

**La asimetría entre entrada completa y token embebido es deliberada.** Un
`expected_output` que *es* el path es una declaración inequívoca de
intención y conserva la regla original, más laxa. Un token embebido en
prosa es evidencia más débil y exige más señal. No se tocó la semántica de
la entrada completa: cambiarla arriesgaba regresiones sin evidencia que lo
justificara. Una extensión no listada significa "no se detectó claim", que
es el comportamiento permisivo preexistente, nunca un claim falso.

**Un solo punto de cambio cubre planificación y frontera**, porque ambos ya
consumían el mismo helper: `TaskProposal`/`SubtaskProposal.claimed_paths()`
→ `_detect_owned_path_conflicts` (preflight de planificación),
`_out_of_scope_paths` (frontera de escritura) y
`_normalized_decompose_content`, donde los claims se convierten en los
`owned_paths` de cada subtarea. **Esa tercera vía es la que habría evitado
el incidente**: el padre tenía la prosa con backticks, así que sus
subtareas heredaban `owned_paths=[]`; ahora heredan el path real.

### 2. Respaldo por claims de hermanas no relacionadas

`_sibling_claimed_paths(item)` se consulta **sólo cuando los claims propios
están vacíos** — exactamente cuando la frontera queda desarmada. Compara la
entrega contra los **claims declarados** de las demás tareas
(`owned_paths`/`expected_outputs`), no contra sus artifacts.

Esa diferencia es la razón de ser del mecanismo.
`_colliding_dependency_paths` lee `Artifact`s ya materializados, así que es
reactivo: en Gate-MVP.3 el item que integró corrió *primero*, cuando ninguna
hermana tenía artifact, y no colisionó con nada; las hermanas chocaron
después (13 `workspace_action_rejected`). Leer declaraciones cierra ese
agujero de orden.

Sólo cuentan tareas **realmente no relacionadas**. Se excluyen la propia
tarea, sus ancestros transitivos, sus **descendientes** transitivos, las
`CANCELLED`, y el caso de `shared_component` compartido cuando la estrategia
del candidato no es `EXCLUSIVE`.

**La exclusión de descendientes es nueva y no está calcada de
`_colliding_dependency_paths`.** Aquel sólo excluye ancestros, y le alcanza:
un descendiente que todavía no corrió no tiene artifacts con los que
colisionar. Acá el problema aparece precisamente por leer **declaraciones
futuras**: una tarea de cierre `BLOCKED` que depende del ítem actual ya
declaró sus `expected_outputs`, y sin esta exclusión reservaría por
anticipado los archivos de su propio prerequisito y lo bloquearía. El grafo
hay que mirarlo en las dos direcciones justamente porque las declaraciones
existen antes que el trabajo. Por eso `_transitive_dependent_ids` es nuevo:
no existía helper de grafo inverso.

El evento `workspace_sibling_claim_rejected` emite los paths rechazados
**y** el mapeo `path -> work_item_ids` que los reclama. Adjudicar Gate-MVP.3
obligó a releer la base para saber quién reclamaba qué; el evento tiene que
bastar por sí solo. Sigue lanzando `InvalidPlan`, así que la política de
reintento/split no cambia.

`_out_of_scope_paths` sigue siendo puro (ADR 0040 lo documenta así); el
lookup vive en el método nuevo y `_reject_sibling_claimed_write` se encadena
en los tres puntos de integración que ya existían.

## Alcance honesto

**El respaldo del punto 2 no habría atrapado el incidente de Gate-MVP.3 por
sí solo.** La hermana `464cf1f8` también tenía su `expected_outputs` en
prosa, así que sus claims también estaban vacíos: no había nada contra qué
comparar de ninguno de los dos lados. El mecanismo que carga el peso es el
punto 1; el punto 2 es defensa en profundidad para la forma residual — el
que escribe no tiene claims legibles, pero una hermana sí.

## Riesgo evaluado y descartado

`DecomposeProposal.validate_no_unresolved_path_overlap` **lanza**
`ValueError` si dos subtareas reclaman el mismo path con `EXCLUSIVE` de
algún lado, y más claims visibles podrían producir rechazos nuevos.
Verificado que no ocurre: el orquestador llama
`DecomposeProposal.model_validate(self._normalized_decompose_content(...))`
— la normalización corre **antes** y fuerza `output_strategy=FRAGMENT` en
todas las subtareas, así que al validar ambos lados son no-exclusivos. A
nivel plan el solapamiento no lanza: `_detect_owned_path_conflicts` pide una
revisión acotada y luego continúa.

## Límites

- **No cubre lo que el planificador no nombra.** Si `expected_outputs` no
  menciona el archivo de ninguna forma, no hay claim y la frontera sigue
  permisiva. Esto reduce la dependencia del formato, no la elimina.
- La lista `_FILE_SUFFIXES` es finita: un archivo con extensión inusual
  dentro de backticks y sin `/` no se detecta. Falla hacia el
  comportamiento actual (permisivo), nunca hacia un claim falso.
- El respaldo del punto 2 sólo protege paths que **alguna otra tarea
  declaró**. Una tarea sin claims que escribe un archivo que nadie reclamó
  sigue sin encontrar oposición mecánica.
- No es una sandbox: sigue siendo coordinación entre tareas, mismo límite
  que ADR 0040 ya fijó.
- No toca la compuerta de eliminaciones en proyectos importados — es la
  corrección 2, en otro PR.

## Verificación

`test_planning_prompts.py`: los tres strings exactos que produjo Gate-MVP.3,
leídos de la base de esa corrida; `` `re.sub` ``/`` `str.strip` ``/
`` `os.path` `` no producen claim; `` `INFORME.md` ``/`` `config.yaml` `` sí;
contenido inseguro entre backticks se ignora; entradas sin backticks se
comportan igual que antes; deduplicación entre ambas grafías; varios paths
en una misma entrada.

`test_evaluation_contracts.py`: hermana realmente no relacionada
(rechazada), descendiente directo e indirecto (permitidos), ancestro
transitivo (permitido), reclamante `CANCELLED` (ignorada),
`shared_component` no-exclusivo (permitido) y candidato `EXCLUSIVE` dentro
del grupo (rechazado), y el evento conservando `claimed_by`.

`test_effective_write_boundary.py`: la forma exacta de Gate-MVP.3
end-to-end, ahora rechazada antes de que `isolation.prepare` toque disco;
el caso del respaldo; y la regresión del descendiente, donde la entrega
debe pasar.

`ruff`, `mypy` y `.\test.ps1` completos en verde. Ningún fixture
preexistente necesitó ajuste.
