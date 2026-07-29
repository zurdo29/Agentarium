# ADR 0009: Vista previa local aislada del producto

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

Agentarium podía materializar y verificar archivos dentro del workspace, pero el
usuario tenía que buscarlos manualmente para comprobar el resultado. Servir HTML
generado por un modelo desde el mismo contexto del centro de control también
podría darle acceso innecesario a la API local.

## Decisión

La API expone una ruta de vista previa por proyecto que sólo sirve extensiones
web conocidas desde `workspaces/<project>/project`. La resolución de rutas:

1. Permanece dentro del workspace del proyecto.
2. Rechaza segmentos protegidos como `.git`, `.env`, `.openai` y `.codex`.
3. Rechaza enlaces simbólicos y tipos de archivo no permitidos.
4. Busca un `index.html` conocido cuando no se indica una ruta.

Cada respuesta usa `Cache-Control: no-store`, `nosniff` y una política CSP con
`sandbox`, formularios, objetos y conexiones de red desactivados. La interfaz
embebe la vista desde el origen separado de la API en un `iframe` con sandbox y
permite abrirla en una pestaña independiente.

## Consecuencias

Un proyecto web completado puede probarse directamente desde su ficha sin dar al
contenido generado acceso de red ni exponer metadatos internos del repositorio.
La vista previa no sustituye las validaciones ni afirma que el producto sea
seguro para publicación; es una superficie local de inspección funcional.
