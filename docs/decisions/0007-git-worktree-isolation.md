# ADR 0007: Aislamiento transaccional con Git worktrees

- Estado: aceptada
- Fecha: 2026-07-27

## Contexto

Los archivos se escribían primero en staging, pero se copiaban al proyecto antes
de que tester y revisor emitieran su veredicto. Un intento rechazado podía
sobrescribir el producto aunque el estado de la tarea indicara que necesitaba
cambios.

## Decisión

Cada proyecto contiene un repositorio Git interno en
`workspaces/<project>/project`, independiente del repositorio de Agentarium.
Cada intento sigue este ciclo:

1. Crear una rama y un worktree con identificador único.
2. Escribir exclusivamente dentro de ese worktree.
3. Capturar diff, archivos y commit candidato.
4. Ejecutar checksums, perfiles y revisión sobre el candidato.
5. Fusionar a `main` sólo cuando tester y revisor aprueban.
6. Eliminar siempre el worktree y la rama temporal.

Las operaciones Git son comandos fijos de la aplicación, se ejecutan sin shell,
con entorno filtrado y bajo un lock por proyecto. Los repositorios internos usan
`core.autocrlf=false` para conservar bytes y checksums idénticos en Windows.

## Consecuencias

Un intento rechazado conserva su JSON y evidencia del change set, pero no
modifica el producto. Los intentos aprobados dejan commits auditables y rutas de
producto verificadas. Las fusiones paralelas se serializan por proyecto; un
conflicto de integración se convierte en cambios solicitados, no en una
sobrescritura silenciosa.
