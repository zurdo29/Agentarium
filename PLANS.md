# Plan de producto

## Entregado por este MVP

- Fase 0: repositorio, documentación, scripts y calidad.
- Fase 1 vertical: dominio, SQLite, roles, proveedores mock/Ollama/OpenAI,
  orquestador, correcciones acotadas, API, CLI e interfaz.
- Base de Fase 2: semáforo de inferencia, reintentos, eventos, checkpoints,
  recuperación, SSE y métricas.

## Siguientes incrementos

1. Sustituir el planificador determinista por prompts evaluados con fixtures.
2. Aislamiento real con ramas/worktrees y aplicación de parches.
3. Departamentos, plugins y políticas de autoridad administrables.
4. Evaluaciones repetibles y comparación de modelos por rol.

Cada incremento debe preservar el flujo mock como prueba de regresión.
