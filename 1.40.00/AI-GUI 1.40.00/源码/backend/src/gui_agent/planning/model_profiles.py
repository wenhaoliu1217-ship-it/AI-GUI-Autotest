"""本机 AI 模型档案：元数据明文、API Key 使用 Windows DPAPI 加密。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import SecretStr

from ..onboarding.session import protect, unprotect
from .ai_provider import AIProviderError, AISettings


Protect = Callable[[str, dict], bytes]
Unprotect = Callable[[str, bytes], dict]


class ModelProfileStore:
    """保存最多 20 个 OpenAI 兼容模型档案，任何公开响应都不包含密钥。"""

    def __init__(
        self,
        root: Path,
        *,
        protect_value: Protect = protect,
        unprotect_value: Unprotect = unprotect,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._protect = protect_value
        self._unprotect = unprotect_value

    def list(self) -> dict[str, Any]:
        state = self._read_state()
        profiles = [self._public(item, state.get("activeProfileId")) for item in state["profiles"]]
        return {"activeProfileId": state.get("activeProfileId"), "profiles": profiles}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._read_state()
        if len(state["profiles"]) >= 20:
            raise ValueError("最多保存 20 个模型档案")
        api_key = str(payload.pop("apiKey", "")).strip()
        if not api_key:
            raise ValueError("保存模型档案时必须填写 API Key")
        now = _now()
        profile = {
            "id": f"model-{uuid4().hex[:12]}",
            "name": str(payload.get("name") or payload.get("model") or "未命名模型").strip(),
            "provider": str(payload.get("provider") or "custom").strip(),
            "protocol": str(payload.get("protocol") or "responses").strip(),
            "baseUrl": str(payload.get("baseUrl") or "").strip(),
            "model": str(payload.get("model") or "").strip(),
            "inputCostPerMillion": payload.get("inputCostPerMillion"),
            "outputCostPerMillion": payload.get("outputCostPerMillion"),
            "connectionStatus": "untested",
            "verifiedModelId": None,
            "capabilities": None,
            "lastTestedAt": None,
            "createdAt": now,
            "updatedAt": now,
        }
        self._validate_profile(profile)
        self._write_secret(profile["id"], api_key)
        state["profiles"].append(profile)
        if payload.get("makeActive", True) or not state.get("activeProfileId"):
            state["activeProfileId"] = profile["id"]
        self._write_state(state)
        return self._public(profile, state.get("activeProfileId"))

    def update(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._read_state()
        profile = self._find(state, profile_id)
        api_key = str(payload.pop("apiKey", "")).strip()
        for key in (
            "name", "provider", "protocol", "baseUrl", "model",
            "inputCostPerMillion", "outputCostPerMillion",
        ):
            if key in payload and payload[key] is not None:
                profile[key] = payload[key].strip() if isinstance(payload[key], str) else payload[key]
        self._validate_profile(profile)
        if api_key:
            self._write_secret(profile_id, api_key)
        profile.update({
            "connectionStatus": "untested",
            "verifiedModelId": None,
            "capabilities": None,
            "lastTestedAt": None,
            "updatedAt": _now(),
        })
        if payload.get("makeActive"):
            state["activeProfileId"] = profile_id
        self._write_state(state)
        return self._public(profile, state.get("activeProfileId"))

    def delete(self, profile_id: str) -> dict[str, Any]:
        state = self._read_state()
        self._find(state, profile_id)
        state["profiles"] = [item for item in state["profiles"] if item["id"] != profile_id]
        self._secret_path(profile_id).unlink(missing_ok=True)
        if state.get("activeProfileId") == profile_id:
            state["activeProfileId"] = state["profiles"][0]["id"] if state["profiles"] else None
        self._write_state(state)
        return self.list()

    def activate(self, profile_id: str) -> dict[str, Any]:
        state = self._read_state()
        self._find(state, profile_id)
        state["activeProfileId"] = profile_id
        self._write_state(state)
        return self.list()

    def settings(self, profile_id: str) -> AISettings:
        state = self._read_state()
        profile = self._find(state, profile_id)
        secret_path = self._secret_path(profile_id)
        if not secret_path.is_file():
            raise AIProviderError("该模型档案没有可用的本地密钥")
        try:
            secret = self._unprotect(self._entropy(profile_id), secret_path.read_bytes())
        except Exception as exc:
            raise AIProviderError(f"无法读取该模型档案的本地加密密钥：{exc}") from exc
        api_key = str(secret.get("apiKey", "")).strip()
        if not api_key:
            raise AIProviderError("该模型档案没有可用的本地密钥")
        return AISettings(
            protocol=profile["protocol"],
            base_url=profile["baseUrl"],
            model=profile["model"],
            api_key=SecretStr(api_key),
            input_cost_per_million=profile.get("inputCostPerMillion"),
            output_cost_per_million=profile.get("outputCostPerMillion"),
        ).validated()

    def record_probe(self, profile_id: str, result: dict[str, Any]) -> dict[str, Any]:
        state = self._read_state()
        profile = self._find(state, profile_id)
        profile.update({
            "connectionStatus": "connected",
            "verifiedModelId": result.get("verifiedModelId"),
            "capabilities": result.get("capabilities"),
            "lastTestedAt": _now(),
            "updatedAt": _now(),
        })
        self._write_state(state)
        return self._public(profile, state.get("activeProfileId"))

    def record_probe_failure(self, profile_id: str) -> None:
        state = self._read_state()
        profile = self._find(state, profile_id)
        profile.update({"connectionStatus": "failed", "lastTestedAt": _now(), "updatedAt": _now()})
        self._write_state(state)

    def _read_state(self) -> dict[str, Any]:
        path = self.root / "profiles.json"
        if not path.is_file():
            return {"schemaVersion": "1.40.00", "activeProfileId": None, "profiles": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("模型档案索引损坏，无法安全读取") from exc
        profiles = value.get("profiles")
        if not isinstance(profiles, list):
            raise ValueError("模型档案索引格式无效")
        return {
            "schemaVersion": "1.40.00",
            "activeProfileId": value.get("activeProfileId"),
            "profiles": profiles,
        }

    def _write_state(self, state: dict[str, Any]) -> None:
        target = self.root / "profiles.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _write_secret(self, profile_id: str, api_key: str) -> None:
        target = self._secret_path(profile_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".dpapi.tmp")
        temporary.write_bytes(self._protect(self._entropy(profile_id), {"apiKey": api_key}))
        temporary.replace(target)

    def _secret_path(self, profile_id: str) -> Path:
        self._validate_id(profile_id)
        return self.root / "secrets" / f"{profile_id}.dpapi"

    @staticmethod
    def _entropy(profile_id: str) -> str:
        return f"ai-gui-1.40.00:{profile_id}"

    @staticmethod
    def _find(state: dict[str, Any], profile_id: str) -> dict[str, Any]:
        ModelProfileStore._validate_id(profile_id)
        profile = next((item for item in state["profiles"] if item.get("id") == profile_id), None)
        if profile is None:
            raise KeyError("模型档案不存在")
        return profile

    @staticmethod
    def _validate_id(profile_id: str) -> None:
        if not profile_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in profile_id):
            raise ValueError("模型档案编号非法")

    @staticmethod
    def _validate_profile(profile: dict[str, Any]) -> None:
        if not str(profile.get("name", "")).strip():
            raise ValueError("请填写模型档案名称")
        if profile.get("protocol") not in {"responses", "chat_completions"}:
            raise ValueError("不支持的 API 协议")
        if not str(profile.get("baseUrl", "")).startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("API Base URL 必须使用 HTTPS；仅本机服务可使用 HTTP")
        if not str(profile.get("model", "")).strip():
            raise ValueError("请填写模型名称")

    def _public(self, profile: dict[str, Any], active_profile_id: str | None) -> dict[str, Any]:
        return {
            **profile,
            "apiKey": None,
            "keyConfigured": self._secret_path(profile["id"]).is_file(),
            "isActive": profile["id"] == active_profile_id,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
