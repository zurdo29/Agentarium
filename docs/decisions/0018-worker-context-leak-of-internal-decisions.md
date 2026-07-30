# ADR 0018: El worker no debe copiar el registro interno de decisiones

- Estado: aceptada
- Fecha: 2026-07-29

## Contexto

Verificando ADR 0017 en vivo contra qwen2.5-coder:7b (documento de
arquitectura, sin código), la tarea "Escribir Decisiones de Diseño" produjo
como única "decisión de diseño":

> **Alcance inicial conservador** — Decision: Ejecutar un único hito vertical
> con 4 entregables dependientes. Razón: Es reversible, observable y
> adecuado para el presupuesto local. Alternativas: Crear varios hitos en
> paralelo / Solicitar más detalles al usuario.

Ese texto es literalmente el registro `Decision` que `Orchestrator.
plan_project` crea para su propio historial de auditoría (alcance
"single vertical milestone", no una decisión sobre el sistema de reservas del
restaurante). El worker lo copió tal cual como si fuera contenido del
dominio solicitado. El revisor de la tarea de cierre lo detectó y pidió
cambios correctamente, pero la tarea agotó sus 3 intentos repitiendo la
misma confusión y el proyecto terminó `failed`.

Es la tercera vez que se observa esta familia de fuga con el mismo modelo: ya
había aparecido como jerga suelta ("Single Hito Vertical") en un run anterior
del mismo objetivo, y ahora como el registro completo, verbatim. La causa es
mecánica y reproducible: `ContextBuilder.project()` incluye
`project.decisions` (el registro interno de gestión) en el contexto que
recibe el worker (`operational()["project"]`), y el prompt de `work` no
aclaraba que ese campo es meta-información del propio Agentarium, distinta
del contenido que debe producir la entrega.

## Decisión

Se agregó una frase explícita a la instrucción de `work` en
`llm/prompts.py`: `SOLICITUD.payload.project.decisions` es un registro
interno de gestión del propio Agentarium, nunca contenido de dominio ni una
decisión de arquitectura del producto solicitado, y no debe copiarse,
resumirse ni adaptarse como parte del entregable. `WORKSPACE_PROMPT_VERSION`
avanza a `workspace-v5` siguiendo el mismo precedente de ADR 0014/0016 para
cambios de contenido del prompt.

No se removió `project.decisions` del contexto ni se lo renombró: sigue
siendo potencialmente útil como contexto legítimo (por ejemplo, si en el
futuro hay decisiones de rework relevantes al dominio). El problema no era
que el dato estuviera disponible, sino que no estaba etiquetado como interno.

## Consecuencias

Esta es una instrucción de prompt, no una compuerta mecánica: no hay garantía
de que un modelo la respete siempre, igual que ADR 0017 no garantiza que el
revisor use bien la evidencia de dependencia que ahora recibe. El patrón de
fuga de contexto interno con qwen2.5-coder:7b ya se vio tres veces
independientes; si se repite después de este cambio, la conclusión pasa de
"hace falta aclarar el prompt" a "este modelo no es confiable para tareas
donde el contexto interno de Agentarium es visible", lo cual sería evidencia
real para la comparación de modelos por rol, no una suposición.
