from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class _DocumentIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements_by_id: dict[str, str] = {}
        self.script_sources: list[str] = []
        self.duplicate_ids: set[str] = set()
        self.label_targets: set[str] = set()
        self.form_controls: list[tuple[str, str | None, bool]] = []
        self.custom_controls_without_tab_stop: list[str] = []
        self.positive_tab_indexes: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        normalized_tag = tag.casefold()
        element_id = values.get("id")
        if element_id:
            if element_id in self.elements_by_id:
                self.duplicate_ids.add(element_id)
            self.elements_by_id[element_id] = normalized_tag
        if normalized_tag == "label" and values.get("for"):
            self.label_targets.add(str(values["for"]))
        if normalized_tag in {"input", "select", "textarea"} and (
            normalized_tag != "input"
            or str(values.get("type", "")).casefold() != "hidden"
        ):
            has_aria_name = bool(
                str(values.get("aria-label", "")).strip()
                or str(values.get("aria-labelledby", "")).strip()
            )
            self.form_controls.append(
                (normalized_tag, element_id, has_aria_name)
            )
        tabindex = values.get("tabindex")
        if tabindex:
            try:
                if int(tabindex) > 0:
                    self.positive_tab_indexes.append(
                        element_id or normalized_tag
                    )
            except ValueError:
                self.positive_tab_indexes.append(element_id or normalized_tag)
        interactive_roles = {
            "button",
            "checkbox",
            "link",
            "menuitem",
            "radio",
            "switch",
            "tab",
        }
        role = str(values.get("role", "")).casefold()
        native_interactive = normalized_tag in {
            "a",
            "button",
            "input",
            "select",
            "textarea",
        }
        if (
            role in interactive_roles
            and not native_interactive
            and tabindex != "0"
        ):
            self.custom_controls_without_tab_stop.append(
                element_id or f"<{normalized_tag}>"
            )
        source = values.get("src")
        if normalized_tag == "script" and source:
            self.script_sources.append(source)


def _relative_file(root: Path, value: str) -> Path:
    target = (root / value).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Reference escapes the validation root: {value}")
    return target


def _strip_javascript_comments(value: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", without_blocks)


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()


def _axis_reference(target: str, axis: str) -> str:
    if target == "player":
        return (
            rf"(?:(?:this\s*\.\s*)?player"
            rf"(?:\s*\.\s*position)?\s*\.\s*{axis}"
            rf"|player{axis.upper()})"
        )
    if target == "enemy":
        return (
            rf"(?:(?:this\s*\.\s*)?enemy"
            rf"(?:\s*\.\s*position)?\s*\.\s*{axis}"
            rf"|(?:this\s*\.\s*)?enemies\s*\[[^\]]+\]"
            rf"(?:\s*\.\s*position)?\s*\.\s*{axis})"
        )
    raise ValueError(f"Unsupported movement target: {target}")


def _has_assignment(code: str, target: str, axis: str) -> bool:
    subject = _axis_reference(target, axis)
    return bool(
        re.search(
            rf"{subject}\s*(?:\+\+|--|[+\-*/]?=)",
            code,
            flags=re.IGNORECASE,
        )
    )


def _enemy_axis_reacts_to_player(code: str, axis: str) -> bool:
    enemy_axis = _axis_reference("enemy", axis)
    player_axis = _axis_reference("player", axis)
    direct = re.search(
            rf"{enemy_axis}\s*[+\-*/]?=\s*[^;\r\n]{{0,180}}{player_axis}",
            code,
            flags=re.IGNORECASE,
        )
    if direct:
        return True
    direction_variables = re.findall(
        rf"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        rf"Math\.sign\s*\(\s*{player_axis}\s*-\s*{enemy_axis}\s*\)",
        code,
        flags=re.IGNORECASE,
    )
    return any(
        re.search(
            rf"{enemy_axis}\s*[+\-*/]?=\s*{re.escape(variable)}\b",
            code,
            flags=re.IGNORECASE,
        )
        for variable in direction_variables
    )


def _has_wall_collision(code: str) -> bool:
    player_axis = r"(?:player\s*\.\s*[xy]|next[XY]|new[XY])"
    player_against_wall = re.search(
        rf"{player_axis}\s*[^;\r\n]{{0,160}}"
        r"wall\s*\.\s*(?:[xy]|width|height)",
        code,
        flags=re.IGNORECASE,
    )
    wall_against_player = re.search(
        r"wall\s*\.\s*(?:[xy]|width|height)\s*[^;\r\n]{0,160}"
        rf"{player_axis}",
        code,
        flags=re.IGNORECASE,
    )
    proposed_coordinates = bool(
        re.search(r"\b(?:nextX|newX)\b", code, flags=re.IGNORECASE)
        and re.search(r"\b(?:nextY|newY)\b", code, flags=re.IGNORECASE)
    )
    rollback_coordinates = bool(
        re.search(
            r"(?:const|let)\s+(?:previousX|oldX)\s*=\s*player\.x",
            code,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"(?:const|let)\s+(?:previousY|oldY)\s*=\s*player\.y",
            code,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"player\.x\s*=\s*(?:previousX|oldX)",
            code,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"player\.y\s*=\s*(?:previousY|oldY)",
            code,
            flags=re.IGNORECASE,
        )
    )
    return bool(
        (player_against_wall or wall_against_player)
        and re.search(
            r"\b(?:walls?\s*\.\s*some|for\s*\([^)]*\bwall\b[^)]*\))",
            code,
            flags=re.IGNORECASE,
        )
        and (proposed_coordinates or rollback_coordinates)
    )


def _has_visible_wall_style(code: str) -> bool:
    wall_loops = list(
        re.finditer(
            r"for\s*\([^)]*\bwall\b[^)]*\)",
            code,
            flags=re.IGNORECASE,
        )
    )
    for loop in wall_loops:
        prefix = code[max(0, loop.start() - 320):loop.start()]
        colors = re.findall(
            r"fillStyle\s*=\s*(['\"])([^'\"]+)\1",
            prefix,
            flags=re.IGNORECASE,
        )
        if colors and colors[-1][1].casefold() not in {
            "#000",
            "#000000",
            "black",
            "rgb(0,0,0)",
        }:
            return True
    return False


def _has_full_canvas_wall(code: str) -> bool:
    width_match = re.search(r"canvas\s*\.\s*width\s*=\s*(\d+)", code)
    height_match = re.search(r"canvas\s*\.\s*height\s*=\s*(\d+)", code)
    if not width_match or not height_match:
        return False
    canvas_width = int(width_match.group(1))
    canvas_height = int(height_match.group(1))
    walls = re.findall(
        r"\{\s*x\s*:\s*0\s*,\s*y\s*:\s*0\s*,\s*"
        r"width\s*:\s*(\d+)\s*,\s*height\s*:\s*(\d+)\s*\}",
        code,
        flags=re.IGNORECASE,
    )
    return any(
        int(width) >= canvas_width and int(height) >= canvas_height
        for width, height in walls
    )


def _has_lives_system(code: str) -> bool:
    state = r"(?:lives|vidas)"
    declared = re.search(
        rf"(?:const|let|var)\s+{state}\b",
        code,
        flags=re.IGNORECASE,
    )
    decremented = re.search(
        rf"\b{state}\s*(?:--|-=|=\s*{state}\s*-)",
        code,
        flags=re.IGNORECASE,
    )
    game_over = re.search(
        rf"\b{state}\s*(?:<=|===?|==)\s*0",
        code,
        flags=re.IGNORECASE,
    )
    return bool(declared and decremented and game_over)


def _has_victory_and_restart(code: str) -> bool:
    normalized = _normalized(code)
    victory = bool(
        re.search(
            r"\b(?:collectibles|pellets|dots|coins)\s*\.\s*length\s*"
            r"(?:===?|<=)\s*0",
            code,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"\b(?:victoria|ganaste|ganador|victory|you win|won)\b",
            normalized,
        )
    )
    restart_functions = re.findall(
        r"function\s+((?:restart|reset|reiniciar|reinicia)\w*)\s*\(",
        code,
        flags=re.IGNORECASE,
    )
    restart_wired = any(
        len(
            re.findall(
                rf"\b{re.escape(function_name)}\s*\(",
                code,
                flags=re.IGNORECASE,
            )
        )
        >= 2
        or bool(
            re.search(
                rf"(?:addEventListener|onclick\s*=)[^;\r\n]{{0,160}}"
                rf"\b{re.escape(function_name)}\b",
                code,
                flags=re.IGNORECASE,
            )
        )
        for function_name in restart_functions
    )
    return victory and restart_wired


def validate(
    html_path: Path,
    script_paths: list[Path],
    *,
    navigation: bool,
    pursuit: bool,
    pacman: bool,
    wall_collision: bool = False,
    lives: bool = False,
    win_restart: bool = False,
    keyboard_accessibility: bool = False,
    crud: bool = False,
    search: bool = False,
    filtering: bool = False,
    confirmation: bool = False,
    empty_state: bool = False,
    local_storage: bool = False,
    json_transfer: bool = False,
    guide: bool = False,
) -> list[str]:
    root = Path.cwd().resolve()
    document = _DocumentIndex()
    html_source = html_path.read_text(encoding="utf-8")
    document.feed(html_source)
    errors: list[str] = []
    for duplicate_id in sorted(document.duplicate_ids):
        errors.append(
            f"HTML contains duplicate id '{duplicate_id}'. Element ids must be unique."
        )
    if keyboard_accessibility:
        for form_tag, control_id, has_aria_name in document.form_controls:
            if has_aria_name or (
                control_id is not None
                and control_id in document.label_targets
            ):
                continue
            identity = f"#{control_id}" if control_id else f"<{form_tag}>"
            errors.append(
                f"Keyboard accessibility requires an accessible label for {identity}. "
                "Associate a <label for=\"...\"> or provide aria-label."
            )
        for identity in document.custom_controls_without_tab_stop:
            errors.append(
                f"Custom interactive control {identity} requires tabindex=\"0\" "
                "for keyboard access."
            )
        for identity in document.positive_tab_indexes:
            errors.append(
                f"Keyboard accessibility rejects positive tabindex on {identity}; "
                "use document order or tabindex=\"0\"."
            )

    scripts = list(script_paths)
    for source in document.script_sources:
        parsed = urlsplit(source)
        if parsed.scheme or parsed.netloc or source.startswith(("data:", "//")):
            continue
        relative = parsed.path
        try:
            referenced = _relative_file(html_path.parent, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not referenced.is_file():
            errors.append(f"HTML references a missing script: {relative}")
        elif referenced not in scripts:
            scripts.append(referenced)

    javascript_sources = [
        path.read_text(encoding="utf-8")
        for path in scripts
        if path.is_file() and (path == root or root in path.parents)
    ]
    javascript = "\n".join(javascript_sources)
    assignments = re.findall(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"document\.getElementById\(\s*(['\"])([^'\"]+)\2\s*\)",
        javascript,
    )
    referenced_ids = re.findall(
        r"document\.getElementById\(\s*(['\"])([^'\"]+)\1\s*\)",
        javascript,
    )
    dynamic_ids = {
        element_id
        for _, element_id in re.findall(
            r"\.\s*id\s*=\s*(['\"])([^'\"]+)\1",
            javascript,
        )
    }
    for _, element_id in referenced_ids:
        if (
            element_id not in document.elements_by_id
            and element_id not in dynamic_ids
        ):
            errors.append(f"JavaScript references a missing element id: {element_id}")
    for variable, _, element_id in assignments:
        if re.search(rf"\b{re.escape(variable)}\s*\.\s*getContext\s*\(", javascript):
            canvas_tag = document.elements_by_id.get(element_id)
            if canvas_tag is None and element_id in dynamic_ids:
                canvas_tag = "canvas"
            if canvas_tag != "canvas":
                errors.append(
                    f"{variable}.getContext requires <canvas id=\"{element_id}\">, "
                    f"but HTML defines <{canvas_tag or 'missing'}>"
                )

    created_canvases = re.findall(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"document\.createElement\(\s*(['\"])canvas\2\s*\)",
        javascript,
        flags=re.IGNORECASE,
    )
    for variable, _ in created_canvases:
        escaped = re.escape(variable)
        attached = re.search(
            rf"(?:appendChild|append|replaceWith)\s*\(\s*{escaped}\s*\)",
            javascript,
            flags=re.IGNORECASE,
        )
        if not attached:
            errors.append(
                f"Dynamically created canvas '{variable}' must be attached to the "
                "document, for example gameContainer.appendChild(canvas)."
            )
        assigned_ids = re.findall(
            rf"\b{escaped}\s*\.\s*id\s*=\s*(['\"])([^'\"]+)\1",
            javascript,
            flags=re.IGNORECASE,
        )
        for _, assigned_id in assigned_ids:
            if assigned_id in document.elements_by_id:
                errors.append(
                    f"Dynamically created canvas '{variable}' reuses existing "
                    f"element id '{assigned_id}'. Use a unique id such as game-canvas."
                )
        render_functions = re.findall(
            rf"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{{"
            rf"[^{{}}]{{0,500}}\b{escaped}\s*\.\s*getContext\s*\(",
            javascript,
            flags=re.IGNORECASE,
        )
        for function_name in render_functions:
            direct_occurrences = len(
                re.findall(
                    rf"\b{re.escape(function_name)}\s*\(",
                    javascript,
                )
            )
            scheduled = re.search(
                rf"(?:requestAnimationFrame|setInterval|setTimeout)\s*\(\s*"
                rf"{re.escape(function_name)}\b",
                javascript,
            )
            if direct_occurrences < 3 and not scheduled:
                errors.append(
                    f"Canvas renderer '{function_name}' must run during "
                    "initialization, before the first user input."
                )

    if re.search(r"\.\s*style\s*\.\s*(?:left|top)\s*=", javascript):
        combined_styles = f"{html_source}\n{javascript}"
        if not re.search(
            r"position\s*[:=]\s*['\"]?(?:absolute|fixed)",
            combined_styles,
            flags=re.IGNORECASE,
        ):
            errors.append(
                "DOM coordinates use style.left/style.top but movable elements "
                "lack position: absolute or fixed."
            )
        if not re.search(
            r"#game-container\s*\{[^}]*position\s*:\s*relative",
            html_source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            errors.append(
                "An absolutely positioned DOM game requires "
                "#game-container { position: relative; }."
            )

    code = _strip_javascript_comments(javascript)
    normalized_code = _normalized(code)
    for source in javascript_sources:
        source_code = _strip_javascript_comments(source)
        for variable in re.findall(
            r"^const\s+([A-Za-z_$][\w$]*)\s*=",
            source_code,
            flags=re.MULTILINE,
        ):
            without_declarations = re.sub(
                rf"\b(?:const|let|var)\s+{re.escape(variable)}\s*=",
                "",
                source_code,
            )
            if re.search(
                rf"\b{re.escape(variable)}\s*=",
                without_declarations,
            ):
                errors.append(
                    f"JavaScript variable '{variable}' is declared const but "
                    "reassigned. Declare resettable game state with let or mutate "
                    "it in place."
                )
    if crud:
        if not re.search(
            r"\.\s*(?:push|unshift)\s*\(",
            code,
            flags=re.IGNORECASE,
        ):
            errors.append(
                "CRUD requires a concrete create operation that adds a record "
                "to application state."
            )
        edit_function = re.search(
            r"(?:function\s+|(?:const|let)\s+)"
            r"(?:edit|update|editar|actualizar|modificar)\w*",
            normalized_code,
            flags=re.IGNORECASE,
        )
        edit_mutation = re.search(
            r"(?:\[[^\]]+\]\s*\.\s*\w+\s*=|Object\.assign\s*\(|"
            r"\.\s*map\s*\([^)]*=>[^;]{0,240}\.\.\.)",
            code,
            flags=re.IGNORECASE,
        )
        if not (edit_function and edit_mutation):
            errors.append(
                "CRUD requires a concrete edit/update operation that mutates "
                "an existing record."
            )
        delete_with_splice = re.search(
            r"\.\s*splice\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        delete_with_filter = re.search(
            r"\b([A-Za-z_$][\w$]*)\s*=\s*\1\s*\.\s*filter\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        if not (delete_with_splice or delete_with_filter):
            errors.append(
                "CRUD requires a concrete delete operation that removes a "
                "record from application state."
            )
    if search:
        search_signal = re.search(
            r"(?:search|buscar|busqueda|query)\w*",
            normalized_code,
        )
        search_behavior = re.search(
            r"\.\s*(?:includes|startsWith|indexOf)\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        search_event = re.search(
            r"addEventListener\s*\(\s*['\"](?:input|keyup|change)['\"]",
            code,
            flags=re.IGNORECASE,
        )
        if not (search_signal and search_behavior and search_event):
            errors.append(
                "Search requires a named search control, a text match operation, "
                "and an input/change event."
            )
    if filtering:
        filter_signal = re.search(
            r"(?:filter|filtrar|filtro|category|categoria|estado.?stock)",
            normalized_code,
        )
        filter_behavior = re.search(
            r"\.\s*filter\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        filter_event = re.search(
            r"addEventListener\s*\(\s*['\"](?:input|change)['\"]",
            code,
            flags=re.IGNORECASE,
        )
        if not (filter_signal and filter_behavior and filter_event):
            errors.append(
                "Filtering requires a filter/category control, Array.filter "
                "behavior, and an input/change event."
            )
    if confirmation and not re.search(
        r"\b(?:window\s*\.\s*)?confirm\s*\(",
        code,
        flags=re.IGNORECASE,
    ):
        errors.append(
            "Destructive actions require a wired confirm(...) decision before "
            "deleting data."
        )
    if empty_state:
        detects_empty = re.search(
            r"\.\s*length\s*(?:===?|<=)\s*0",
            code,
            flags=re.IGNORECASE,
        )
        renders_empty = re.search(
            r"(?:sin\s+(?:productos|datos|registros)|"
            r"no\s+hay\s+(?:productos|datos|registros)|empty\s+state)",
            normalized_code,
        )
        if not (detects_empty and renders_empty):
            errors.append(
                "Empty-state behavior must detect an empty collection and render "
                "a clear no-data message."
            )
    if local_storage:
        has_load = re.search(
            r"localStorage\s*\.\s*getItem\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        has_save = re.search(
            r"localStorage\s*\.\s*setItem\s*\(",
            code,
            flags=re.IGNORECASE,
        )
        if not (has_load and has_save):
            errors.append(
                "Local persistence requires both localStorage.getItem(...) and "
                "localStorage.setItem(...)."
            )
    if json_transfer:
        exports_json = (
            re.search(r"\bBlob\s*\(", code)
            and re.search(r"(?:createObjectURL|\.download\s*=)", code)
            and re.search(r"JSON\s*\.\s*stringify\s*\(", code)
        )
        imports_json = (
            re.search(r"\bFileReader\s*\(", code)
            and re.search(r"JSON\s*\.\s*parse\s*\(", code)
            and re.search(r"\btry\s*\{", code)
            and re.search(r"\bcatch\s*\(", code)
        )
        if not exports_json:
            errors.append(
                "JSON export requires JSON.stringify, a Blob, and a downloadable URL."
            )
        if not imports_json:
            errors.append(
                "JSON import requires FileReader, JSON.parse, and guarded error handling."
            )
    if guide:
        guide_files = [
            path
            for path in root.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and path.suffix.casefold() in {".md", ".txt"}
        ]
        embedded_guide = re.search(
            r"(?:como\s+(?:usar|ejecutar|probar)|"
            r"instrucciones\s+de\s+uso|pruebas\s+principales)",
            _normalized(html_source),
        )
        if not guide_files and not embedded_guide:
            errors.append(
                "Guide deliverable requires a Markdown/text guide or an embedded "
                "section explaining how to run and test the main features."
            )
    if navigation:
        has_input = bool(
            re.search(
                r"(?:addEventListener\s*\(\s*['\"]"
                r"(?:keydown|keyup|pointerdown|touchstart)"
                r"|on(?:keydown|keyup|pointerdown|touchstart)\s*=)",
                code,
                flags=re.IGNORECASE,
            )
        )
        if not has_input:
            errors.append(
                "Navigation criterion requires keyboard, pointer, or touch input. "
                "Add document.addEventListener('keydown', ...) and call the player "
                "movement function for ArrowUp, ArrowDown, ArrowLeft, ArrowRight."
            )
        if not _has_assignment(code, "player", "x"):
            errors.append("Navigation criterion requires horizontal player movement")
        if not _has_assignment(code, "player", "y"):
            errors.append("Navigation criterion requires vertical player movement")
        if (
            re.search(
                r"function\s+movePlayer\s*\(\s*direction\s*\)",
                code,
                flags=re.IGNORECASE,
            )
            and len(re.findall(r"\bdirection\b", code, flags=re.IGNORECASE)) < 2
        ):
            errors.append(
                "movePlayer(direction) must apply the selected direction to the "
                "next player coordinates."
            )

    if pursuit:
        if not _has_assignment(code, "enemy", "x"):
            errors.append("Aggressive enemies must update their horizontal position")
        if not _has_assignment(code, "enemy", "y"):
            errors.append("Aggressive enemies must update their vertical position")
        if not (
            _enemy_axis_reacts_to_player(code, "x")
            or _enemy_axis_reacts_to_player(code, "y")
        ):
            errors.append(
                "Enemy movement must derive from the player position. Update each "
                "enemy axis from the matching player axis, for example "
                "enemy.x += Math.sign(player.x - enemy.x) and "
                "enemy.y += Math.sign(player.y - enemy.y)."
            )

    if pacman:
        required_features = {
            "maze or walls": (
                r"\b(?:maze|labyrinth|laberinto|wall|walls|muro|muros)\b"
            ),
            "collectibles": (
                r"\b(?:collectible|collectibles|pellet|pellets|dot|dots|"
                r"coin|coins|moneda|monedas|"
                r"pildora|pildoras|punto|puntos)\b"
            ),
            "score": r"\b(?:score|puntaje|puntuacion|marcador)\b",
            "collision rules": (
                r"(?:collision|collide|colision|colisiones|intersect)\w*\b"
            ),
        }
        for label, pattern in required_features.items():
            if not re.search(pattern, normalized_code):
                errors.append(f"Pac-Man criterion requires {label}")
        if not re.search(
            r"\bscore\s*(?:\+\+|--|[+\-*/]=|=\s*score\s*[+\-])",
            code,
            flags=re.IGNORECASE,
        ):
            errors.append(
                "Pac-Man score must change when the player collects an item, "
                "for example score += 10."
            )
        if not re.search(
            r"\b(?:collectibles|pellets|dots|coins)\s*\.\s*"
            r"(?:splice|filter|shift|pop)\s*\(",
            code,
            flags=re.IGNORECASE,
        ):
            errors.append(
                "Pac-Man collectibles must be consumed after collection, for "
                "example collectibles.splice(index, 1)."
            )
    if wall_collision and not _has_wall_collision(code):
        errors.append(
            "Maze walls must block player movement with real rectangle or tile "
            "collision. Compare the next player coordinates with wall.x, wall.y, "
            "wall.width and wall.height before assigning player.x/player.y."
        )
    if wall_collision and _has_full_canvas_wall(code):
        errors.append(
            "A maze wall covers the entire canvas. Use border and interior wall "
            "segments that leave traversable corridors."
        )
    if wall_collision and not _has_visible_wall_style(code):
        errors.append(
            "Maze walls must use a visible color that contrasts with the dark "
            "canvas background before drawing each wall."
        )
    if lives and not _has_lives_system(code):
        errors.append(
            "The game needs a lives state that decreases on enemy collision and "
            "reaches a game-over condition, for example lives -= 1 and lives <= 0."
        )
    if win_restart and not _has_victory_and_restart(code):
        errors.append(
            "The game needs victory when collectibles.length === 0 and a wired "
            "restart/reset function that can start a fresh round."
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--script", action="append", default=[])
    parser.add_argument("--navigation", action="store_true")
    parser.add_argument("--keyboard-accessibility", action="store_true")
    parser.add_argument("--crud", action="store_true")
    parser.add_argument("--search", action="store_true")
    parser.add_argument("--filtering", action="store_true")
    parser.add_argument("--destructive-guard", action="store_true")
    parser.add_argument("--empty-state", action="store_true")
    parser.add_argument("--local-storage", action="store_true")
    parser.add_argument("--json-transfer", action="store_true")
    parser.add_argument("--guide", action="store_true")
    parser.add_argument("--pursuit", action="store_true")
    parser.add_argument("--pacman", action="store_true")
    parser.add_argument("--wall-collision", action="store_true")
    parser.add_argument("--lives", action="store_true")
    parser.add_argument("--win-restart", action="store_true")
    arguments = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        html_path = _relative_file(root, arguments.html)
        script_paths = [
            _relative_file(root, value)
            for value in arguments.script
        ]
        errors = validate(
            html_path,
            script_paths,
            navigation=arguments.navigation,
            pursuit=arguments.pursuit,
            pacman=arguments.pacman,
            wall_collision=arguments.wall_collision,
            lives=arguments.lives,
            win_restart=arguments.win_restart,
            keyboard_accessibility=arguments.keyboard_accessibility,
            crud=arguments.crud,
            search=arguments.search,
            filtering=arguments.filtering,
            confirmation=arguments.destructive_guard,
            empty_state=arguments.empty_state,
            local_storage=arguments.local_storage,
            json_transfer=arguments.json_transfer,
            guide=arguments.guide,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print("Web document and behavior contract verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
