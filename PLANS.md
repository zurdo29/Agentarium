# Agentarium — estado y hoja de ruta

Este documento es la guía operativa para continuar el proyecto. No es una
bitácora de sesiones: el detalle histórico y las decisiones ya cerradas viven
en `docs/decisions/`. Si una investigación no cambia la arquitectura, debe
quedar en el commit, el issue o el informe de benchmark correspondiente, no
crecer indefinidamente aquí.

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

## Foto actual — 1 de agosto de 2026

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
- Vista previa local aislada por proyecto.

### Evidencia disponible

- Última verificación registrada: Ruff y MyPy limpios; 134/134 pruebas backend
  en verde, sin `xfail` ni exclusiones.
- El build, lint y las dos pruebas web pasaron antes de los últimos cambios de
  backend, pero no se reverificaron en la sesión del último commit.
- La concurrencia entre un `project run` y lecturas repetidas de
  `project status` se verificó con un modelo real sin nuevas transiciones
  inválidas.
- Los detalles y reproducciones de los fixes recientes están en ADR 0015–0023.

### Riesgos y límites actuales

| Área | Evidencia actual | Consecuencia |
|---|---|---|
| Propiedad de archivos | El modelo sigue usando `expected_outputs` y suele dejar `owned_paths` vacío | El preflight de ADR 0023 puede no ver un conflicto real |
| División de tareas | La cobertura exige coincidencia textual de criterios | Una división razonable puede rechazarse aunque cubra el significado |
| Dependencias de ejecución | El worker puede elegir paquetes no disponibles en el sandbox | El fallo aparece tarde, después de gastar inferencias e intentos |
| Evaluación de modelos | Hay corridas útiles, pero no una matriz repetible | No se puede elegir modelo por rol con evidencia suficiente |
| Mantenibilidad | `engine.py` tiene 2051 líneas y `app/page.tsx` 2207 | Cada cambio cruza demasiadas responsabilidades |
| Interfaz | Sólo hay dos pruebas de render/strings; no prueban interacciones reales | Reintentos, acciones y SSE pueden romperse sin señal temprana |
| Persistencia | Las columnas nuevas se migran manualmente desde `create_all()` | El riesgo aumenta con cada evolución del esquema |
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

### P0 — cerrar el bucle de planificación actual

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

### P1 — benchmark reproducible y taxonomía de fallos

**Esfuerzo estimado:** 2 PR, 4–6 sesiones más tiempo de inferencia en segundo
plano.

1. Versionar tres casos en `benchmarks/cases/`:
   - CLI de gastos CSV;
   - documento de arquitectura;
   - API de biblioteca con SQLite.
2. Cada caso debe incluir objetivo, artefactos esperados y validadores
   independientes del resumen del agente.
3. Añadir comandos:

   ```powershell
   .\.venv\Scripts\agentarium.exe benchmark run
   .\.venv\Scripts\agentarium.exe benchmark report
   ```

4. Registrar por corrida: proveedor, modelo, versiones de prompts, duración,
   intentos, splits, intervención humana, resultado técnico, resultado
   semántico y categoría final de fallo.
5. Usar categorías estables: `planning_contract`, `path_conflict`,
   `unsupported_capability`, `duplicate_candidate`, `technical_validation`,
   `semantic_rejection`, `provider_failure`, `infrastructure` y `completed`.
6. Ejecutar la matriz inicial de 3 casos × 3 modelos × 3 repeticiones. Las 27
   corridas deben ser automatizadas; no supervisadas manualmente una por una.

**Criterios de salida:**

- informe Markdown/JSON reproducible desde eventos persistidos;
- cero falsos `completed` en los validadores independientes;
- baseline de tasa de finalización, tiempo y causas de fallo;
- ninguna recomendación de modelo por rol antes de tener esos datos.

### P2 — contrato real de capacidades del runtime

**Esfuerzo estimado:** 1–2 PR, 2–3 sesiones.

Para el MVP no se instalarán paquetes dinámicamente durante una tarea. Esa
opción mezcla ejecución con red y autoridad, y complica mucho el aislamiento.

1. Crear un manifiesto estructurado de capacidades: Python, paquetes
   permitidos/disponibles, ejecutables y restricciones de red.
2. Incluirlo en el contexto del planificador y del worker como datos, no sólo
   como una frase de prompt.
3. Hacer preflight de imports/comandos antes de gastar tester y revisor.
4. Si falta una capacidad, terminar con `unsupported_capability` y una acción
   concreta: elegir stack permitido, configurar un entorno o solicitar
   aprobación.
5. Dejar la creación de un entorno por proyecto y la instalación con red como
   una mejora futura, siempre detrás de aprobación y allowlist.

**Criterio de salida:** el caso API nunca llega tarde a un
`ModuleNotFoundError`; o usa una capacidad declarada o falla temprano con una
explicación accionable.

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

1. **PR 1 — planificación mecánica:** rutas efectivas más IDs de criterios y
   sus regresiones.
2. **PR 2 — medición:** casos versionados, taxonomía y `benchmark report`.
3. **PR 3 — capacidades:** manifiesto del runtime y fallo temprano por capacidad
   no disponible.

No empezar la modularización grande antes de que PR 1 y PR 2 congelen el
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
