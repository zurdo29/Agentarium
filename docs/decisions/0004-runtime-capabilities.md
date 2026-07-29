# ADR 0004: El runtime declara capacidades, no sólo estado

## Estado

Aceptada.

## Contexto

El flujo `mock` podía finalizar un proyecto como `completed`, aunque únicamente
hubiera producido artefactos JSON. La interfaz mostraba ese estado sin distinguir
una simulación de una ejecución capaz de materializar el producto, lo que hacía
parecer que el botón de ejecución no había hecho nada.

## Decisión

La API publica un contrato de runtime con un modo (`simulation`,
`artifact_only` o `workspace`), los proveedores efectivos de los roles y
capacidades explícitas para inferencia, archivos de proyecto, comandos y
aislamiento.

La interfaz presenta ese modo de forma persistente y, al terminar una ejecución
sin capacidad de workspace, explica que el flujo fue verificado pero el producto
no fue construido. Durante una acción muestra el último evento recibido.

## Consecuencias

- `completed` conserva su significado de finalización del flujo actual.
- La capacidad `project_files` será la condición para presentar una ejecución
  como producto materializado.
- El próximo incremento puede incorporar herramientas reales sin inferir
  capacidades a partir del nombre del proveedor.
