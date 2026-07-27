# Arquitectura

Agentarium usa una arquitectura modular orientada a eventos. FastAPI expone
servicios de aplicación; SQLAlchemy persiste checkpoints y auditoría en SQLite;
Pydantic define los artefactos intercambiados; AsyncIO separa tareas listas del
límite real de inferencias.

```text
UI / CLI
   |
FastAPI + ApplicationService
   |
Orchestrator ---- ResourceScheduler ---- LLMProvider
   |                    |                 |-- Mock
   |                    |                 |-- Ollama
   |                    |                 `-- OpenAI-compatible
   |
Repository (SQLAlchemy / SQLite)
   |-- estado y checkpoints
   |-- artefactos, runs, reviews y tests
   `-- decisiones, aprobaciones, eventos y métricas
```

## Flujo vertical

1. Director produce `ProjectBrief`.
2. Manager crea hitos y un DAG validado.
3. Scheduler asigna tareas listas al worker.
4. El artefacto se valida contra su contrato y contra archivos observables.
5. Tester y reviewer emiten resultados independientes.
6. Un rechazo cambia a `changes_requested`, luego vuelve a `ready` dentro del
   presupuesto de intentos.
7. Cada transición y ejecución se guarda antes de continuar.

## Memoria

- Operativa: tarea, criterios, archivos autorizados y artefactos dependientes.
- Proyecto: brief, decisiones, arquitectura, hitos y resumen.
- Auditoría: eventos y artefactos completos, nunca inyectados automáticamente.

## Recuperación

Los estados `assigned` y `running` encontrados al reiniciar se convierten en
`ready` con un evento de recuperación. Tareas completadas no se repiten. Los
timeouts, JSON inválido y caídas del proveedor se registran y consumen intentos
acotados.

## Extensión

Los roles provienen de YAML, los proveedores implementan una interfaz común y el
aislamiento implementa un contrato independiente. Esto permite añadir
departamentos, worktrees o LangGraph sin cambiar los modelos centrales.
