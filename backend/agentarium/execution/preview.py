from __future__ import annotations

import mimetypes
from pathlib import Path


class PreviewUnavailable(FileNotFoundError):
    pass


class WorkspacePreview:
    _ENTRYPOINTS = ("index.html", "public/index.html", "src/index.html")
    _ALLOWED_SUFFIXES = {
        ".css",
        ".gif",
        ".htm",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".mjs",
        ".mp3",
        ".ogg",
        ".png",
        ".svg",
        ".ttf",
        ".wav",
        ".webp",
        ".woff",
        ".woff2",
    }
    _PROTECTED_PARTS = {".agents", ".codex", ".git", ".openai", ".ssh"}

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def resolve(self, project_id: str, requested_path: str = "") -> Path:
        if not project_id or Path(project_id).name != project_id:
            raise PreviewUnavailable("Invalid preview project")
        project_root = (self.workspace_root / project_id / "project").resolve()
        if not self._is_within(project_root, self.workspace_root):
            raise PreviewUnavailable("Preview escaped the workspace")
        if not project_root.is_dir():
            raise PreviewUnavailable("Project workspace is not available yet")

        normalized = requested_path.strip().replace("\\", "/").strip("/")
        if not normalized:
            normalized = self._entrypoint(project_root)
        relative = Path(normalized)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or any(part.casefold() in self._PROTECTED_PARTS for part in relative.parts)
            or any(
                part.casefold() == ".env" or part.casefold().startswith(".env.")
                for part in relative.parts
            )
        ):
            raise PreviewUnavailable("Preview path is not allowed")
        target = (project_root / relative).resolve()
        if not self._is_within(target, project_root) or not target.is_file():
            raise PreviewUnavailable("Preview file was not found")
        if target.suffix.casefold() not in self._ALLOWED_SUFFIXES:
            raise PreviewUnavailable("Preview file type is not allowed")
        current = target
        while current != project_root:
            if current.is_symlink():
                raise PreviewUnavailable("Preview cannot cross symbolic links")
            current = current.parent
        return target

    @staticmethod
    def media_type(path: Path) -> str:
        if path.suffix.casefold() in {".js", ".mjs"}:
            return "application/javascript"
        guessed, _ = mimetypes.guess_type(path.name)
        return guessed or "application/octet-stream"

    def _entrypoint(self, project_root: Path) -> str:
        for candidate in self._ENTRYPOINTS:
            if (project_root / candidate).is_file():
                return candidate
        raise PreviewUnavailable("No HTML entrypoint is available yet")

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents
