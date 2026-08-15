# Ollama

1. Inicia Ollama fuera de Agentarium.
2. Confirma el modelo ya instalado con `ollama list`.
3. Elegí el proveedor y modelo activos desde el panel de la interfaz web
   (`Motor de inferencia`), o seteá `AGENTARIUM_PROVIDER`/
   `AGENTARIUM_MODEL` en `.env` antes de arrancar. **Editar `provider`/
   `model` en `configs/roles/default.yaml` no tiene efecto**:
   `build_application()` sobreescribe esos valores en cada arranque desde
   la selección guardada o esas mismas variables de entorno.
4. Ejecuta `agentarium doctor` -- confirma que el servidor responde *y*
   que el modelo configurado está realmente descargado, no sólo que
   Ollama está corriendo.

Agentarium no descarga modelos. Con 8 GB de VRAM se recomienda empezar con un
modelo cuantizado pequeño y mantener concurrencia de inferencia en `1`.
