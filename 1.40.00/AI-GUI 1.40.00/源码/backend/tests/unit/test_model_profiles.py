import json
from pathlib import Path

from gui_agent.planning.model_profiles import ModelProfileStore


def _protect(entropy: str, value: dict) -> bytes:
    return json.dumps({"entropy": entropy, "value": value}).encode("utf-8")


def _unprotect(entropy: str, encrypted: bytes) -> dict:
    payload = json.loads(encrypted.decode("utf-8"))
    assert payload["entropy"] == entropy
    return payload["value"]


def _store(tmp_path: Path) -> ModelProfileStore:
    return ModelProfileStore(tmp_path, protect_value=_protect, unprotect_value=_unprotect)


def _profile(**changes):
    value = {
        "name": "日常测试 · Kimi",
        "provider": "kimi",
        "protocol": "chat_completions",
        "baseUrl": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.5",
        "apiKey": "secret-value-never-public",
        "makeActive": True,
    }
    value.update(changes)
    return value


def test_model_profile_keeps_key_out_of_public_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.create(_profile())
    listed = store.list()

    assert created["keyConfigured"] is True
    assert created["apiKey"] is None
    assert listed["activeProfileId"] == created["id"]
    assert "secret-value-never-public" not in (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert store.settings(created["id"]).api_key.get_secret_value() == "secret-value-never-public"


def test_model_profile_update_preserves_existing_key_when_field_is_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create(_profile())

    updated = store.update(created["id"], _profile(name="回归模型", model="kimi-k2.6", apiKey=""))

    assert updated["name"] == "回归模型"
    assert updated["connectionStatus"] == "untested"
    settings = store.settings(created["id"])
    assert settings.model == "kimi-k2.6"
    assert settings.api_key.get_secret_value() == "secret-value-never-public"


def test_deleting_active_profile_selects_next_profile_and_removes_secret(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(_profile(name="模型 A"))
    second = store.create(_profile(name="模型 B"))
    store.activate(first["id"])

    result = store.delete(first["id"])

    assert result["activeProfileId"] == second["id"]
    assert not (tmp_path / "secrets" / f"{first['id']}.dpapi").exists()
