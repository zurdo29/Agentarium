# Contratos de artefactos

Los contratos canónicos son modelos Pydantic en
`backend/agentarium/domain/models.py`. Cada registro lleva un UUID, timestamps y
referencias al proyecto o tarea correspondiente.

| Contrato | Evidencia principal |
| --- | --- |
| `ProjectBrief` | alcance, entregables, ambigüedades y criterios de éxito |
| `WorkItem` | dependencias, autoridad, archivos, herramientas, riesgo e intentos |
| `AgentRun` | entrada/resumen, resultado terminal, modelo, duración y correlación |
| `Artifact` | contenido validado, archivos observados y checksum |
| `TestReport` | checks independientes y evidencia de comandos |
| `Review` | veredicto, razones y resultado por criterio |
| `ApprovalRequest` | acción, motivo, riesgo, alternativas y recursos afectados |
| `ExecutionEvent` | transición, agente, intento, error, recursos y correlación |

Un `AgentRun` sólo puede finalizar con uno de estos resultados: artefacto
entregado, información requerida, bloqueo, solicitud de aprobación, escalamiento
o rechazo. Las afirmaciones del modelo no sustituyen la existencia del archivo,
checksum, código de salida o informe requerido.
