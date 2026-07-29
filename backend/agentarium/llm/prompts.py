from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from agentarium.domain.models import AgentDefinition
from agentarium.execution import (
    ReviewEvaluationProposal,
    TestEvaluationProposal,
    WorkArtifactProposal,
)
from agentarium.planning import BriefProposal, PlanProposal

from .base import ModelRequest

PLANNING_PROMPT_VERSION = "planning-v2"
WORKSPACE_PROMPT_VERSION = "workspace-v4"

_OPERATION_CONTRACTS: dict[str, type[BaseModel]] = {
    "brief": BriefProposal,
    "plan": PlanProposal,
    "work": WorkArtifactProposal,
    "test": TestEvaluationProposal,
    "review": ReviewEvaluationProposal,
}

_OPERATION_INSTRUCTIONS = {
    "brief": (
        "Transforma el objetivo en un brief pequeño, verificable y reversible. "
        "Explicita ambigüedades, restricciones, criterios de éxito y cualquier "
        "acción que necesite aprobación humana. Una ambigüedad sólo requiere "
        "aprobación si cambia materialmente el alcance o exige autoridad externa; "
        "resuelve vocabulario común con un supuesto conservador y no conviertas "
        "aclaraciones no bloqueantes en entregables."
    ),
    "plan": (
        "Convierte el brief en un único hito y un DAG de tareas concretas. "
        "Cada tarea debe producir evidencia observable, tener criterios comprobables "
        "y depender solamente de claves declaradas. Evita trabajo duplicado. El DAG "
        "debe implementar el objetivo principal aunque existan ambigüedades. Copia "
        "literalmente cada entrada de brief.deliverables en expected_outputs de al "
        "menos una tarea y cada entrada de brief.scope y brief.success_criteria en "
        "acceptance_criteria de al menos una tarea; ninguna aclaración puede "
        "sustituir esa cobertura."
    ),
    "work": (
        "La tarea actual es SOLICITUD.payload.task y tiene prioridad absoluta. Produce "
        "un artefacto NUEVO que satisfaga sus expected_outputs y cada uno de sus "
        "acceptance_criteria. Los dependency_artifacts son sólo referencias de entrada: "
        "nunca los devuelvas ni los reescribas como si fueran la entrega actual. En un "
        "reintento los archivos de dependencia se omiten deliberadamente: usa "
        "dependency_references sólo como contexto y corrige explícitamente "
        "prior_candidate_files contiene exclusivamente el candidato mas reciente "
        "de esta misma tarea: usalo como base editable, conserva lo que ya funciona "
        "y devuelve archivos completos con cambios reales; no lo repitas sin "
        "corregirlo. Corrige prior_review_feedback y cada "
        "prior_validation_failures con una "
        "implementación nueva. Los perfiles de validación son obligatorios: no "
        "afirmes que los corregiste en summary; el comportamiento debe existir "
        "literalmente en files[].content. Si comienzas desde cero, conserva y "
        "satisface cada cumulative_validation_requirements: resume los requisitos "
        "objetivos aprendidos en todos los intentos. Declara archivos de "
        "texto completos para el "
        "workspace. Cada path parte desde la raíz del producto (por ejemplo index.html o "
        "docs/design.md): nunca incluyas workspaces/, project/ ni el id del proyecto. "
        "Representa literalmente en nombres, datos, comentarios o UI los conceptos de "
        "dominio exigidos; por ejemplo, una vecina agresiva no puede quedar como un "
        "enemy o neighbor genérico. No traduzcas un término requerido a una categoría "
        "más amplia: conserva la frase exacta en datos o comentarios si no puede ser "
        "un identificador. Los expected_outputs deben quedar funcionales: implementa "
        "comportamiento ejecutable concreto y no entregues TODOs, placeholders, "
        "funciones vacías ni comentarios del tipo 'logic here'. "
        "No propongas comandos y copia literalmente en acceptance_criteria_addressed "
        "cada criterio de la tarea que la entrega cubre."
    ),
    "test": (
        "Valida únicamente evidencia técnica: file_verified, checksums, aislamiento y "
        "validation_profiles. passed debe reflejar esos checks fijos; no vuelvas a juzgar "
        "el significado de los criterios de producto, que pertenece al revisor."
    ),
    "review": (
        "Revisa críticamente el artefacto contra todos los criterios de aceptación. "
        "Inspecciona explícitamente artifact.files[].content, no sólo summary o evidence. "
        "Tu veredicto es semántico e independiente del tester técnico. "
        "Las adaptaciones explícitas de acceptance_criteria son intencionales y "
        "prevalecen sobre convenciones de la obra base; nunca rechaces un criterio "
        "porque se aleje de la versión tradicional. "
        "acceptance_results debe contener exacta y únicamente las claves recibidas en "
        "acceptance_criteria, sin agregar checks técnicos. No exijas entregables fuera "
        "del alcance de la tarea actual."
    ),
}


def render_prompt(request: ModelRequest, agent: AgentDefinition) -> str:
    contract = response_contract(request.operation)
    version = (
        PLANNING_PROMPT_VERSION
        if request.operation in {"brief", "plan"}
        else WORKSPACE_PROMPT_VERSION
        if request.operation == "work"
        else "artifact-v1"
    )
    instruction = _OPERATION_INSTRUCTIONS.get(
        request.operation,
        "Resuelve la operación dentro de la autoridad declarada.",
    )
    authority = {
        "role": agent.role.value,
        "authority": agent.authority,
        "tools": agent.tools,
        "allowed_directories": agent.allowed_directories,
    }
    sections = [
        f"AGENTARIUM_PROMPT_VERSION={version}",
        "Devuelve exactamente un objeto JSON, sin Markdown ni texto adicional.",
        (
            "No inventes archivos, comandos ejecutados ni evidencia. Si falta autoridad "
            "o hace falta una acción sensible, decláralo en el campo previsto por el contrato."
        ),
        f"OPERACIÓN={request.operation}",
        f"INSTRUCCIÓN={instruction}",
        "AUTORIDAD=" + _json(authority),
    ]
    if contract is not None:
        sections.extend(
            [
                "CONTRATO_JSON_SCHEMA=" + _json(response_schema(request.operation)),
                "Respeta el contrato exactamente; no agregues campos.",
            ]
        )
    sections.append("SOLICITUD=" + _json(request.model_dump(mode="json")))
    if request.operation == "work":
        current_task = request.payload.get("task", {})
        retry_guidance = request.payload.get("retry_guidance", {})
        sections.extend(
            [
                "CONTRATO_DE_LA_TAREA_ACTUAL=" + _json(current_task),
                "CORRECCIONES_OBLIGATORIAS=" + _json(retry_guidance),
                (
                    "RECORDATORIO_FINAL=Genera la entrega de "
                    "CONTRATO_DE_LA_TAREA_ACTUAL. No copies títulos, criterios ni "
                    "archivos de una dependencia como respuesta."
                ),
            ]
        )
    return "\n".join(sections)


def response_contract(operation: str) -> type[BaseModel] | None:
    return _OPERATION_CONTRACTS.get(operation)


def response_schema(operation: str) -> dict[str, Any] | None:
    contract = response_contract(operation)
    if contract is None:
        return None
    schema = deepcopy(contract.model_json_schema())
    definitions = schema.pop("$defs", {})
    inlined = _inline_references(schema, definitions)
    if not isinstance(inlined, dict):
        raise TypeError("Response schema root must be an object")
    return inlined


def ollama_response_schema(operation: str) -> dict[str, Any] | None:
    schema = response_schema(operation)
    if schema is None:
        return None
    unsupported_constraints = {
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
    }
    compatible = _without_keys(schema, unsupported_constraints)
    if not isinstance(compatible, dict):
        raise TypeError("Ollama response schema root must be an object")
    return compatible


def _inline_references(
    value: Any,
    definitions: dict[str, Any],
) -> Any:
    if isinstance(value, list):
        return [_inline_references(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.rsplit("/", 1)[-1]
        resolved = deepcopy(definitions.get(name, {}))
        resolved.update({key: item for key, item in value.items() if key != "$ref"})
        return _inline_references(resolved, definitions)
    return {
        key: _inline_references(item, definitions)
        for key, item in value.items()
    }


def _without_keys(value: Any, excluded: set[str]) -> Any:
    if isinstance(value, list):
        return [_without_keys(item, excluded) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _without_keys(item, excluded)
        for key, item in value.items()
        if key not in excluded
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
