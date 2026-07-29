# ADR 0013: Contratos web activados por dominio

- Estado: aceptada
- Fecha: 2026-07-28

## Contexto

El perfil web nació al validar un juego y asociaba cualquier criterio que
contuviera “navegación” o “movimiento” con desplazamiento bidimensional de un
jugador. Una prueba posterior pidió navegación por teclado en una aplicación de
inventario. El perfil exigió `player.x`, `player.y` y controles con flechas,
rechazó tres candidatos técnicamente válidos para ese criterio y agotó la tarea.

La regla era objetiva, pero su activación no distinguía el dominio.

## Decisión

Los contratos de movimiento, persecución, colisiones, vidas y victoria sólo se
activan cuando el conjunto de criterios contiene señales explícitas de un
videojuego, como jugador, enemigos, partida, laberinto, Pac-Man o puntuación.

La accesibilidad por teclado fuera de ese contexto usa un contrato separado. El
perfil acepta controles HTML nativos en el orden del documento, exige nombres
accesibles para campos de formulario, exige `tabindex="0"` en controles
personalizados y rechaza índices de tabulación positivos. No exige listeners de
flechas ni estado de jugador.

Los ids HTML duplicados se rechazan en todas las aplicaciones web porque
`getElementById` sería ambiguo independientemente del dominio.

Las capacidades comunes de aplicaciones tienen contratos propios activados por
los criterios: CRUD exige mutaciones concretas para alta, edición y borrado;
búsqueda y filtros exigen controles conectados a operaciones de coincidencia;
las acciones destructivas exigen `confirm`; el estado vacío exige detección y
mensaje; persistencia exige lectura y escritura; transferencia JSON exige
exportación descargable e importación protegida; y una guía exige documentación
en texto/Markdown o instrucciones embebidas. Estas reglas no contienen nombres
del dominio inventario y son reutilizables en gestores, catálogos y paneles.

La versión del contrato fijo avanza para que reintentos posteriores no hereden
como requisito acumulado un fallo producido por la activación anterior.

## Consecuencias

El juego conserva sus verificaciones de movimiento cuando sus criterios
describen un juego. Formularios, paneles y herramientas locales pueden declarar
navegación por teclado sin adoptar conceptos de videojuego. Añadir un dominio
nuevo requiere definir señales de activación conservadoras y una prueba negativa
con un dominio distinto.
