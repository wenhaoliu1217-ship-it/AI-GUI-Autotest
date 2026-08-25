"""Project-manifest uploads and run-scoped downloads."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from ..artifacts import ArtifactManager
from ..domain.models import Step
from ..locating.strategies import resolve_action_locator, resolve_locator
from ..security.policy import SecurityError


class FileTransferError(PlaywrightError):
    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def execute_upload(page, step: Step, files: tuple[dict, ...], artifacts: ArtifactManager, timeout_ms: int) -> dict:
    record = next((item for item in files if item.get("id") == step.file_id), None)
    if record is None:
        raise SecurityError(f"测试文件未登记或未授权给本次运行：{step.file_id}")
    if record.get("validationStatus") != step.expected_file_validity:
        raise SecurityError(
            f"测试文件有效性与计划不一致：期望 {step.expected_file_validity}，实际 {record.get('validationStatus')}"
        )
    source = Path(str(record.get("path", ""))).resolve()
    if not source.is_file():
        raise SecurityError(f"测试文件暂存不存在：{step.file_id}")
    evidence = {
        "direction": "upload", "fileId": record["id"], "fileName": record["fileName"],
        "size": record["size"], "sha256": record["sha256"], "mimeType": record["mimeType"],
        "extension": record["extension"], "validationProfile": record["validationProfile"],
        "validationStatus": record["validationStatus"], "validationErrors": record.get("validationErrors", []),
        "expectedResult": record.get("expectedResult"), "businessObjectName": step.business_object_name,
        "networkRequests": [], "networkResponses": [], "generatedBusinessId": None,
        "residualObjectCheck": "not_requested", "contentExposedToModel": False,
    }

    def request_listener(request) -> None:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            evidence["networkRequests"].append({
                "method": request.method, "url": _safe_url(request.url),
                "resourceType": request.resource_type,
                "contentLength": request.headers.get("content-length"),
            })

    def response_listener(response) -> None:
        if response.request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        headers = _safe_headers(response.headers)
        item = {"method": response.request.method, "url": _safe_url(response.url), "status": response.status, "headers": headers}
        evidence["networkResponses"].append(item)
        declared_length = headers.get("content-length")
        if evidence["generatedBusinessId"] is None and declared_length and int(declared_length) <= 1_000_000:
            try:
                evidence["generatedBusinessId"] = _extract_business_id(response.json())
            except Exception:
                pass

    page.on("request", request_listener)
    page.on("response", response_listener)
    try:
        resolve_action_locator(page, step.locator).set_input_files(str(source))  # type: ignore[arg-type]
        if evidence["networkRequests"] and not evidence["networkResponses"]:
            try:
                page.wait_for_event(
                    "response",
                    predicate=lambda response: response.request.method not in {"GET", "HEAD", "OPTIONS"},
                    timeout=min(timeout_ms, 5_000),
                )
            except PlaywrightTimeoutError:
                evidence["responseObservation"] = "timeout"
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3_000))
        except PlaywrightTimeoutError:
            evidence["networkSettlement"] = "timeout"
        else:
            evidence["networkSettlement"] = "idle"
        if step.residual_object_locator is not None:
            actual_count = resolve_locator(page, step.residual_object_locator).count()
            evidence["residualObjectCheck"] = {
                "status": "verified" if actual_count == step.expected_residual_count else "failed",
                "locator": step.residual_object_locator.describe(),
                "expectedCount": step.expected_residual_count,
                "actualCount": actual_count,
            }
            if actual_count != step.expected_residual_count:
                raise PlaywrightError(
                    f"上传后残留对象数量不符合预期：期望 {step.expected_residual_count}，实际 {actual_count}"
                )
    except Exception as exc:
        evidence.update({"status": "failed", "error": str(exc)})
        artifacts.event("file_upload_failed", **evidence)
        raise FileTransferError(str(exc), evidence) from exc
    finally:
        page.remove_listener("request", request_listener)
        page.remove_listener("response", response_listener)
    evidence["status"] = "completed"
    artifacts.event("file_upload_completed", **evidence)
    return evidence


def execute_download(page, step: Step, artifacts: ArtifactManager, timeout_ms: int) -> dict:
    expectation = step.download_validation
    assert expectation is not None
    evidence: dict[str, Any] = {
        "direction": "download", "businessObjectName": step.business_object_name,
        "expected": expectation.model_dump(mode="json", by_alias=True),
        "responseHeaders": {}, "contentExposedToModel": False,
    }
    responses: list[Any] = []
    def response_listener(response) -> None:
        responses.append(response)
    page.on("response", response_listener)
    cdp_downloads: list[dict[str, Any]] = []
    cdp = page.context.new_cdp_session(page)
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})
    def paused_listener(event: dict[str, Any]) -> None:
        cdp_downloads.append(event)
        cdp.send("Fetch.continueResponse", {"requestId": event["requestId"]})
    cdp.on("Fetch.requestPaused", paused_listener)
    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            resolve_action_locator(page, step.locator).click()  # type: ignore[arg-type]
        download = download_info.value
        target, relative = artifacts.download_path(download.suggested_filename)
        download.save_as(str(target))
        failure = download.failure()
        if failure:
            raise PlaywrightError(f"下载失败：{failure}")
        size = target.stat().st_size
        sha256 = _sha256(target)
        matched = next((item for item in reversed(responses) if item.url == download.url), None)
        cdp_match = next((item for item in reversed(cdp_downloads) if item.get("request", {}).get("url") == download.url), None)
        cdp_headers = {
            str(item.get("name", "")): str(item.get("value", ""))
            for item in (cdp_match.get("responseHeaders", []) if cdp_match else [])
        }
        response_headers = (
            matched.headers if matched else
            cdp_headers
        )
        response_status = (
            matched.status if matched else
            cdp_match.get("responseStatusCode") if cdp_match else None
        )
        evidence.update({
            "status": "completed", "url": _safe_url(download.url),
            "fileName": download.suggested_filename, "artifact": relative.replace("\\", "/"),
            "size": size, "sha256": sha256,
            "responseStatus": response_status,
            "responseHeaders": _safe_headers(response_headers),
            "formatValidation": _validate_download(target, expectation.format, expectation.required_json_keys),
        })
        errors: list[str] = []
        if expectation.extension and target.suffix.lower() != expectation.extension.lower():
            errors.append(f"扩展名应为 {expectation.extension}，实际 {target.suffix.lower()}")
        if expectation.filename_pattern and not re.fullmatch(expectation.filename_pattern, download.suggested_filename):
            errors.append("文件名不符合预期模式")
        if size < expectation.minimum_size:
            errors.append(f"文件大小小于 {expectation.minimum_size}")
        if expectation.sha256 and sha256.lower() != expectation.sha256.lower():
            errors.append("SHA-256 与预期不一致")
        errors.extend(evidence["formatValidation"].get("errors", []))
        evidence["validationErrors"] = errors
        if errors:
            evidence["status"] = "validation_failed"
            raise FileTransferError("下载内容校验失败：" + "；".join(errors), evidence)
        artifacts.event("file_download_completed", **evidence)
        return evidence
    except FileTransferError:
        artifacts.event("file_download_failed", **evidence)
        raise
    except Exception as exc:
        evidence.update({"status": "failed", "error": str(exc)})
        artifacts.event("file_download_failed", **evidence)
        raise FileTransferError(str(exc), evidence) from exc
    finally:
        page.remove_listener("response", response_listener)
        try:
            cdp.send("Fetch.disable")
        except Exception:
            pass
        cdp.detach()


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {"content-type", "content-length", "content-disposition", "etag", "last-modified"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in allowed}


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _extract_business_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("id", "objectId", "businessId", "modelId", "assetId"):
        value = payload.get(key)
        if isinstance(value, (str, int)):
            return str(value)
    data = payload.get("data")
    return _extract_business_id(data) if isinstance(data, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_download(path: Path, format_name: str, required_keys: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    details: dict[str, Any] = {"format": format_name, "errors": errors}
    try:
        if format_name == "json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            details["topLevelType"] = type(payload).__name__
            if required_keys and (not isinstance(payload, dict) or any(key not in payload for key in required_keys)):
                errors.append("JSON 缺少必需字段")
        elif format_name == "zip":
            with zipfile.ZipFile(path) as archive:
                details["entries"] = len(archive.namelist())
                if archive.testzip(): errors.append("ZIP CRC 校验失败")
        elif format_name == "csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                header = next(csv.reader(stream), None)
            details["columns"] = header or []
            if not header: errors.append("CSV 不能为空")
        elif format_name == "text":
            path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        errors.append(f"{format_name} 格式校验失败：{exc}")
    return details
