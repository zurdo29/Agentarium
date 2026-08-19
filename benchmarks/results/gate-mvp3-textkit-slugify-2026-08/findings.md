# Hallazgos — Gate-MVP.3 `textkit-slugify`

## Adjudicación

| Criterio | Resultado |
|---|---|
| `measurement_valid` | **true** |
| `candidate_passed` | **false** |

`measurement_valid=true`: identidad correcta y verificada antes de medir
(modelo, digest, proveedor, versión de Ollama y concurrencia idénticos a la
corrida original; commit de Agentarium cambiado a propósito, que es lo que
se mide), una sola corrida, cero intervención manual.

`candidate_passed=false`: 3 de los 10 puntos del criterio fallan.

| # | Criterio | Resultado |
|---|---|---|
| 1 | proyecto `completed` | ❌ `failed` (11.11%) |
| 2 | frontera de escritura respetada | ❌ ver sección 1 |
| 3 | evidencia estática etiquetada honestamente | ✅ ver sección 4 |
| 4 | ningún F821 integrado | ✅ el perfil corrió y pasó |
| 5 | patch aplicable | ✅ `git am` EXIT 0 |
| 6 | árbol post-patch == integrado | ✅ `4448c3ab…` en ambos |
| 7 | 2 tests anteriores + el nuevo en verde post-fix | ❌ ver sección 2 |
| 8 | test nuevo rojo pre-fix | ✅ `'-example-' != 'example'` |
| 9 | origen intacto | ✅ HEAD y hashes idénticos |
| 10 | `consistency.matches_git_history` | ✅ `true` |

---

## 1. Gate-MVP.1 no se armó: `expected_outputs` en prosa deja la frontera permisiva

**Hecho.** El work item `1b340098` declaró `owned_paths=[]` y
`expected_outputs=["Código modificado en \`textkit/slug.py\`"]`, y entregó
**dos** archivos: `textkit/slug.py` y `tests/test_slug.py`. El segundo está
fuera de su scope declarado. Se integró (`e40beb17`), y a partir de ahí las
tareas hermanas que sí tenían `tests/test_slug.py` como su trabajo real
quedaron bloqueadas con 13 `workspace_action_rejected` del tipo
*"Workspace file paths collide with files already owned by an unrelated
task: tests/test_slug.py"*.

**Por qué no se disparó Gate-MVP.1.** Evaluado directamente contra el
código real, no inferido:

```
merge_path_claims([], ['Código modificado en `textkit/slug.py`'])  ->  []
```

`_PATH_CLAIM_PATTERN` (`backend/agentarium/planning/contracts.py:18`) es
`^[^.\s][^\s]*\.[A-Za-z][A-Za-z0-9]{0,7}$`: anclado, exige un **token único
sin espacios**. La cadena del planificador es prosa con espacios, así que
no matchea; el conjunto de claims queda vacío y `_out_of_scope_paths`
devuelve `set()` sin comparar nada. Cero eventos
`workspace_own_scope_rejected` en toda la corrida.

**Esto no es un bug de Gate-MVP.1: es su límite documentado, ahora
observado en producción.** ADR 0040 lo dice explícitamente: *"Sin ningún
claim parseable, la frontera es permisiva — no una garantía universal."* La
decisión de ser permisivo se tomó a conciencia para no romper el patrón
dominante de tareas descritas en prosa.

**Lo que sí es nuevo y relevante.** La corrida original tenía
`expected_outputs=["textkit/slug.py"]` — un path pelado, parseable, contra
el que Gate-MVP.1 **sí** habría disparado. Esta corrida produjo prosa para
el mismo goal, mismo modelo y misma configuración. Es decir: **el formato
de `expected_outputs` que emite el planificador no es estable entre
corridas**, y de ese formato depende por completo que la frontera se arme.
Un gate que protege sólo cuando el LLM eligió el formato conveniente no es
una garantía; es una probabilidad. Ese es el hallazgo central de esta
medición, y no era observable antes de correrla.

## 2. El candidato borró los 2 tests originales, y eso ocultó una regresión real

**Hecho.** El `changes.patch` integrado elimina la clase `SlugifyTests`
completa —con `test_basic_lowercase` y `test_strips_accents`— y la
reemplaza por una clase `TestSlugify` con un único test nuevo. También
reescribe `slugify` perdiendo dos comportamientos: el `.lower()` y la
normalización de acentos vía `unicodedata`.

**La regresión, medida y no inferida** (Clon C: implementación integrada +
tests originales restaurados):

```
FAIL: test_basic_lowercase   AssertionError: 'Hello-World' != 'hello-world'
FAIL: test_strips_accents    AssertionError: 'Café-con-Leche' != 'cafe-con-leche'
```

Los dos tests que el candidato borró son exactamente los dos que habrían
expuesto su propia regresión. La implementación entregada además **no
cumple el ejemplo del goal**: `'  Cafe con Leche!!  '` produce
`'Cafe-con-Leche'`, no `'cafe-con-leche'`. Y el test nuevo tampoco cubre el
ejemplo pedido (`' Café con Leche!! '`), sino uno propio
(`'---example---'`).

**Gate-MVP.2 detectó la eliminación y la dejó visible.** El mecanismo
funcionó exactamente como se diseñó:

```
removed_top_level_names["tests/test_slug.py"] =
  ['SlugifyTests', 'SlugifyTests.test_basic_lowercase', 'SlugifyTests.test_strips_accents']
```

persistido en `TestReport.command_evidence` y presente en el
`summary.json` del export. Lo que **no** hace —por decisión explícita de
ADR 0041— es rechazar automáticamente. Por eso se integró igual.

## 3. El revisor recibió la evidencia de eliminación y la contradijo

**Hecho, citado literal.** Para el ítem `7ffc7c53`, con la lista de
eliminaciones **no vacía** en su payload, el `critical_reviewer` escribió:

> "No se han eliminado nombres de nivel superior en los archivos Python
> entregados"

Para `1b340098` —el que efectivamente se integró, con la misma lista no
vacía— aprobó sin mencionarlas en absoluto.

Esto sustenta con evidencia real el límite que ADR 0041 ya había declarado
en abstracto: *"un candidato puede seguir aprobándose aunque la lista no
esté vacía, si el reviewer lo justifica o simplemente lo ignora"*. La
medición muestra un caso peor que "ignorar": el revisor **afirmó lo
contrario del dato que tenía delante**. Reforzar el prompt no resolvería
esto; es la clase de fallo que ADR 0020 ya documentó (un prompt por sí solo
no garantiza nada).

## 4. Lo que sí mejoró respecto de la corrida original

No todo se repitió. Comparado con `mvp-candidate-textkit-slugify-2026-08`:

- **`verification_mode` honesto**: los 3 TestReports quedaron
  `static_only`, y el único ítem `completed` aparece en
  `unverified_completed_items` con `reason="static_only_verification"`.
  Antes no existía forma de distinguir evidencia estática de ejecutada.
- **`PYTHON_UNDEFINED_NAMES` activo**: corrió sobre ambos `.py`
  (`started=True`) y pasó. Esta vez el modelo no dejó un nombre indefinido,
  así que el perfil no tuvo que rechazar nada — pero está armado y
  funcionando, que es lo que el gate garantizaba.
- **Eliminaciones visibles**: `removed_top_level_names` capturó exactamente
  los 3 nombres borrados y viajó hasta el export.
- **P3.4 intacto**: cero `SCRIPT_EXECUTION`, cero
  `imported_project_execution_blocked` (nunca se pidió ejecución).

Es decir: **los dos gates hicieron lo que prometieron**. Gate-MVP.2 cumplió
por completo. Gate-MVP.1 cumplió su contrato tal como está escrito, pero su
contrato tiene un hueco que esta corrida atravesó.

## 5. Causa raíz del fallo del candidato

En una sola frase: **una subtarea escribió el archivo de su hermana porque
su propio scope no era mecánicamente legible, y el revisor aprobó una
entrega que borraba los tests que la habrían delatado.**

Las dos mitades son independientes y ambas necesarias:

1. Sin el hueco de la sección 1, el candidato fuera de scope no se habría
   materializado y la hermana no se habría bloqueado.
2. Sin el hueco de la sección 3, la eliminación de tests habría sido motivo de
   rechazo aunque el archivo se hubiera escrito.

## 6. Qué NO se hizo

No se corrigió nada. No se repitió la corrida. No se ajustó el goal, el
fixture, el modelo ni la configuración. No se abrió ADR: esto es una
medición, no una decisión arquitectónica. La decisión sobre qué hacer con
las secciones 1 y 3 queda para la revisión conjunta, sobre esta evidencia.
