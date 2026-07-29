from __future__ import annotations

import asyncio
import json
from typing import Any

from agentarium.domain.models import AgentDefinition

from .base import LLMProvider, ModelRequest, ProviderResponse
from .prompts import render_prompt


class MockProvider(LLMProvider):
    name = "mock"

    async def generate(
        self,
        request: ModelRequest,
        agent: AgentDefinition,
    ) -> ProviderResponse:
        await asyncio.sleep(0)
        content = self._content_for(request)
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True)
        prompt = render_prompt(request, agent)
        return ProviderResponse(
            content=content,
            raw_text=raw,
            prompt_characters=len(prompt),
            response_characters=len(raw),
        )

    def _content_for(self, request: ModelRequest) -> dict[str, Any]:
        operation = request.operation
        if operation == "brief":
            goal = str(request.payload["goal"]).strip()
            return {
                "summary": f"Proyecto ejecutable y verificable para: {goal}",
                "scope": [
                    "Producir un resultado vertical pequeño",
                    "Mantener decisiones y evidencia auditables",
                    "Validar cada entregable antes de integrarlo",
                ],
                "deliverables": [
                    "Especificación de alcance",
                    "Artefacto principal",
                    "Resultado integrado y documentado",
                ],
                "assumptions": [
                    "Se prioriza un prototipo local y reversible",
                    "No se usan credenciales ni publicación remota",
                ],
                "ambiguities": [
                    "Los detalles no críticos se resolverán con la opción de menor alcance"
                ],
                "constraints": [
                    "Ejecución local-first",
                    "Sin descargas grandes automáticas",
                    "Toda afirmación verificable requiere evidencia",
                ],
                "success_criteria": [
                    "Los tres entregables quedan persistidos",
                    "Tester y reviewer aprueban el resultado final",
                    "El flujo puede reanudarse sin repetir tareas completadas",
                ],
                "approvals_required": [],
            }
        if operation == "plan":
            brief = request.payload["brief"]
            return {
                "milestone": {
                    "title": "MVP verificable",
                    "description": (
                        "Del objetivo a un resultado integrado con controles independientes."
                    ),
                },
                "tasks": [
                    {
                        "key": "scope",
                        "title": "Especificar el alcance verificable",
                        "description": "Convertir el brief en una especificación acotada.",
                        "dependencies": [],
                        "expected_outputs": ["specification"],
                        "acceptance_criteria": [
                            "Incluye alcance y exclusiones",
                            "Define evidencia verificable",
                        ],
                        "risk": "low",
                        "priority": 90,
                    },
                    {
                        "key": "implementation",
                        "title": "Producir el artefacto principal",
                        "description": "Construir el entregable principal según la especificación.",
                        "dependencies": ["scope"],
                        "expected_outputs": ["implementation_artifact"],
                        "acceptance_criteria": [
                            "Satisface la especificación aprobada",
                            "Incluye evidencia observable",
                        ],
                        "risk": "medium",
                        "priority": 80,
                    },
                    {
                        "key": "integration",
                        "title": "Integrar y documentar el resultado",
                        "description": (
                            "Integrar artefactos previos y documentar el resultado final."
                        ),
                        "dependencies": ["implementation"],
                        "expected_outputs": [
                            "integrated_result",
                            *brief["deliverables"],
                        ],
                        "acceptance_criteria": [
                            *brief["scope"],
                            *brief["success_criteria"],
                        ],
                        "risk": "low",
                        "priority": 70,
                    },
                ],
            }
        if operation == "work":
            task = request.payload["task"]
            is_draft = task["risk"] == "medium" and request.attempt == 1
            dependency_artifacts = request.payload.get("dependency_artifacts", [])
            workspace_file = self._workspace_file(
                request,
                task,
                dependency_artifacts,
                is_draft=is_draft,
            )
            return {
                "artifact_type": task["expected_outputs"][0],
                "title": task["title"],
                "summary": f"Resultado determinista para {task['title']}.",
                "quality": "draft" if is_draft else "verified",
                "evidence": []
                if is_draft
                else [
                    "Contrato Pydantic validado",
                    "Archivo materializado y checksum calculado",
                ],
                "dependency_artifact_ids": [artifact["id"] for artifact in dependency_artifacts],
                "acceptance_criteria_addressed": task["acceptance_criteria"],
                "limitations": (
                    ["Falta evidencia explícita; requiere corrección"]
                    if is_draft
                    else ["Proveedor mock: no representa calidad de un modelo real"]
                ),
                "files": [workspace_file],
            }
        if operation == "test":
            artifact = request.payload["artifact"]
            file_verified = bool(request.payload.get("file_verified"))
            validation_profiles = request.payload.get("validation_profiles", [])
            profiles_passed = bool(validation_profiles) and all(
                profile.get("passed") for profile in validation_profiles
            )
            passed = file_verified and profiles_passed and bool(artifact.get("summary"))
            return {
                "passed": passed,
                "checks": [
                    {
                        "name": "artifact_schema",
                        "passed": bool(artifact.get("artifact_type")),
                        "evidence": "El artefacto fue validado por Pydantic.",
                    },
                    {
                        "name": "file_exists",
                        "passed": file_verified,
                        "evidence": "El archivo materializado existe y su checksum coincide.",
                    },
                    {
                        "name": "validation_profiles",
                        "passed": profiles_passed,
                        "evidence": (
                            f"{len(validation_profiles)} perfiles fijos registraron "
                            "salida y código de retorno."
                        ),
                    },
                ],
                "summary": (
                    "Comprobaciones automáticas superadas."
                    if passed
                    else "Falta evidencia materializada."
                ),
            }
        if operation == "review":
            artifact = request.payload["artifact"]
            criteria = request.payload["acceptance_criteria"]
            approved = artifact.get("quality") == "verified"
            return {
                "verdict": "approved" if approved else "changes_requested",
                "reasons": (
                    ["La evidencia y las limitaciones son explícitas."]
                    if approved
                    else ["El resultado afirma completitud sin evidencia suficiente."]
                ),
                "acceptance_results": {criterion: approved for criterion in criteria},
            }
        raise ValueError(f"Unsupported mock operation: {operation}")

    @staticmethod
    def _workspace_file(
        request: ModelRequest,
        task: dict[str, Any],
        dependency_artifacts: list[dict[str, Any]],
        *,
        is_draft: bool,
    ) -> dict[str, str]:
        artifact_type = str(task["expected_outputs"][0])
        file_names = {
            "specification": "docs/specification.md",
            "implementation_artifact": "src/implementation.md",
            "integrated_result": "README.md",
        }
        path = file_names.get(artifact_type, f"deliverables/{artifact_type}.md")
        project = request.payload.get("project", {})
        brief = project.get("brief") or {}
        goal = str(brief.get("summary", "Objetivo local no especificado"))
        criteria = "\n".join(
            f"- {criterion}" for criterion in task.get("acceptance_criteria", [])
        )
        dependencies = "\n".join(
            f"- {artifact['title']} ({artifact['id']})"
            for artifact in dependency_artifacts
        )
        content = "\n".join(
            [
                f"# {task['title']}",
                "",
                "## Objetivo",
                goal,
                "",
                "## Entregable",
                str(task["description"]),
                "",
                "## Criterios de aceptación",
                criteria or "- Sin criterios declarados",
                "",
                "## Dependencias verificadas",
                dependencies or "- Ninguna",
                "",
                "## Estado",
                (
                    "Borrador: requiere corrección."
                    if is_draft
                    else "Verificado por el flujo local de Agentarium."
                ),
                "",
                "> Contenido determinista del proveedor mock; no sustituye inferencia real.",
                "",
            ]
        )
        return {
            "path": path,
            "content": content,
            "purpose": f"Materializar el entregable {artifact_type}",
        }
