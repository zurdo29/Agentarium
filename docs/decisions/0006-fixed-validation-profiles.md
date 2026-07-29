# ADR 0006: Perfiles fijos para validaciones ejecutables

- Estado: aceptada
- Fecha: 2026-07-27

## Contexto

Una allowlist de ejecutables no basta para ejecutar comandos propuestos por un
modelo. Un intérprete permitido puede recibir código o argumentos capaces de
salir del alcance esperado. Sin embargo, Agentarium necesita evidencia real de
validación —salida estándar, error, timeout y código de retorno— para distinguir
un archivo escrito de un entregable comprobado.

## Decisión

Los agentes no declaran comandos. La aplicación selecciona perfiles internos e
inmutables según los archivos materializados:

- `workspace_inventory`: inventario de archivos del proyecto.
- `python_syntax`: compilación sintáctica de archivos `.py`.
- `json_syntax`: parseo estricto de archivos `.json`.
- `javascript_syntax`: `node --check` para `.js`, `.mjs` y `.cjs`.

Todos los perfiles usan ejecución sin shell, un directorio confinado al
workspace, entorno filtrado, timeout de 20 segundos y logs truncados por
política. El resultado persistido incluye perfil, targets, comando efectivo,
cwd, stdout, stderr, código de retorno, timeout y veredicto.

## Consecuencias

El runtime puede anunciar ejecución de comandos porque al menos el inventario se
ejecuta para cada artefacto. Esta capacidad no equivale a ejecutar tests
arbitrarios ni aplicaciones generadas. Nuevos perfiles deberán ser código de la
aplicación, tener pruebas propias y no aceptar fragmentos de shell del modelo.
