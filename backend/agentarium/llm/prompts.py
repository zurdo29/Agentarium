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
from agentarium.planning import BriefProposal, DecomposeProposal, PlanProposal

from .base import ModelRequest

PLANNING_PROMPT_VERSION = "planning-v4"
WORKSPACE_PROMPT_VERSION = "workspace-v11"
DECOMPOSE_PROMPT_VERSION = "decompose-v3"
PLAN_REVISION_PROMPT_VERSION = "plan-revision-v3"

_OPERATION_CONTRACTS: dict[str, type[BaseModel]] = {
    "brief": BriefProposal,
    "plan": PlanProposal,
    "work": WorkArtifactProposal,
    "test": TestEvaluationProposal,
    "review": ReviewEvaluationProposal,
    "decompose": DecomposeProposal,
    "plan_revision": PlanProposal,
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
        "sustituir esa cobertura. "
        "Declara owned_paths por tarea: los archivos que esa tarea va a escribir "
        "(puede quedar vacío si el entregable no es un archivo concreto). Dos "
        "tareas sin relación de dependencia entre sí no deben declarar el mismo "
        "owned_path salvo que genuinamente necesiten combinar contenido en el "
        "mismo artefacto — en ese caso usa shared_component (un nombre corto "
        "que identifique ese artefacto compartido) igual en ambas, y "
        "output_strategy distinto de 'exclusive' en cada una: 'fragment' si la "
        "tarea produce una pieza autocontenida que otra combina después, "
        "'consolidation' si es la tarea que combina esas piezas en el archivo "
        "final, 'patch' si extiende un archivo que otra tarea ya posee. Una "
        "dependencia entre tareas sólo ordena ejecución, no dice cómo combinar "
        "contenido: si dos tareas comparten un archivo, usa shared_component "
        "además de (no en vez de) declarar la dependencia si corresponde. "
        "Preferí dividir un componente compartido en archivos separados (por "
        "ejemplo módulos bajo una carpeta) en vez de forzar varias tareas "
        "'exclusive' a escribir el mismo archivo. "
        "SOLICITUD.payload.runtime_capabilities declara qué puede ejecutar el "
        "worker (third_party_packages_allowed, network_policy). Si una tarea "
        "sólo puede cumplirse con una capacidad fuera de esa declaración, no "
        "la plantees como alcanzable sin más: decláralo explícitamente como "
        "limitación conocida de la tarea."
    ),
    "plan_revision": (
        "SOLICITUD.payload.previous_plan tiene tareas cuyos owned_paths "
        "colisionan sin una agrupación válida — ver path_conflicts (pares de "
        "claves de tarea y los paths en conflicto). Devolvé un PlanProposal "
        "completo y corregido (no un parche): mismas claves de tarea salvo que "
        "decidas fusionar o dividir alguna. Para cada conflicto, resolvé con una "
        "de estas estrategias: (1) fusionar las tareas en conflicto en una sola "
        "si en realidad modifican el mismo componente; (2) separar el "
        "componente compartido en archivos distintos, uno por tarea; (3) darles "
        "el mismo shared_component y un output_strategy no-exclusive "
        "('fragment' para las que producen piezas, 'consolidation' para la que "
        "las combina) si genuinamente deben combinar contenido en un archivo "
        "final; (4) si alguna genuinamente depende de que la otra termine "
        "primero, agregá la dependencia Y un shared_component+output_strategy "
        "coherente — la dependencia sola no alcanza, no explica cómo combinar "
        "el contenido. No dejes ningún path del conflicto sin resolver. "
        "SOLICITUD.payload.runtime_capabilities sigue vigente en la revisión, "
        "igual que en el plan original: si la reorganización de tareas exige una "
        "capacidad fuera de lo declarado (third_party_packages_allowed, "
        "network_policy), no la asumas disponible — decláralo como limitación "
        "en la tarea correspondiente en vez de plantearla como resuelta."
    ),
    "work": (
        "La tarea actual es SOLICITUD.payload.task y tiene prioridad absoluta. Produce "
        "un artefacto NUEVO que satisfaga sus expected_outputs y cada uno de sus "
        "acceptance_criteria. "
        "task.output_strategy define cómo te relacionás con owned_paths: "
        "'exclusive' (default) — el archivo es tuyo, comportamiento normal. "
        "'fragment' — producís una pieza autocontenida bajo tu propio owned_path "
        "(por ejemplo un módulo o archivo de rutas separado); nunca toques el "
        "archivo de entrada compartido (task.shared_component identifica ese "
        "componente), otra tarea de consolidación lo arma con tu pieza. "
        "'patch' — extendés un archivo que ya posee otra tarea aprobada: "
        "revisá dependency_artifacts/dependency_references para ver su contenido "
        "actual y modificalo agregando lo tuyo, nunca lo sobreescribas desde cero "
        "ni lo devuelvas sin cambios. "
        "'consolidation' — dependés de las tareas fragment/patch de tu mismo "
        "shared_component; tu trabajo es combinar sus salidas (ya aprobadas, "
        "están en dependency_artifacts) en tu propio owned_path, no inventar "
        "contenido nuevo que ellas no produjeron. "
        "Los dependency_artifacts son sólo referencias de entrada salvo que tu "
        "propia estrategia sea patch/consolidation (ahí son la base a extender): "
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
        "cada criterio de la tarea que la entrega cubre. "
        "SOLICITUD.payload.project.decisions es un registro interno de gestión del "
        "propio Agentarium (por ejemplo, la decisión de ejecutar un único hito "
        "vertical); nunca es contenido de dominio ni una decisión de arquitectura "
        "del producto solicitado. No lo copies, resumas ni adaptes como si fuera "
        "parte del entregable. "
        "Si un archivo .py declarado será ejecutado, debés respetar las "
        "capacidades y políticas declaradas en "
        "SOLICITUD.payload.runtime_capabilities: "
        "third_party_packages_allowed lista los paquetes de terceros permitidos "
        "(vacía hoy: sólo biblioteca estándar de Python, por ejemplo sqlite3, "
        "http.server, json, csv, argparse) y network_policy es la política "
        "declarada que debe respetarse, no una garantía técnica de aislamiento "
        "('deny' hoy: no accedas a la red). No importes Flask, FastAPI, requests, "
        "pandas ni nada fuera de third_party_packages_allowed. Si el objetivo "
        "pide explícitamente una librería de terceros como parte de la entrega, "
        "decláralo como limitación en vez de producir un script que no puede "
        "ejecutarse. Un preflight estático rechaza estos imports antes de "
        "llegar a tester/revisor (no es sólo una instrucción de prosa). Si "
        "SOLICITUD.payload.retry_guidance.cumulative_rejected_imports no está "
        "vacío, esos módulos ya fueron rechazados en un intento previo de "
        "esta misma tarea: no los repitas, ni con otro alias."
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
        "Si dependency_artifacts no está vacío, compara explícitamente su contenido "
        "contra artifact.files[].content: rechaza si el artefacto actual contradice, "
        "duplica de forma distinta o redefine datos, cifras o términos ya establecidos "
        "en una dependencia aprobada. "
        "acceptance_results debe contener exacta y únicamente las claves recibidas en "
        "acceptance_criteria, sin agregar checks técnicos. No exijas entregables fuera "
        "del alcance de la tarea actual."
    ),
    "decompose": (
        "SOLICITUD.payload.task agotó sus intentos sin producir una entrega "
        "aceptable. Divídela en entre 2 y 4 subtareas más chicas e "
        "independientemente resolubles. "
        "SOLICITUD.payload.acceptance_criteria_index numera los criterios del "
        "padre como 'ac-1', 'ac-2', etc. Repartí esos identificadores entre "
        "las subtareas con acceptance_criteria_ids: cada id debe aparecer en "
        "exactamente una subtarea, cada subtarea debe recibir al menos uno, y "
        "entre todas deben cubrirse todos. El texto de cada criterio se copia "
        "del padre, así que no hace falta que lo reescribas fiel: lo que "
        "importa es el reparto de ids. Usa prior_review_feedback y "
        "prior_validation_failures (evidencia de por qué falló) para separar "
        "las causas de falla en subtareas distintas cuando sea razonable, en "
        "vez de dividir arbitrariamente. Cada subtítulo debe ser distinto y "
        "describir un entregable concreto y verificable por sí solo. "
        "Declara owned_paths por subtarea; si dos subtareas necesitan escribir "
        "en el mismo archivo, dales el mismo shared_component y un "
        "output_strategy no-exclusive ('fragment' para las que producen una "
        "pieza, 'consolidation' para la que las combina) — nunca dejes a dos "
        "subtareas reclamando el mismo path como 'exclusive'."
    ),
}


def render_prompt(request: ModelRequest, agent: AgentDefinition) -> str:
    contract = response_contract(request.operation)
    version = (
        PLANNING_PROMPT_VERSION
        if request.operation in {"brief", "plan"}
        else WORKSPACE_PROMPT_VERSION
        if request.operation == "work"
        else DECOMPOSE_PROMPT_VERSION
        if request.operation == "decompose"
        else PLAN_REVISION_PROMPT_VERSION
        if request.operation == "plan_revision"
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
