# Ollama

1. Inicia Ollama fuera de Agentarium.
2. Confirma el modelo ya instalado con `ollama list`.
3. Cambia `provider` y `model` en `configs/roles/default.yaml`.
4. Ejecuta `agentarium doctor`.

Agentarium no descarga modelos. Con 8 GB de VRAM se recomienda empezar con un
modelo cuantizado pequeño y mantener concurrencia de inferencia en `1`.
