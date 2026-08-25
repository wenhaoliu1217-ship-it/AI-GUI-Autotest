"""Content-addressed fixed test files; plans never receive host filesystem paths."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MAX_ASSET_BYTES = 20 * 1024 * 1024


class FileAssetError(ValueError):
    pass


class FileAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.blobs = self.root / "blobs"
        self.projects = self.root / "projects"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.projects.mkdir(parents=True, exist_ok=True)

    def register(self, project_id: str, filename: str, content: bytes, declared_sha256: str) -> dict:
        self._project_id(project_id)
        safe_name = Path(filename).name
        if safe_name != filename or not re.fullmatch(r"[A-Za-z0-9_. -]{1,160}", safe_name):
            raise FileAssetError("测试文件名非法或包含路径")
        if not content or len(content) > MAX_ASSET_BYTES:
            raise FileAssetError("测试文件必须为 1 字节至 20 MB")
        actual = hashlib.sha256(content).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", declared_sha256) or actual != declared_sha256:
            raise FileAssetError("测试文件 SHA-256 与声明值不一致")
        target = self.blobs / actual
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
        records = [item for item in self.list(project_id) if item["sha256"] != actual]
        record = {
            "ref": f"asset:{actual}", "sha256": actual, "filename": safe_name,
            "bytes": len(content), "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        records.append(record)
        self._write_manifest(project_id, records)
        return record

    def list(self, project_id: str) -> list[dict]:
        target = self._manifest(project_id)
        return json.loads(target.read_text(encoding="utf-8")) if target.is_file() else []

    def resolve(self, project_id: str, asset_ref: str) -> Path:
        digest = asset_ref.removeprefix("asset:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FileAssetError("文件引用必须是 asset:<sha256>")
        if not any(item["sha256"] == digest for item in self.list(project_id)):
            raise FileAssetError("文件引用未登记到当前项目")
        target = (self.blobs / digest).resolve()
        if self.blobs not in target.parents or not target.is_file():
            raise FileAssetError("文件资产不存在")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise FileAssetError("文件资产完整性校验失败")
        return target

    def _project_id(self, project_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", project_id):
            raise FileAssetError("项目编号非法")
        return project_id

    def _manifest(self, project_id: str) -> Path:
        return self.projects / f"{self._project_id(project_id)}.json"

    def _write_manifest(self, project_id: str, records: list[dict]) -> None:
        target = self._manifest(project_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
