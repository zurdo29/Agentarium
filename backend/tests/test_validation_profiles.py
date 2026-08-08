from pathlib import Path

import pytest
from agentarium.config.settings import project_root
from agentarium.domain.models import ScriptExecutionContract
from agentarium.execution import (
    RuntimeCapabilityManifest,
    ValidationProfile,
    ValidationProfileExecutor,
    WorkspaceFileProposal,
    WorkspaceMaterializer,
)
from agentarium.execution.web_smoke import (
    _enemy_axis_reacts_to_player,
    _has_full_canvas_wall,
    _has_lives_system,
    _has_victory_and_restart,
    _has_visible_wall_style,
    _has_wall_collision,
)


def _policy_path() -> Path:
    return project_root() / "configs" / "policies" / "security.yaml"


def test_enemy_pursuit_accepts_object_state_and_intermediate_direction() -> None:
    code = (
        "const dx=Math.sign(this.player.x-enemy.x);"
        "const dy=Math.sign(this.player.y-enemy.y);"
        "enemy.x+=dx*5; enemy.y+=dy*5;"
    )

    assert _enemy_axis_reacts_to_player(code, "x")
    assert _enemy_axis_reacts_to_player(code, "y")


def test_advanced_gameplay_contract_requires_real_state_transitions() -> None:
    incomplete = (
        "const walls=[]; const collectibles=[]; let score=0;"
        "const player={x:0,y:0}; function draw(){"
        "for(const wall of walls){ctx.fillRect(wall.x,wall.y,10,10);}}"
    )
    complete = (
        "const walls=[{x:0,y:0,width:20,height:20}];"
        "let lives=3; let score=0; let collectibles=[{x:2,y:2}];"
        "const player={x:30,y:30,width:10,height:10};"
        "function hitsWall(nextX,nextY){return walls.some(wall=>"
        "nextX < wall.x + wall.width && nextX + player.width > wall.x && "
        "nextY < wall.y + wall.height && nextY + player.height > wall.y);}"
        "function hitEnemy(){lives -= 1; if(lives <= 0){gameOver=true;}}"
        "function resetGame(){lives=3;score=0;collectibles=[{x:2,y:2}];}"
        "if(collectibles.length === 0){message='Victoria';}"
        "button.addEventListener('click', resetGame);"
    )

    assert not _has_wall_collision(incomplete)
    assert not _has_lives_system(incomplete)
    assert not _has_victory_and_restart(incomplete)
    assert _has_wall_collision(complete)
    assert _has_lives_system(complete)
    assert _has_victory_and_restart(complete)


def test_maze_contract_rejects_full_canvas_and_invisible_walls() -> None:
    broken = (
        "canvas.width=600;canvas.height=400;"
        "const walls=[{x:0,y:0,width:600,height:400}];"
        "ctx.fillStyle='#000';"
        "for(const wall of walls){ctx.fillRect(wall.x,wall.y,wall.width,wall.height);}"
    )
    visible = (
        "ctx.fillStyle='#1d4ed8';"
        "for(const wall of walls){ctx.fillRect(wall.x,wall.y,wall.width,wall.height);}"
    )

    assert _has_full_canvas_wall(broken)
    assert not _has_visible_wall_style(broken)
    assert _has_visible_wall_style(visible)


def test_advanced_acceptance_criteria_activate_web_contract_flags() -> None:
    flags = ValidationProfileExecutor._web_contract_flags(
        [
            "Las paredes del laberinto bloquean el movimiento",
            "Las vidas disminuyen con cada colision con enemigas",
            "Hay victoria y reinicio de la partida",
        ]
    )

    assert flags == [
        "--navigation",
        "--pursuit",
        "--wall-collision",
        "--lives",
        "--win-restart",
    ]


def test_inventory_keyboard_accessibility_does_not_activate_game_movement() -> None:
    flags = ValidationProfileExecutor._web_contract_flags(
        [
            "Diseño responsive y accesible con navegación por teclado",
            "Los formularios se pueden recorrer con tabulación",
        ]
    )

    assert flags == ["--keyboard-accessibility"]


def test_inventory_scope_activates_generic_application_contracts() -> None:
    flags = ValidationProfileExecutor._web_contract_flags(
        [
            "Crear, editar y eliminar productos",
            "Buscar por nombre y filtrar por categoría",
            "Persistir cambios en localStorage",
            "Exportar e importar JSON con manejo de errores",
            "Mostrar estado vacío y confirmar antes de borrar",
            "Entregar una guía breve con instrucciones",
        ]
    )

    assert flags == [
        "--crud",
        "--search",
        "--filtering",
        "--destructive-guard",
        "--empty-state",
        "--local-storage",
        "--json-transfer",
        "--guide",
    ]


@pytest.mark.asyncio
async def test_keyboard_accessibility_accepts_labeled_native_controls(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<form id="inventory-form">'
                    '<label for="name">Nombre</label>'
                    '<input id="name" name="name">'
                    '<button type="submit">Guardar</button>'
                    '</form><script src="app.js"></script>'
                ),
                purpose="Formulario accesible",
            ),
            WorkspaceFileProposal(
                path="app.js",
                content=(
                    "document.getElementById('inventory-form')"
                    ".addEventListener('submit', event => event.preventDefault());"
                ),
                purpose="Interacción del formulario",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=[
            "Diseño accesible con navegación por teclado",
        ],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert web_result.passed


@pytest.mark.asyncio
async def test_generic_application_contract_accepts_complete_crud_flow(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<label for="search-products">Buscar</label>'
                    '<input id="search-products">'
                    '<label for="filter-category">Categoría</label>'
                    '<select id="filter-category"><option value="">Todas</option></select>'
                    '<p id="empty-state"></p>'
                    '<button id="add-product">Agregar</button>'
                    '<script src="app.js"></script>'
                ),
                purpose="Interfaz CRUD",
            ),
            WorkspaceFileProposal(
                path="app.js",
                content=(
                    "let products=JSON.parse(localStorage.getItem('products')||'[]');"
                    "const searchProducts=document.getElementById('search-products');"
                    "const filterCategory=document.getElementById('filter-category');"
                    "function save(){localStorage.setItem('products',JSON.stringify(products));}"
                    "function addProduct(){"
                    "products.push({name:'Tornillo',category:'Metal'});save();}"
                    "function editProduct(index){products[index].name='Tuerca';save();}"
                    "function deleteProduct(index){if(confirm('¿Eliminar?')){"
                    "products.splice(index,1);save();}}"
                    "function render(){if(products.length===0){"
                    "document.getElementById('empty-state').textContent='Sin productos';}}"
                    "searchProducts.addEventListener('input',()=>products.filter("
                    "product=>product.name.includes(searchProducts.value)));"
                    "filterCategory.addEventListener('change',()=>products.filter("
                    "product=>product.category===filterCategory.value));"
                    "function exportJson(){const blob=new Blob([JSON.stringify(products)]);"
                    "const link=document.createElement('a');"
                    "link.href=URL.createObjectURL(blob);link.download='inventory.json';}"
                    "function importJson(file){const reader=new FileReader();"
                    "reader.onload=event=>{try{products=JSON.parse(event.target.result);}"
                    "catch(error){console.error(error);}};reader.readAsText(file);}"
                    "document.getElementById('add-product').addEventListener('click',addProduct);"
                    "render();"
                ),
                purpose="Comportamiento CRUD completo",
            ),
            WorkspaceFileProposal(
                path="README.md",
                content=(
                    "# Guía\n\nCómo ejecutar y probar las funciones principales."
                ),
                purpose="Guía de uso",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=[
            "Crear, editar y eliminar productos",
            "Buscar por nombre y filtrar por categoría",
            "Persistir cambios en localStorage",
            "Exportar e importar JSON con manejo de errores",
            "Mostrar estado vacío y confirmar antes de borrar",
            "Entregar una guía breve con instrucciones",
        ],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert web_result.passed


@pytest.mark.asyncio
async def test_generic_application_contract_rejects_add_only_flow(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<button id="add-product">Agregar</button>'
                    '<script src="app.js"></script>'
                ),
                purpose="Interfaz incompleta",
            ),
            WorkspaceFileProposal(
                path="app.js",
                content=(
                    "const products=[];"
                    "function addProduct(){products.push({name:'Tornillo'});}"
                    "document.getElementById('add-product')"
                    ".addEventListener('click',addProduct);"
                ),
                purpose="Flujo que sólo agrega",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=[
            "Crear, editar y eliminar productos",
            "Mostrar estado vacío y confirmar antes de borrar",
        ],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "edit/update operation" in web_result.result.stderr
    assert "delete operation" in web_result.result.stderr
    assert "confirm(...)" in web_result.result.stderr
    assert "Empty-state behavior" in web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_rejects_duplicate_element_ids(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<section id="product-form"></section>'
                    '<form id="product-form"></form>'
                    '<script src="app.js"></script>'
                ),
                purpose="Documento con ids duplicados",
            ),
            WorkspaceFileProposal(
                path="app.js",
                content=(
                    "document.getElementById('product-form')"
                    ".addEventListener('submit', event => event.preventDefault());"
                ),
                purpose="Interacción ambigua",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["La aplicación web funciona"],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "duplicate id 'product-form'" in web_result.result.stderr


@pytest.mark.asyncio
async def test_fixed_profiles_capture_command_evidence(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="src/main.py",
                content="answer: int = 42\n",
                purpose="Python source",
            ),
            WorkspaceFileProposal(
                path="data/config.json",
                content='{"enabled": true}\n',
                purpose="JSON configuration",
            ),
            WorkspaceFileProposal(
                path="web/app.js",
                content="const answer = 42;\n",
                purpose="JavaScript source",
            ),
        ],
    )

    results = await validator.validate("project", files)

    assert {result.profile for result in results} == {
        ValidationProfile.WORKSPACE_INVENTORY,
        ValidationProfile.PYTHON_SYNTAX,
        ValidationProfile.IMPORT_PREFLIGHT,
        ValidationProfile.JSON_SYNTAX,
        ValidationProfile.JAVASCRIPT_SYNTAX,
    }
    failed = {
        result.profile.value: {
            "command": result.result.command,
            "stdout": result.result.stdout,
            "stderr": result.result.stderr,
            "return_code": result.result.return_code,
        }
        for result in results
        if not result.passed
    }
    assert not failed, failed
    assert all(result.result.return_code == 0 for result in results)
    assert all(not result.result.timed_out for result in results)
    inventory = next(
        result
        for result in results
        if result.profile is ValidationProfile.WORKSPACE_INVENTORY
    )
    assert "src/main.py" in inventory.result.stdout
    assert "data/config.json" in inventory.result.stdout
    assert "web/app.js" in inventory.result.stdout


@pytest.mark.asyncio
async def test_json_profile_reports_syntax_failure_without_executing_content(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="data/invalid.json",
                content='{"missing": "brace"\n',
                purpose="Invalid fixture",
            )
        ],
    )

    results = await validator.validate("project", files)
    json_result = next(
        result for result in results if result.profile is ValidationProfile.JSON_SYNTAX
    )

    assert not json_result.passed
    assert json_result.result.return_code != 0
    assert json_result.result.stderr
    assert json_result.as_evidence()["passed"] is False


@pytest.mark.asyncio
async def test_web_profile_rejects_broken_canvas_and_incomplete_gameplay(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content='<div id="game"></div><script src="game.js"></script>',
                purpose="Juego web",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const canvas=document.getElementById('game');"
                    "const ctx=canvas.getContext('2d');"
                    "const player={x:0,y:0}; const enemies=[{x:2,y:2}];"
                    "document.addEventListener('keydown',e=>{player.y+=1;});"
                    "function checkCollision(){return false;}"
                ),
                purpose="Lógica del juego",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=[
            "Los jugadores pueden navegar evitando enemigos agresivos",
            "El juego sigue las reglas básicas de Pac-Man",
        ],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "requires <canvas" in web_result.result.stderr
    assert "horizontal player movement" in web_result.result.stderr
    assert "Pac-Man criterion" in web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_accepts_concrete_pacman_gameplay(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content='<canvas id="game"></canvas><script src="game.js"></script>',
                purpose="Juego web",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const canvas=document.getElementById('game');"
                    "const ctx=canvas.getContext('2d');"
                    "const maze=[[1]], pellets=[{x:1,y:1}]; let score=0;"
                    "const player={x:0,y:0}; const enemies=[{x:2,y:2}];"
                    "document.addEventListener('keydown',event=>{"
                    "if(event.key==='ArrowLeft')player.x-=1;"
                    "if(event.key==='ArrowRight')player.x+=1;"
                    "if(event.key==='ArrowUp')player.y-=1;"
                    "if(event.key==='ArrowDown')player.y+=1;});"
                    "enemies.forEach(enemy=>{"
                    "const horizontal=Math.sign(player.x-enemy.x);"
                    "const vertical=Math.sign(player.y-enemy.y);"
                    "enemy.x+=horizontal; enemy.y+=vertical;});"
                    "score+=10; pellets.splice(0,1);"
                    "function checkCollision(){return player.x===enemies[0].x;}"
                ),
                purpose="Lógica completa",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=[
            "Los jugadores pueden navegar evitando enemigos agresivos",
            "El juego sigue las reglas básicas de Pac-Man",
        ],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert web_result.passed, web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_rejects_unattached_dynamic_canvas(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<div id="game-container"></div>'
                    '<script src="game.js"></script>'
                ),
                purpose="Game shell",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const canvas=document.createElement('canvas');"
                    "canvas.id='game-container';"
                    "function drawGame(){canvas.getContext('2d');}"
                    "document.addEventListener('keydown',()=>drawGame());"
                ),
                purpose="Invisible dynamic canvas",
            ),
        ],
    )

    results = await validator.validate("project", files)
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "must be attached" in web_result.result.stderr
    assert "reuses existing element id" in web_result.result.stderr
    assert "must run during initialization" in web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_accepts_a_referenced_dynamic_canvas_id(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    '<div id="game-container"></div>'
                    '<script src="game.js"></script>'
                ),
                purpose="Game shell",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const container=document.getElementById('game-container');"
                    "const canvas=document.createElement('canvas');"
                    "canvas.id='game-canvas';container.appendChild(canvas);"
                    "const mounted=document.getElementById('game-canvas');"
                    "mounted.getContext('2d');"
                ),
                purpose="Visible dynamic canvas",
            ),
        ],
    )

    results = await validator.validate("project", files)
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert web_result.passed, web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_rejects_reassigned_const_state(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content='<script src="game.js"></script>',
                purpose="Game shell",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const collectibles=[];"
                    "function resetGame(){collectibles=[];}"
                ),
                purpose="Broken reset state",
            ),
        ],
    )

    results = await validator.validate("project", files)
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "declared const but reassigned" in web_result.result.stderr


@pytest.mark.asyncio
async def test_each_html_validates_only_scripts_in_its_own_surface(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content='<canvas id="game"></canvas><script src="game.js"></script>',
                purpose="Root game",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const canvas=document.getElementById('game');"
                    "const ctx=canvas.getContext('2d');"
                ),
                purpose="Root logic",
            ),
            WorkspaceFileProposal(
                path="src/index.html",
                content='<canvas id="game"></canvas><script src="game.js"></script>',
                purpose="Secondary game",
            ),
            WorkspaceFileProposal(
                path="src/game.js",
                content=(
                    "const canvas=document.getElementById('game');"
                    "const ctx=canvas.getContext('2d');"
                ),
                purpose="Secondary logic",
            ),
        ],
    )

    results = await validator.validate("project", files)
    web_results = [
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    ]

    assert len(web_results) == 2
    assert all(result.passed for result in web_results)


@pytest.mark.asyncio
async def test_web_profile_checks_existing_sibling_html_for_javascript_only_fix(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    materializer.materialize(
        "project",
        "setup",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content='<div id="game"></div><script src="game.js"></script>',
                purpose="Entrada existente",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content="const player={x:0,y:0};",
                purpose="Lógica anterior",
            ),
        ],
    )
    project_root = workspace_root / "project" / "project"
    changed_files = materializer.stage(
        "project",
        project_root,
        [
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const canvas=document.getElementById('game');"
                    "canvas.getContext('2d');"
                ),
                purpose="Corrección JavaScript",
            )
        ],
    )

    results = await validator.validate(
        "project",
        changed_files,
        acceptance_criteria=["El juego web permite navegar"],
    )
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "requires <canvas" in web_result.result.stderr


@pytest.mark.asyncio
async def test_web_profile_rejects_dom_coordinates_without_positioning(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="index.html",
                content=(
                    "<style>#game-container{width:400px}</style>"
                    '<div id="game-container"></div>'
                    '<script src="game.js"></script>'
                ),
                purpose="Juego DOM",
            ),
            WorkspaceFileProposal(
                path="game.js",
                content=(
                    "const player=document.createElement('div');"
                    "player.style.left='20px'; player.style.top='20px';"
                    "document.getElementById('game-container').appendChild(player);"
                ),
                purpose="Posicionamiento del juego",
            ),
        ],
    )

    results = await validator.validate("project", files)
    web_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.WEB_APPLICATION
    )

    assert not web_result.passed
    assert "lack position" in web_result.result.stderr
    assert "position: relative" in web_result.result.stderr


@pytest.mark.asyncio
async def test_import_preflight_passes_stdlib_only_imports(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="app.py",
                content=(
                    "import sqlite3\nimport json\n"
                    "from http.server import HTTPServer\n"
                ),
                purpose="API stdlib-only",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_allows_future_annotations_import(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="app.py",
                content=(
                    "from __future__ import annotations\n\n"
                    "def f() -> None:\n    return None\n"
                ),
                purpose="Modulo con anotaciones diferidas",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_rejects_disallowed_third_party_import(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="app.py",
                content="import flask\n",
                purpose="API con dependencia no permitida",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert not preflight.passed
    assert preflight.targets == ("flask",)
    assert "flask" in preflight.result.stderr


@pytest.mark.asyncio
async def test_import_preflight_allows_declared_third_party_package(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    capabilities = RuntimeCapabilityManifest(
        python_version="3.11.0",
        executables_allowed=("python",),
        executables_available=("python",),
        third_party_packages_allowed=("requests",),
    )
    validator = ValidationProfileExecutor(
        workspace_root, _policy_path(), capabilities=capabilities
    )
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="client.py",
                content="import requests\n",
                purpose="Cliente HTTP con dependencia declarada",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_rejects_import_inside_function_body(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="app.py",
                content=(
                    "def build_app():\n"
                    "    import flask\n"
                    "    return flask.Flask(__name__)\n"
                ),
                purpose="Import diferido dentro de una funcion",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert not preflight.passed
    assert preflight.targets == ("flask",)


@pytest.mark.asyncio
async def test_import_preflight_rejects_import_behind_try_except_fallback(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="app.py",
                content=(
                    "try:\n"
                    "    import flask\n"
                    "except ImportError:\n"
                    "    flask = None\n"
                ),
                purpose="Import con guard de fallback",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert not preflight.passed
    assert preflight.targets == ("flask",)


@pytest.mark.asyncio
async def test_import_preflight_allows_same_delivery_sibling_imports(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="helpers.py",
                content="def add(a, b):\n    return a + b\n",
                purpose="Modulo auxiliar de la misma entrega",
            ),
            WorkspaceFileProposal(
                path="app.py",
                content="import helpers\n\nprint(helpers.add(1, 2))\n",
                purpose="Modulo principal que importa a su hermano",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_allows_import_from_local_subpackage(
    tmp_path: Path,
) -> None:
    # Regression: the earlier implementation only collected file *stems*
    # into a flat, location-blind set, so "library" (a directory, not a
    # file) never matched and this got wrongly rejected as external.
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="library/models.py",
                content="class Book:\n    pass\n",
                purpose="Submodulo del paquete local",
            ),
            WorkspaceFileProposal(
                path="main.py",
                content="from library import models\n\nprint(models.Book)\n",
                purpose="Modulo principal que importa su propio subpaquete",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_allows_package_referring_to_its_own_name(
    tmp_path: Path,
) -> None:
    # Regression: a module inside a package that refers to its own
    # containing package by top-level name (library/api.py doing
    # `from library import models`) is not a sibling of api.py's own
    # directory (library/) -- "library" sits one level up, at the project
    # root. A single-level sibling check (the previous fix) missed this
    # very common layout and wrongly rejected it as external.
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="library/models.py",
                content="class Book:\n    pass\n",
                purpose="Submodulo del paquete",
            ),
            WorkspaceFileProposal(
                path="library/api.py",
                content="from library import models\n\nprint(models.Book)\n",
                purpose="Modulo que se refiere a su propio paquete contenedor",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert preflight.passed


@pytest.mark.asyncio
async def test_import_preflight_rejects_disallowed_import_despite_unrelated_same_named_file(
    tmp_path: Path,
) -> None:
    # Regression: the earlier implementation collected stems from every
    # .py file anywhere in the delivery, so an unrelated otro/flask.py
    # made `import flask` in app/main.py look "local" even though nothing
    # under app/ actually makes it resolve -- exactly the late
    # ModuleNotFoundError this profile exists to catch early instead.
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="otro/flask.py",
                content="# Coincidentally named, unrelated to app/.\n",
                purpose="Archivo sin relacion, mismo nombre que una libreria externa",
            ),
            WorkspaceFileProposal(
                path="app/main.py",
                content="import flask\n",
                purpose="Modulo que pretende usar Flask de verdad",
            ),
        ],
    )

    results = await validator.validate("project", files)
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )

    assert not preflight.passed
    assert preflight.targets == ("flask",)


@pytest.mark.asyncio
async def test_import_preflight_absent_without_python_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="README.md",
                content="# Sin codigo Python\n",
                purpose="Documentacion",
            ),
        ],
    )

    results = await validator.validate("project", files)

    assert not any(
        result.profile is ValidationProfile.IMPORT_PREFLIGHT for result in results
    )


@pytest.mark.asyncio
async def test_import_preflight_survives_unparseable_source_without_crashing(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="broken.py",
                content="def broken(:\n    pass\n",
                purpose="Fuente con error de sintaxis",
            ),
        ],
    )

    results = await validator.validate("project", files)

    python_syntax = next(
        result
        for result in results
        if result.profile is ValidationProfile.PYTHON_SYNTAX
    )
    assert not python_syntax.passed
    preflight = next(
        (
            result
            for result in results
            if result.profile is ValidationProfile.IMPORT_PREFLIGHT
        ),
        None,
    )
    assert preflight is None or preflight.passed


@pytest.mark.asyncio
async def test_script_execution_skipped_when_import_preflight_fails(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content="import flask\n\nprint('never gets here')\n",
                purpose="Herramienta con dependencia no permitida",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
    )

    assert not any(
        result.profile is ValidationProfile.SCRIPT_EXECUTION for result in results
    )
    preflight = next(
        result
        for result in results
        if result.profile is ValidationProfile.IMPORT_PREFLIGHT
    )
    assert not preflight.passed


@pytest.mark.asyncio
async def test_script_execution_contract_skipped_when_import_preflight_fails(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content="import flask\n\nprint('never gets here')\n",
                purpose="Herramienta con dependencia no permitida",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(entrypoint="tool.py", args=[]),
    )

    assert not any(
        result.profile is ValidationProfile.SCRIPT_EXECUTION for result in results
    )


@pytest.mark.asyncio
async def test_script_execution_fails_when_python_claimed_without_source(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="README.md",
                content="# Herramienta\nEjecuta con `python tool.py`.",
                purpose="Documentacion",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Herramienta de linea de comandos en Python"],
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert not script_result.passed
    assert "no incluye" in script_result.result.stderr


@pytest.mark.asyncio
async def test_script_execution_runs_a_working_python_tool(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "with open('output.txt', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write('done')\n"
                ),
                purpose="Herramienta de linea de comandos",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert script_result.passed
    assert script_result.result.return_code == 0


@pytest.mark.asyncio
async def test_script_execution_fails_when_script_crashes(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content="raise RuntimeError('deliberate failure')\n",
                purpose="Script con error en tiempo de ejecucion",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Ejecutar el script de Python"],
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert not script_result.passed
    assert script_result.result.return_code != 0
    assert "RuntimeError" in script_result.result.stderr


@pytest.mark.asyncio
async def test_script_execution_profile_absent_without_execution_signals(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="helpers.py",
                content="def add(a, b):\n    return a + b\n",
                purpose="Modulo auxiliar",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Sumar dos numeros correctamente"],
    )

    assert not any(
        result.profile is ValidationProfile.SCRIPT_EXECUTION for result in results
    )


@pytest.mark.asyncio
async def test_script_execution_contract_uses_declared_args(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "import sys\n"
                    "if len(sys.argv) < 2:\n"
                    "    raise SystemExit('falta el argumento de entrada')\n"
                    "with open('output.txt', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write(sys.argv[1])\n"
                ),
                purpose="Herramienta que exige un argumento real",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(entrypoint="tool.py", args=["hola"]),
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert script_result.passed, script_result.result.stderr


@pytest.mark.asyncio
async def test_script_execution_contract_fails_when_declared_output_is_missing(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "try:\n"
                    "    raise ValueError('deliberate')\n"
                    "except Exception as error:\n"
                    "    print(error)\n"
                ),
                purpose="Script que traga la excepcion sin sys.exit",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", produces="result.json"
        ),
    )
    script_results = [
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    ]

    # The raw execution "succeeds" (return code 0, exception swallowed)...
    assert any(
        result.passed and result.result.return_code == 0 for result in script_results
    )
    # ...but the profile still fails overall because the declared output
    # never showed up, which is exactly the gap this contract closes.
    assert any(
        not result.passed and "no aparece" in result.result.stderr
        for result in script_results
    )


@pytest.mark.asyncio
async def test_script_execution_contract_ignores_a_preexisting_produces_file(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content="# no hace nada\n",
                purpose="Script vacio que no produce nada por si mismo",
            ),
            WorkspaceFileProposal(
                path="result.json",
                content='{"ya estaba aqui": true}',
                # Simulates the file being materialized alongside the
                # script, or left over from a previous attempt in the same
                # worktree — not something tool.py itself created.
                purpose="Artefacto preexistente, no generado por el script",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", produces="result.json"
        ),
    )
    script_results = [
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    ]

    # Without erasing the preexisting file before running, this would pass
    # even though the script did nothing — exactly the false positive this
    # guards against.
    assert any(
        not result.passed and "no aparece" in result.result.stderr
        for result in script_results
    )


@pytest.mark.asyncio
async def test_script_execution_contract_fails_when_entrypoint_is_missing(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="other.py",
                content="print('hola')\n",
                purpose="Script que no es el entrypoint declarado",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(entrypoint="tool.py"),
    )
    script_results = [
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    ]

    # No silent fall back to blind mode for the file that IS there.
    assert len(script_results) == 1
    assert not script_results[0].passed
    assert "tool.py" in script_results[0].result.stderr
    assert "no lo incluye" in script_results[0].result.stderr


@pytest.mark.asyncio
async def test_script_execution_contract_fails_when_no_python_files_exist(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="README.md",
                content="# Herramienta\nEjecuta con `python tool.py`.",
                purpose="Documentacion sin fuente",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(entrypoint="tool.py"),
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert not script_result.passed
    assert "tool.py" in script_result.result.stderr


@pytest.mark.asyncio
async def test_script_execution_contract_only_runs_the_declared_entrypoint(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "import sys\n"
                    "if len(sys.argv) < 2:\n"
                    "    raise SystemExit('falta argumento')\n"
                ),
                purpose="Entrypoint declarado",
            ),
            WorkspaceFileProposal(
                path="helper.py",
                # Deliberately fails if executed standalone, even though it
                # would be a perfectly valid module to import. Proves the
                # contract is the sole authority: this must never run.
                content="raise RuntimeError('helper.py no deberia ejecutarse solo')\n",
                purpose="Modulo auxiliar sin contrato propio",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", args=["valor"]
        ),
    )
    script_results = {
        result.targets: result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    }

    # Only the declared entrypoint was executed — no SCRIPT_EXECUTION result
    # exists for helper.py at all, blind or otherwise.
    assert script_results.keys() == {("tool.py",)}
    assert script_results[("tool.py",)].passed
    assert script_results[("tool.py",)].result.command[-1] == "valor"


@pytest.mark.asyncio
async def test_script_execution_contract_activates_profile_without_prose_signals(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "with open('output.txt', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write('ok')\n"
                ),
                purpose="Herramienta",
            ),
        ],
    )

    # Same non-triggering prose as
    # test_script_execution_profile_absent_without_execution_signals — the
    # only difference is a declared contract, which must be enough on its
    # own to activate the profile.
    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Sumar dos numeros correctamente"],
        execution_contract=ScriptExecutionContract(entrypoint="tool.py"),
    )

    assert any(
        result.profile is ValidationProfile.SCRIPT_EXECUTION for result in results
    )


@pytest.mark.asyncio
async def test_script_execution_contract_resolves_produces_relative_to_entrypoint_dir(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="src/tool.py",
                content=(
                    "with open('result.json', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write('{}')\n"
                ),
                purpose="Herramienta en subdirectorio",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(
            entrypoint="src/tool.py", produces="result.json"
        ),
    )
    script_results = [
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    ]

    # If `produces` resolved against the project root instead of the
    # entrypoint's own directory, this would fail to find the file the
    # script actually wrote.
    assert len(script_results) == 1
    assert script_results[0].passed, script_results[0].result.stderr


# P3.4 (ADR 0034): allow_project_code_execution=False -- an imported
# project's fail-closed gate. These pair with the SCRIPT_EXECUTION tests
# above: same fixtures, only the new flag differs, so the delta is the gate
# itself, not a different scenario.


@pytest.mark.asyncio
async def test_authority_gate_does_not_affect_non_executing_profiles(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content="def add(a, b):\n    return a + b\n",
                purpose="Modulo auxiliar, sin pedir ejecucion",
            ),
            WorkspaceFileProposal(
                path="data/config.json",
                content='{"valido": true}',
                purpose="Config",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Sumar dos numeros correctamente"],
        allow_project_code_execution=False,
    )

    assert not any(result.blocked_by_authority for result in results)
    profiles = {result.profile for result in results}
    assert ValidationProfile.WORKSPACE_INVENTORY in profiles
    assert ValidationProfile.PYTHON_SYNTAX in profiles
    assert ValidationProfile.JSON_SYNTAX in profiles
    assert ValidationProfile.IMPORT_PREFLIGHT in profiles
    assert all(
        result.passed
        for result in results
        if result.profile
        in {
            ValidationProfile.WORKSPACE_INVENTORY,
            ValidationProfile.PYTHON_SYNTAX,
            ValidationProfile.JSON_SYNTAX,
            ValidationProfile.IMPORT_PREFLIGHT,
        }
    )
    # No SCRIPT_EXECUTION at all here -- nothing requested it, so there is
    # nothing for the gate to block in the first place.
    assert not any(
        result.profile is ValidationProfile.SCRIPT_EXECUTION for result in results
    )


@pytest.mark.asyncio
async def test_authority_gate_blocks_blind_script_execution_without_running_it(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "with open('output.txt', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write('done')\n"
                ),
                purpose="Herramienta de linea de comandos",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Ejecutar la herramienta de linea de comandos en Python"],
        allow_project_code_execution=False,
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert script_result.blocked_by_authority
    assert not script_result.passed
    assert script_result.as_evidence()["blocked_by_authority"] is True
    # The real proof, not just the structured flag: the script never ran.
    assert list(workspace_root.rglob("output.txt")) == []


@pytest.mark.asyncio
async def test_authority_gate_blocks_declared_contract_execution_without_running_it(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="tool.py",
                content=(
                    "with open('result.json', 'w', encoding='utf-8') as handle:\n"
                    "    handle.write('{}')\n"
                ),
                purpose="Herramienta con contrato declarado",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", produces="result.json"
        ),
        allow_project_code_execution=False,
    )
    script_results = [
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    ]

    assert len(script_results) == 1
    assert script_results[0].blocked_by_authority
    assert not script_results[0].passed
    assert list(workspace_root.rglob("result.json")) == []


@pytest.mark.asyncio
async def test_authority_gate_reports_authority_not_missing_file(
    tmp_path: Path,
) -> None:
    """Both reasons apply here (no .py delivered, and execution disabled) --
    the authority reason must win, since it is the real and durable one:
    even a delivery that did include a .py file would still be blocked."""
    workspace_root = tmp_path / "workspaces"
    materializer = WorkspaceMaterializer(workspace_root, _policy_path())
    validator = ValidationProfileExecutor(workspace_root, _policy_path())
    files = materializer.materialize(
        "project",
        "task",
        1,
        [
            WorkspaceFileProposal(
                path="README.md",
                content="# Herramienta\nEjecuta con `python tool.py`.",
                purpose="Documentacion",
            ),
        ],
    )

    results = await validator.validate(
        "project",
        files,
        acceptance_criteria=["Herramienta de linea de comandos en Python"],
        allow_project_code_execution=False,
    )
    script_result = next(
        result
        for result in results
        if result.profile is ValidationProfile.SCRIPT_EXECUTION
    )

    assert script_result.blocked_by_authority
    assert not script_result.passed
    assert "no incluye" not in script_result.result.stderr
