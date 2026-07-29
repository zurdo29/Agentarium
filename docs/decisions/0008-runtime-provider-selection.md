# ADR 0008: Selección verificada del proveedor de inferencia

- Estado: aceptada
- Fecha: 2026-07-27

## Contexto

Agentarium registraba proveedores `mock`, Ollama y OpenAI-compatible, pero los
roles estaban fijados a `mock` y la interfaz no podía distinguir entre un
servidor disponible, uno sin modelos y uno inaccesible. Cambiar la configuración
a un proveedor caído hacía que el fallo apareciera recién durante una ejecución.

## Decisión

La aplicación expone un diagnóstico local de los tres proveedores. Ollama se
sondea mediante `GET /api/tags` y los servidores OpenAI-compatible mediante
`GET /models`, con timeout corto, caché y sin devolver credenciales.

Un proveedor real sólo puede activarse si:

1. Su endpoint responde correctamente.
2. Publica al menos un modelo.
3. El usuario elige explícitamente uno de esos modelos.
4. No hay agentes ejecutándose durante el cambio.

La selección se aplica a todos los roles para las nuevas ejecuciones y se guarda
en `runtime/provider-selection.json`. No se escriben `.env`, credenciales ni
configuración versionada desde la interfaz. `mock` permanece siempre disponible
como motor determinista y una activación fallida conserva la selección anterior.

También se admiten `AGENTARIUM_PROVIDER` y `AGENTARIUM_MODEL` al iniciar el
backend. Para un proveedor no determinista el modelo es obligatorio.

## Consecuencias

El dashboard puede explicar por qué todavía usa `mock`, mostrar los modelos
locales encontrados y activar uno sin reiniciar la aplicación. La ausencia de
Ollama o de modelos deja de ser un fallo tardío. Si un proveedor activo cae
durante una tarea, la ejecución falla de forma visible en vez de producir un
resultado `mock` presentado como inferencia real.
