# ADR 0005: Materialización acotada de archivos de proyecto

- Estado: aceptada
- Fecha: 2026-07-27

## Contexto

El flujo vertical persistía respuestas estructuradas como JSON, pero no producía
archivos que pudieran inspeccionarse como un proyecto. El ejecutor de comandos
existente aplica una allowlist, aunque una allowlist de ejecutables no vuelve
seguro el código generado por un modelo: por ejemplo, un intérprete permitido
todavía podría intentar leer o modificar recursos fuera del objetivo.

## Decisión

La primera capacidad real del runtime será materializar únicamente archivos de
texto declarados mediante `WorkArtifactProposal`.

- Las rutas deben ser relativas y no pueden contener traversal, unidades de
  Windows, directorios de control ni archivos de entorno.
- Cada tarea escribe primero en
  `workspaces/<project>/tasks/<task>/attempt-<n>`.
- La integración usa reemplazo atómico hacia
  `workspaces/<project>/project`.
- Se limitan cantidad y tamaño por política versionada.
- Cada archivo integrado produce ruta, tamaño y SHA-256 verificables.
- Los comandos generados por agentes permanecen deshabilitados.

El JSON de control continúa guardándose para auditoría y ahora referencia tanto
su propio archivo como los archivos materiales del proyecto.

## Consecuencias

Agentarium puede demostrar escritura real y confinada incluso con el proveedor
mock. Ese proveedor produce contenido determinista y no debe presentarse como
una implementación específica de alta calidad. La ejecución de compiladores,
tests o servidores requerirá un incremento posterior con perfiles de comandos
fijos, aislamiento reforzado y evidencia de salida/código de retorno.
