import hashlib

import pytest

from gui_agent.artifacts import FileAssetError, FileAssetStore


def test_file_asset_is_content_addressed_and_project_scoped(tmp_path) -> None:
    store = FileAssetStore(tmp_path / "assets")
    content = b"fixed e2e evidence"
    digest = hashlib.sha256(content).hexdigest()
    record = store.register("project-a", "evidence.txt", content, digest)
    assert record["ref"] == f"asset:{digest}"
    assert store.resolve("project-a", record["ref"]).read_bytes() == content
    with pytest.raises(FileAssetError, match="未登记"):
        store.resolve("project-b", record["ref"])


def test_file_asset_rejects_paths_and_hash_mismatch(tmp_path) -> None:
    store = FileAssetStore(tmp_path / "assets")
    with pytest.raises(FileAssetError, match="路径"):
        store.register("project-a", "../secret.txt", b"x", hashlib.sha256(b"x").hexdigest())
    with pytest.raises(FileAssetError, match="不一致"):
        store.register("project-a", "safe.txt", b"x", "0" * 64)
