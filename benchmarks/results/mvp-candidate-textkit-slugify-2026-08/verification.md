# Verificación mecánica — candidato MVP `textkit-slugify`

Comandos reales ejecutados, en orden, con código de salida y evidencia dura.
Todos corridos localmente contra `runtime/agentarium.db` (sin aislar,
mismo criterio que P3.0) y contra un repo fixture fuera de `workspace_root`.

## 1. Entorno

```
$ ollama list
Error: ... dial tcp 127.0.0.1:11434 ... denegó expresamente
```
Servidor caído → se levantó (`ollama serve`, background) → reintentado:
```
$ ollama list
qwen2.5-coder:7b   dae161e27b0e   4.7 GB
```

```
$ .venv/Scripts/agentarium.exe doctor
14 ok, 1 advertencia(s), 0 error(es).
EXIT:0
```

## 2. Construcción del fixture (`textkit-slugify-source`)

```
$ git init --initial-branch=main
$ git commit -m "estructura inicial"        # b3572cc
$ git commit -m "agrega slugify"            # 10b0ed4
$ python -c "from textkit.slug import slugify; print(repr(slugify('  Cafe con Leche!!  ')))"
'-cafe-con-leche-'                          # bug reproducido antes de importar
$ git commit -m "agrega tests"              # d218a2f  (arrastró __pycache__ por error)
$ git rm -r --cached tests/__pycache__ textkit/__pycache__
$ git commit -m "limpia __pycache__ y agrega .gitignore"   # f3e6ba5 == imported_commit
$ git status --porcelain                    # vacío
$ python -m unittest discover -s tests -v
Ran 2 tests in 0.001s — OK
```

Hash baseline pre-import: ver `source-hashes.txt` (sha256 de los 5 archivos
trackeados). Recalculado después de toda la corrida (import, run, export,
2 clones de verificación) — **idéntico, cero diferencias**:
```
$ diff textkit-source-BEFORE.sha256 textkit-source-AFTER.sha256
(sin salida)
```
`HEAD` del origen sin moverse: `f3e6ba583a3688398fc7a339681f2767fa40de39`.

## 3. Import

```
$ agentarium project import <source> "<goal exacto>" -t "textkit-slugify"
EXIT:0
```
`imported=true`, `imported_source_path` correcto, `imported_commit=f3e6ba583a3688398fc7a339681f2767fa40de39`.

Verificación de fidelidad del goal (encoding corrupto en stdout redirigido,
dato real intacto):
```
$ printf '%s' "$GOAL" | sha256sum
eb6b09a0571e99485875bfad9dd2a6376659e6645d940e27330c361291b48d71 *-

$ python -c "... SELECT goal FROM projects WHERE id=... ; sha256(...)"
eb6b09a0571e99485875bfad9dd2a6376659e6645d940e27330c361291b48d71
len: 777
```
Coincide exacto.

## 4. Corrida única

```
$ agentarium project run f685b363-da54-4280-99cb-e2ca22b354b4
EXIT:0
status: "failed"
```
`project_run_started` 21:00:59.317 → `project_failed` 21:02:24.690.
12/12 `agent_runs.outcome=artifact_delivered`. Sin reintento manual, sin
tocar el goal, sin repair-center.

## 5. Export

```
$ agentarium project export f685b363-... -f patch -f bundle -o <dest>
EXIT:0
```
`consistency.matches_git_history: true`. Hubo `change_set_integrated` real
(1 evento) → no aplicó `no_exportable_changes`.

## 6. Contraprueba de dos clones

**Clon A** (clona el original, aplica el patch, corre la suite):
```
$ git clone <source> verify-clone-A
$ git am changes.patch
EXIT:0                                       # aplica limpio, sin conflictos
$ git rev-parse HEAD^{tree}
eb92017b5f126eadf8b592e1ca87d604c70d1b0a
$ python -m unittest discover -s tests -v
NameError: name 're' is not defined
Ran 1 test — FAILED (errors=1)
EXIT:1
```

Comparación de árbol contra el `main` real del proyecto en Agentarium:
```
$ cd workspaces/f685b363-.../project && git rev-parse main^{tree}
eb92017b5f126eadf8b592e1ca87d604c70d1b0a     # idéntico al del Clon A
```

**Clon B** (clona el original, se queda en `imported_commit`, recibe sólo
el `tests/test_slug.py` generado por el Clon A):
```
$ git clone <source> verify-clone-B
$ git rev-parse HEAD
f3e6ba583a3688398fc7a339681f2767fa40de39     # == imported_commit
$ cp verify-clone-A/tests/test_slug.py verify-clone-B/tests/test_slug.py
$ python -m unittest discover -s tests -v
AssertionError: '-cafe-con-leche-' != 'cafe-con-leche'
Ran 1 test — FAILED (failures=1)
EXIT:1
$ git status --porcelain
 M tests/test_slug.py                        # cambio local, nunca commiteado; origen intacto
```

## Resumen de hashes/árboles citados en `findings.md`

| Referencia | Valor |
|---|---|
| `imported_commit` | `f3e6ba583a3688398fc7a339681f2767fa40de39` |
| `integration_commit` (main del proyecto) | `a0ee047234306afb446c4b35dba07f4be02943b0` |
| Árbol post-patch (Clon A) | `eb92017b5f126eadf8b592e1ca87d604c70d1b0a` |
| Árbol `main` integrado (proyecto) | `eb92017b5f126eadf8b592e1ca87d604c70d1b0a` |
| Goal persistido (sha256) | `eb6b09a0571e99485875bfad9dd2a6376659e6645d940e27330c361291b48d71` |
| Origen antes/después (sha256 de 5 archivos) | idéntico — ver `source-hashes.txt` |

No se versiona: `runtime/agentarium.db`, `workspaces/` completos, los
clones `verify-clone-A`/`verify-clone-B`, `changes.bundle` (binario), ni
ningún log/proceso temporal. Quedan en el scratchpad local de la sesión que
corrió esto, disponibles si se pide preservarlos aparte.
