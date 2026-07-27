# Agentarium

Agentarium es una plataforma local-first que convierte objetivos de alto nivel en
proyectos verificables ejecutados por cinco roles: director, manager técnico,
worker, tester y revisor crítico. Los roles intercambian artefactos estructurados;
el historial completo se persiste, pero cada ejecución recibe sólo el contexto
necesario.

El MVP funciona sin un modelo real gracias al proveedor `mock`. Ollama y cualquier
servidor compatible con la API de OpenAI se pueden habilitar desde configuración.
No se descarga ningún modelo automáticamente.

## Requisitos comprobados

- Windows 10 o posterior y PowerShell 5.1+
- Python 3.11 o posterior
- Node.js 22.13 o posterior
- Git
- Ollama es opcional

En el entorno de desarrollo inicial se detectaron Python 3.14.6, Node 24.18.0,
npm 11.16.0, Git 2.54.0 y el cliente Ollama 0.32.3. Ollama no estaba ejecutándose.

## Instalación

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

`setup.ps1` crea `.venv`, instala las dependencias Python dentro del proyecto y
ejecuta `npm.cmd ci`. No requiere permisos administrativos.

## Inicio

```powershell
.\dev.ps1
```

Después abre:

- Interfaz: http://127.0.0.1:3000
- API y documentación: http://127.0.0.1:8000/docs

Para detener ambos procesos:

```powershell
.\stop.ps1
```

El inicio de producción local usa:

```powershell
.\start.ps1
```

## Prueba exacta del flujo vertical

```powershell
.\test.ps1
.\.venv\Scripts\agentarium.exe init
.\.venv\Scripts\agentarium.exe project create `
  "Crear un prototipo ARPG con progresión de objetos y alcance pequeño"
.\.venv\Scripts\agentarium.exe project run <ID_DEVUELTO>
.\.venv\Scripts\agentarium.exe project status <ID_DEVUELTO>
```

La suite usa exclusivamente el proveedor mock. Demuestra planificación,
dependencias, ejecución, rechazo inicial simulado, corrección, testing, revisión,
persistencia y consulta por API.

## Comandos

```text
agentarium doctor
agentarium init
agentarium start
agentarium project create <objetivo>
agentarium project run <project-id>
agentarium project pause <project-id>
agentarium project resume <project-id>
agentarium project status <project-id>
agentarium test
```

## Configuración y datos

- Roles: `configs/roles/default.yaml`
- Modelos: `configs/models/default.yaml`
- Política de comandos: `configs/policies/security.yaml`
- Base de datos predeterminada: `runtime/agentarium.db`
- Workspaces de proyectos: `workspaces/<project-id>/`

Variables configurables están documentadas en `.env.example`. Consulta
`docs/architecture/overview.md` para el diseño y `docs/decisions/` para decisiones
arquitectónicas.

## Seguridad

El ejecutor no usa `shell=True`, exige un directorio dentro del workspace, filtra
el entorno, limita tiempo y logs, y acepta sólo comandos configurados. Acciones
destructivas, credenciales, cambios externos, publicación y modelos grandes
requieren aprobación humana.
