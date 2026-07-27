from __future__ import annotations

from agentarium.domain.enums import RiskLevel
from agentarium.domain.models import ApprovalRequest


class ApprovalPolicy:
    _sensitive_actions: dict[str, str] = {
        "borrar muchos": "Eliminación masiva de archivos",
        "eliminar archivos": "Eliminación de archivos",
        "fuera del workspace": "Modificación fuera del workspace",
        "instalar dependencia del sistema": "Instalación de dependencias del sistema",
        "descargar modelo": "Descarga de un modelo grande",
        "credencial": "Acceso a credenciales",
        "api key": "Uso de claves de API",
        "administrador": "Ejecución con privilegios administrativos",
        "publicar": "Publicación de contenido",
        "hacer push": "Push remoto",
        "merge remoto": "Merge remoto",
        "ci/cd": "Modificación de CI/CD",
        "migración destructiva": "Migración destructiva",
    }

    def inspect_goal(self, project_id: str, goal: str) -> list[ApprovalRequest]:
        normalized = goal.casefold()
        approvals: list[ApprovalRequest] = []
        for marker, action in self._sensitive_actions.items():
            if marker in normalized:
                approvals.append(
                    ApprovalRequest(
                        project_id=project_id,
                        action=action,
                        reason=f"El objetivo contiene una acción protegida: '{marker}'.",
                        risk=RiskLevel.HIGH,
                        alternatives=[
                            "Preparar un plan sin ejecutar la acción",
                            "Limitar el alcance a un entorno temporal local",
                        ],
                        affected_resources=["Por determinar durante la planificación"],
                    )
                )
        return approvals
