from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from app.application.ports import Workspace
from app.domain.enums import ALLOWED_FILE_TYPES
from app.domain.errors import WorkspaceError
from app.domain.models import CheckReport, validate_relative_path


class WebWorkspace(Workspace):
    def __init__(self, root: Path, node_binary: str = "node", timeout: float = 5.0) -> None:
        self.root = root.resolve()
        self.node_binary = node_binary
        self.timeout = timeout

    def _project_dir(self, project_id: UUID) -> Path:
        return self.root / "projects" / str(project_id)

    def _artifact_dir(self, artifact_id: UUID) -> Path:
        return self.root / "artifacts" / str(artifact_id)

    def _preview_dir(self, project_id: UUID) -> Path:
        return self.root / "preview" / str(project_id)

    def _resolve(self, project_id: UUID, path: str) -> Path:
        try:
            validate_relative_path(path)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        base = self._project_dir(project_id)
        target = (base / path).resolve()
        if base not in target.parents and target != base:
            raise WorkspaceError("path escapes the project workspace")
        return target

    async def list_files(self, project_id: UUID) -> list[str]:
        base = self._project_dir(project_id)
        if not base.exists():
            return []
        return sorted(str(path.relative_to(base)) for path in base.rglob("*") if path.is_file())

    async def read_file(self, project_id: UUID, path: str) -> str:
        target = self._resolve(project_id, path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8")

    async def read_all(self, project_id: UUID) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in await self.list_files(project_id):
            if Path(path).suffix.lower() in {".png", ".ico"}:
                continue
            try:
                result[path] = await self.read_file(project_id, path)
            except UnicodeDecodeError:
                continue
        return result

    async def write_file(
        self, project_id: UUID, path: str, content: str, allowed_paths: set[str]
    ) -> None:
        try:
            validate_relative_path(path)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        if path not in allowed_paths:
            raise WorkspaceError(f"write outside the current plan is refused: {path}")
        target = self._resolve(project_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-write-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    async def publish(
        self, project_id: UUID, files: dict[str, str], artifact_ids: dict[str, UUID]
    ) -> None:
        preview = self._preview_dir(project_id)
        if preview.exists():
            shutil.rmtree(preview)
        for path, content in files.items():
            validate_relative_path(path)
            preview_target = preview / path
            preview_target.parent.mkdir(parents=True, exist_ok=True)
            preview_target.write_text(content, encoding="utf-8")
            artifact_id = artifact_ids.get(path)
            if artifact_id is not None:
                artifact_target = self._artifact_dir(artifact_id) / path
                artifact_target.parent.mkdir(parents=True, exist_ok=True)
                artifact_target.write_text(content, encoding="utf-8")

    async def write_artifact(self, artifact_id: UUID, path: str, content: str) -> None:
        validate_relative_path(path)
        target = self._artifact_dir(artifact_id) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def read_preview(self, project_id: UUID, path: str) -> bytes:
        base = self._preview_dir(project_id)
        relative = path or "index.html"
        validate_relative_path(relative)
        target = (base / relative).resolve()
        if base not in target.parents and target != base:
            raise WorkspaceError("path escapes the preview directory")
        if not target.is_file():
            raise FileNotFoundError(relative)
        return target.read_bytes()

    async def read_artifact(self, artifact_id: UUID, path: str) -> str:
        try:
            validate_relative_path(path)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        base = self._artifact_dir(artifact_id)
        target = (base / path).resolve()
        if base not in target.parents and target != base:
            raise WorkspaceError("path escapes the artifact directory")
        if not target.is_file():
            raise FileNotFoundError(path)
        return target.read_text(encoding="utf-8")

    async def delete_artifact(self, artifact_id: UUID) -> None:
        directory = self._artifact_dir(artifact_id)
        if directory.exists():
            shutil.rmtree(directory)

    async def delete_project(self, project_id: UUID) -> None:
        for directory in (
            self._project_dir(project_id),
            self._preview_dir(project_id),
        ):
            if directory.exists():
                shutil.rmtree(directory)

    async def check_readable(self, project_id: UUID, paths: list[str]) -> CheckReport:
        issues: list[str] = []
        structure_ok = True
        files_checked = 0
        for path in paths:
            try:
                target = self._resolve(project_id, path)
            except WorkspaceError as exc:
                structure_ok = False
                issues.append(str(exc))
                continue
            if not target.is_file():
                structure_ok = False
                issues.append(f"missing planned file: {path}")
                continue
            if target.stat().st_size == 0:
                structure_ok = False
                issues.append(f"empty planned file: {path}")
                continue
            file_type = target.suffix.lower().lstrip(".")
            if file_type not in ALLOWED_FILE_TYPES:
                structure_ok = False
                issues.append(f"file type not allowed: {path}")
                continue
            files_checked += 1

        js_files = [path for path in paths if path.lower().endswith(".js")]
        syntax_checked: list[str] = []
        js_syntax_ok = True
        for path in js_files:
            try:
                target = self._resolve(project_id, path)
            except WorkspaceError:
                js_syntax_ok = False
                continue
            if not target.is_file():
                continue
            try:
                process = await asyncio.create_subprocess_exec(
                    self.node_binary,
                    "--check",
                    str(target),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except FileNotFoundError:
                js_syntax_ok = False
                issues.append("node binary is not available for JavaScript syntax checks")
                break
            except TimeoutError:
                js_syntax_ok = False
                issues.append(f"JavaScript syntax check timed out: {path}")
                continue
            if process.returncode != 0:
                js_syntax_ok = False
                detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
                issues.append(
                    f"invalid JavaScript in {path}: {detail[-1] if detail else 'syntax error'}"
                )
            else:
                syntax_checked.append(path)

        return CheckReport(
            file_structure_ok=structure_ok,
            js_syntax_ok=js_syntax_ok,
            files_checked=files_checked,
            syntax_checked=syntax_checked,
            issues=issues,
        )
