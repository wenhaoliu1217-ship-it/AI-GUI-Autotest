import hashlib

import pytest
from playwright.sync_api import sync_playwright

from gui_agent.artifacts import ArtifactManager
from gui_agent.domain.models import Step
from gui_agent.execution.runner import _execute_step
from gui_agent.security.policy import DomainPolicy
from gui_agent.security.redaction import Redactor


@pytest.mark.e2e
def test_upload_and_download_capture_sha256(tmp_path) -> None:
    upload = tmp_path / "fixed.txt"
    upload.write_bytes(b"fixed upload")
    upload_sha = hashlib.sha256(upload.read_bytes()).hexdigest()
    artifacts = ArtifactManager(tmp_path / "artifacts", "file-run", Redactor(), ())
    policy = DomainPolicy("https://example.com")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_content('''
          <input id="upload" type="file">
          <a id="download" download="result.txt" href="data:text/plain,fixed%20download">Download</a>
        ''')
        upload_result = _execute_step(
            page,
            Step(action="upload_file", locator={"css": "#upload"}, fileAssetRef=f"asset:{upload_sha}"),
            "https://example.com", policy, Redactor(),
            file_assets={f"asset:{upload_sha}": str(upload)}, artifacts=artifacts,
        )
        assert page.locator("#upload").evaluate("element => element.files[0].name") == "fixed.txt"
        assert upload_result["fileEvidence"]["sha256"] == upload_sha

        expected = hashlib.sha256(b"fixed download").hexdigest()
        download_result = _execute_step(
            page,
            Step(action="download", locator={"css": "#download"}, expectedDownloadSha256=expected),
            "https://example.com", policy, Redactor(), artifacts=artifacts,
        )
        evidence = download_result["fileEvidence"]
        assert evidence["sha256"] == expected
        assert (artifacts.run_dir / evidence["artifact"]).read_bytes() == b"fixed download"
        browser.close()
