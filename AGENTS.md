# AGENTS.md

Agentarium es una empresa local de agentes que transforma objetivos en artefactos
verificables. El backend FastAPI contiene dominio, persistencia SQLite,
proveedores LLM y orquestación; la interfaz React/Vite muestra proyectos, tareas,
eventos, métricas y aprobaciones.

Comandos principales: `.\dev.ps1`, `.\test.ps1`, `agentarium doctor` y
`agentarium project --help`. La documentación vive en `docs/`; la configuración
versionada vive en `configs/`.

Toda funcionalidad nueva debe incluir pruebas. Ejecuta `.\test.ps1` antes de
integrar; conserva tipado, contratos Pydantic y transiciones de estado válidas.
Registra decisiones arquitectónicas en `docs/decisions/`.

No modifiques sin aprobación `.openai/hosting.json`, políticas de CI/CD,
credenciales, archivos fuera de este workspace, migraciones destructivas ni
configuraciones que amplíen autoridad o acceso de red.
