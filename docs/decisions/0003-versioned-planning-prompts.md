# ADR 0003: Prompts de planificación versionados y contratos tipados

## Estado

Aceptada.

## Contexto

El MVP enviaba al proveedor una serialización genérica de la operación. El
proveedor `mock` protegía el flujo vertical, pero no existía un contrato de prompt
estable para evaluar los proveedores reales ni una validación Pydantic completa
del DAG antes de persistirlo.

## Decisión

Los prompts de `brief` y `plan` llevan una versión explícita, autoridad del rol,
instrucciones específicas, payload y JSON Schema generado desde contratos
Pydantic. La respuesta se valida con esos mismos contratos antes de crear el
brief, el hito o las tareas.

Una matriz de fixtures cubre objetivos de dominios distintos y evalúa que el
proveedor `mock` preserve el contrato, produzca un DAG válido y mantenga raíces y
terminales esperadas. `mock` sigue siendo la regresión offline; Ollama y
proveedores compatibles reciben exactamente el mismo prompt versionado.

## Consecuencias

- Los cambios de contrato requieren una nueva versión de prompt y fixtures.
- Respuestas con campos extra, claves inválidas, dependencias desconocidas o
  ciclos se rechazan antes de tocar la persistencia.
- El evaluador actual mide invariantes estructurales, no calidad semántica de un
  modelo real; esa comparación queda para el incremento de evaluaciones por rol.
