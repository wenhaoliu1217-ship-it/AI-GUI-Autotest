"""小规模本地部署使用的 JSON 项目仓库。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import AuditRecord, CompatibilityReport, EnvironmentConfig, ProjectConfig, ScenarioConfig, SessionMetadata
from .session import protect, unprotect


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[ProjectConfig]:
        projects: list[ProjectConfig] = []
        for path in self.root.glob("*/project.json"):
            try:
                projects.append(ProjectConfig.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get(self, project_id: str) -> ProjectConfig | None:
        path = self._dir(project_id) / "project.json"
        if not path.is_file():
            return None
        return ProjectConfig.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: ProjectConfig) -> ProjectConfig:
        target = self._dir(project.id) / "project.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(target, project.model_dump(mode="json", by_alias=True))
        return project

    def delete_project(self, project_id: str) -> None:
        directory = self._dir(project_id)
        if not directory.is_dir():
            return
        for name in ("project.json", "compatibility.json", "session.json", "storage-state.dpapi", "audit.jsonl"):
            (directory / name).unlink(missing_ok=True)
        for folder in ("environments", "scenarios"):
            child = directory / folder
            if child.is_dir():
                for path in child.glob("*.json"):
                    path.unlink(missing_ok=True)
                child.rmdir()
        directory.rmdir()

    def list_environments(self, project_id: str) -> list[EnvironmentConfig]:
        return self._list_models(project_id, "environments", EnvironmentConfig)

    def get_environment(self, project_id: str, environment_id: str) -> EnvironmentConfig | None:
        target = self._object_path(project_id, "environments", environment_id)
        if not target.is_file():
            return None
        return EnvironmentConfig.model_validate_json(target.read_text(encoding="utf-8"))

    def save_environment(self, item: EnvironmentConfig) -> EnvironmentConfig:
        target = self._object_path(item.project_id, "environments", item.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(target, item.model_dump(mode="json", by_alias=True))
        return item

    def list_scenarios(self, project_id: str) -> list[ScenarioConfig]:
        return self._list_models(project_id, "scenarios", ScenarioConfig)

    def get_scenario(self, project_id: str, scenario_id: str) -> ScenarioConfig | None:
        target = self._object_path(project_id, "scenarios", scenario_id)
        if not target.is_file():
            return None
        return ScenarioConfig.model_validate_json(target.read_text(encoding="utf-8"))

    def save_scenario(self, item: ScenarioConfig) -> ScenarioConfig:
        target = self._object_path(item.project_id, "scenarios", item.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(target, item.model_dump(mode="json", by_alias=True))
        return item

    def audit(self, record: AuditRecord) -> None:
        path = self.root / "audit.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n")

    def list_audit(self, project_id: str) -> list[AuditRecord]:
        self._dir(project_id)
        path = self.root / "audit.jsonl"
        if not path.is_file():
            return []
        records: list[AuditRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = AuditRecord.model_validate_json(line)
                if record.project_id == project_id:
                    records.append(record)
            except ValueError:
                continue
        return records

    def save_report(self, report: CompatibilityReport) -> CompatibilityReport:
        target = self._dir(report.project_id) / "compatibility.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(target, report.model_dump(mode="json", by_alias=True))
        return report

    def get_report(self, project_id: str) -> CompatibilityReport | None:
        path = self._dir(project_id) / "compatibility.json"
        if not path.is_file():
            return None
        return CompatibilityReport.model_validate_json(path.read_text(encoding="utf-8"))

    def save_session(self, project: ProjectConfig, state: dict, metadata: SessionMetadata) -> SessionMetadata:
        directory = self._dir(project.id)
        directory.mkdir(parents=True, exist_ok=True)
        encrypted_target = directory / "storage-state.dpapi"
        temporary = encrypted_target.with_suffix(".dpapi.tmp")
        temporary.write_bytes(protect(project.id, state))
        temporary.replace(encrypted_target)
        self._write(directory / "session.json", metadata.model_dump(mode="json", by_alias=True))
        return metadata

    def load_session(self, project_id: str) -> dict | None:
        path = self._dir(project_id) / "storage-state.dpapi"
        if not path.is_file():
            return None
        return unprotect(project_id, path.read_bytes())

    def get_session_metadata(self, project_id: str) -> SessionMetadata | None:
        path = self._dir(project_id) / "session.json"
        if not path.is_file():
            return None
        return SessionMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def _dir(self, project_id: str) -> Path:
        if not project_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in project_id):
            raise ValueError("项目编号非法")
        target = (self.root / project_id).resolve()
        if self.root not in target.parents:
            raise ValueError("项目编号非法")
        return target

    def _object_path(self, project_id: str, folder: str, object_id: str) -> Path:
        if not object_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in object_id):
            raise ValueError("对象编号非法")
        return self._dir(project_id) / folder / f"{object_id}.json"

    def _list_models(self, project_id: str, folder: str, model_type):
        items = []
        for path in (self._dir(project_id) / folder).glob("*.json"):
            try:
                items.append(model_type.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
