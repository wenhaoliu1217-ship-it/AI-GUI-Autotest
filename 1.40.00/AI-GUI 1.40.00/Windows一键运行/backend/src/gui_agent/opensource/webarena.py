"""WebArena self-hosted configuration and trajectory ingestion boundary.

The product does not start WebArena sites or execute WebArena actions here.
This module validates a user-owned self-hosted site/task configuration and
binds an archived trajectory to that configuration for read-only evaluation.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .adapters import normalize_webarena_trajectory


class WebArenaConfigError(ValueError):
    """A self-hosted WebArena configuration is unsafe or incomplete."""


WEBARENA_SITE_TOKENS: dict[str, tuple[str, str]] = {
    "__SHOPPING__": ("shopping", "Shopping"),
    "__SHOPPING_ADMIN__": ("shopping_admin", "Shopping Admin"),
    "__REDDIT__": ("reddit", "Reddit"),
    "__GITLAB__": ("gitlab", "GitLab"),
    "__WIKIPEDIA__": ("wikipedia", "Wikipedia"),
    "__MAP__": ("map", "Map"),
    "__HOME__": ("homepage", "Home"),
    "__HOMEPAGE__": ("homepage", "Home"),
}

WEBARENA_SITE_DEFAULT_PORTS: dict[str, int] = {
    "shopping": 7770,
    "shopping_admin": 7780,
    "reddit": 9999,
    "gitlab": 8023,
    "wikipedia": 8888,
    "map": 3000,
    "homepage": 4399,
}


def _site_key(value: Any, field: str) -> tuple[str, str | None]:
    """Map an original WebArena token/name to a bounded local site id."""

    candidate = _text(value, field, 160)
    token = candidate.upper()
    if token in WEBARENA_SITE_TOKENS:
        site_id, _ = WEBARENA_SITE_TOKENS[token]
        return site_id, token
    normalized = candidate.lower().strip("_ ").replace("-", "_")
    aliases = {
        "shopping_admin": "shopping_admin",
        "shoppingadmin": "shopping_admin",
        "home": "homepage",
        "homepage": "homepage",
    }
    site_id = aliases.get(normalized, normalized)
    if site_id not in WEBARENA_SITE_DEFAULT_PORTS:
        raise WebArenaConfigError(f"{field} references an unsupported WebArena site: {candidate}")
    return site_id, None


def _safe_eval_facts(record: dict[str, Any]) -> dict[str, Any]:
    """Keep evaluator metadata while dropping answers, URLs, and HTML bodies."""

    raw_eval = record.get("eval")
    evaluator = raw_eval if isinstance(raw_eval, dict) else {}
    raw_types = evaluator.get("eval_types") or evaluator.get("evalTypes") or []
    if not isinstance(raw_types, list):
        raw_types = [raw_types]
    eval_types = [str(item).strip()[:80] for item in raw_types if isinstance(item, (str, int)) and str(item).strip()]
    eval_types = list(dict.fromkeys(eval_types))[:12]
    reference_answer = evaluator.get("reference_answer", evaluator.get("reference_answers"))
    reference_url = evaluator.get("reference_url", evaluator.get("reference_urls"))
    program_html = evaluator.get("program_html")
    if isinstance(program_html, list):
        program_html_count = min(len(program_html), 200)
    else:
        program_html_count = 1 if program_html else 0
    return {
        "evalTypes": eval_types,
        "referenceAnswerPresent": reference_answer not in (None, "", [], {}),
        "referenceUrlPresent": reference_url not in (None, "", [], {}),
        "programHtmlCount": program_html_count,
    }


def _imported_task(record: Any, index: int, known_site_ids: set[str]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    if not isinstance(record, dict):
        raise WebArenaConfigError(f"records[{index}] must be an object")
    raw_id = record.get("task_id", record.get("taskId", record.get("id")))
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
        raise WebArenaConfigError(f"records[{index}].task_id must be a string or integer")
    source_id = _text(str(raw_id), f"records[{index}].task_id", 120)
    task_id = source_id if source_id.startswith("webarena.") else f"webarena.{source_id}"
    raw_sites = record.get("sites") or []
    if not isinstance(raw_sites, list) or not raw_sites:
        raise WebArenaConfigError(f"records[{index}].sites must contain at least one site")
    site_ids: list[str] = []
    tokens: list[str] = []
    for site_index, raw_site in enumerate(raw_sites[:8]):
        site_id, token = _site_key(raw_site, f"records[{index}].sites[{site_index}]")
        if site_id not in known_site_ids:
            raise WebArenaConfigError(f"records[{index}] references an unconfigured site: {site_id}")
        if site_id not in site_ids:
            site_ids.append(site_id)
        if token:
            tokens.append(token)
    raw_start_url = record.get("start_url", record.get("startUrl"))
    start_token: str | None = None
    if isinstance(raw_start_url, str) and raw_start_url.strip().upper() in WEBARENA_SITE_TOKENS:
        start_token = raw_start_url.strip().upper()
        start_site, _ = _site_key(start_token, f"records[{index}].start_url")
        if start_site not in site_ids:
            site_ids.insert(0, start_site)
    site_id = start_site if start_token else site_ids[0]
    intent = record.get("intent", record.get("goal", record.get("instruction")))
    intent = _text(intent, f"records[{index}].intent", 4_000)
    requires_login = bool(record.get("require_login", record.get("requiresLogin", False)))
    has_storage_state = bool(record.get("storage_state") or record.get("storageState"))
    evaluation = _safe_eval_facts(record)
    imported = {
        "id": task_id,
        "sourceTaskId": source_id,
        "siteId": site_id,
        "siteIds": site_ids,
        "intent": intent,
        "startUrlToken": start_token,
        "requiresLogin": requires_login,
        "evaluation": evaluation,
        "actionPolicy": "trajectory_evaluation_only",
    }
    config_task = {
        "id": task_id,
        "siteId": site_id,
        "intent": intent,
        "evaluator": "webarena_external_evaluator",
    }
    return imported, config_task, has_storage_state


def import_webarena_task_config(
    payload: Any,
    site_base_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert WebArena's original task JSON into the product's safe local contract.

    This is deliberately an import-only boundary. It never reads storage-state
    files, keeps reference answers/URLs, starts a site, or executes a task.
    """

    if isinstance(payload, dict):
        records = payload.get("tasks", payload.get("records"))
    else:
        records = payload
    if not isinstance(records, list) or not records:
        raise WebArenaConfigError("WebArena import requires a non-empty tasks/records list")
    if len(records) > 2_000:
        raise WebArenaConfigError("WebArena import is limited to 2,000 source records")
    configured_urls = site_base_urls or {}
    if not isinstance(configured_urls, dict):
        raise WebArenaConfigError("siteBaseUrls must be an object")
    normalized_urls: dict[str, str] = {}
    for raw_key, raw_url in configured_urls.items():
        site_id, _ = _site_key(raw_key, "siteBaseUrls key")
        normalized_urls[site_id] = _text(raw_url, f"siteBaseUrls.{raw_key}", 2_000)
    used_site_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise WebArenaConfigError(f"records[{index}] must be an object")
        for raw_site in (record.get("sites") or [])[:8]:
            site_id, _ = _site_key(raw_site, f"records[{index}].sites")
            used_site_ids.add(site_id)
    if not used_site_ids:
        raise WebArenaConfigError("WebArena records do not reference any supported site")
    site_catalog: list[dict[str, Any]] = []
    site_names = {site_id: name for _, (site_id, name) in WEBARENA_SITE_TOKENS.items()}
    for site_id in sorted(used_site_ids):
        base_url = normalized_urls.get(site_id, f"http://127.0.0.1:{WEBARENA_SITE_DEFAULT_PORTS[site_id]}")
        site_catalog.append({
            "id": site_id,
            "name": site_names.get(site_id, site_id),
            "baseUrl": base_url,
            "healthPath": "/",
        })
    known_site_ids = {site["id"] for site in site_catalog}
    source_count = len(records)
    imported_tasks: list[dict[str, Any]] = []
    config_tasks: list[dict[str, Any]] = []
    secrets_dropped = False
    seen_ids: set[str] = set()
    for index, record in enumerate(records[:200]):
        imported, config_task, has_storage_state = _imported_task(record, index, known_site_ids)
        if imported["id"] in seen_ids:
            raise WebArenaConfigError(f"duplicate imported task id: {imported['id']}")
        seen_ids.add(imported["id"])
        imported_tasks.append(imported)
        config_tasks.append(config_task)
        secrets_dropped = secrets_dropped or has_storage_state
    configuration = validate_webarena_self_hosted_config({
        "readOnly": True,
        "allowExternalWrites": False,
        "sites": site_catalog,
        "tasks": config_tasks,
    })
    return {
        "adapter": "webarena",
        "status": "config_imported",
        "source": "webarena_config_files",
        "sourceTaskCount": source_count,
        "importedTaskCount": len(imported_tasks),
        "truncated": source_count > len(imported_tasks),
        "secretsDropped": secrets_dropped,
        "siteCatalog": configuration["sites"],
        "tasks": imported_tasks,
        "configuration": configuration,
        "runtimeStatus": configuration["runtimeStatus"],
        "runtimeReady": False,
        "actionPolicy": "trajectory_evaluation_only",
        "executionPolicy": "no_browser_start_no_external_writes",
    }


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebArenaConfigError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise WebArenaConfigError(f"{field} exceeds the maximum length")
    return value


def _host(value: Any, field: str) -> str:
    candidate = _text(value, field, 253).lower().rstrip(".")
    if "/" in candidate or "://" in candidate or "@" in candidate:
        raise WebArenaConfigError(f"{field} must be a host name, not a URL or credential")
    return candidate


def _site(site: Any, index: int) -> dict[str, Any]:
    if not isinstance(site, dict):
        raise WebArenaConfigError(f"sites[{index}] must be an object")
    site_id = _text(site.get("id"), f"sites[{index}].id", 120)
    name = _text(site.get("name") or site_id, f"sites[{index}].name", 240)
    base_url = _text(site.get("baseUrl") or site.get("base_url"), f"sites[{index}].baseUrl", 2_000)
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebArenaConfigError(f"sites[{index}].baseUrl must be an absolute http/https URL")
    if parsed.username or parsed.password:
        raise WebArenaConfigError(f"sites[{index}].baseUrl must not contain credentials")
    if parsed.query or parsed.fragment:
        raise WebArenaConfigError(f"sites[{index}].baseUrl must not contain query or fragment data")
    allowed_hosts = site.get("allowedHosts") or site.get("allowed_hosts") or [parsed.hostname]
    if not isinstance(allowed_hosts, list) or not allowed_hosts or len(allowed_hosts) > 12:
        raise WebArenaConfigError(f"sites[{index}].allowedHosts must contain 1 to 12 hosts")
    normalized_hosts = list(dict.fromkeys(_host(item, f"sites[{index}].allowedHosts") for item in allowed_hosts))
    if parsed.hostname.lower().rstrip(".") not in normalized_hosts:
        raise WebArenaConfigError(f"sites[{index}].allowedHosts must include the base URL host")
    health_path = site.get("healthPath") or site.get("health_path") or "/"
    health_path = _text(health_path, f"sites[{index}].healthPath", 500)
    if not health_path.startswith("/") or health_path.startswith("//"):
        raise WebArenaConfigError(f"sites[{index}].healthPath must be a local path")
    return {
        "id": site_id,
        "name": name,
        "baseUrl": base_url.rstrip("/") or base_url,
        "healthPath": health_path,
        "allowedHosts": normalized_hosts,
    }


def validate_webarena_self_hosted_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a user-owned WebArena site/task map without network access."""

    if not isinstance(payload, dict):
        raise WebArenaConfigError("configuration must be an object")
    if payload.get("readOnly") is not True:
        raise WebArenaConfigError("WebArena evaluation requires readOnly=true")
    if payload.get("allowExternalWrites", False) is not False:
        raise WebArenaConfigError("external writes are disabled for the WebArena boundary")
    raw_sites = payload.get("sites")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_sites, list) or not 1 <= len(raw_sites) <= 8:
        raise WebArenaConfigError("sites must contain 1 to 8 self-hosted sites")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 200:
        raise WebArenaConfigError("tasks must contain 1 to 200 tasks")
    sites = [_site(item, index) for index, item in enumerate(raw_sites)]
    site_ids = [item["id"] for item in sites]
    if len(site_ids) != len(set(site_ids)):
        raise WebArenaConfigError("site ids must be unique")
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            raise WebArenaConfigError(f"tasks[{index}] must be an object")
        task_id = _text(task.get("id") or task.get("taskId") or task.get("task_id"), f"tasks[{index}].id", 160)
        if task_id in task_ids:
            raise WebArenaConfigError("task ids must be unique")
        task_ids.add(task_id)
        site_id = _text(task.get("siteId") or task.get("site_id"), f"tasks[{index}].siteId", 120)
        if site_id not in site_ids:
            raise WebArenaConfigError(f"tasks[{index}].siteId references an unknown site")
        intent = _text(task.get("intent") or task.get("goal") or task.get("instruction"), f"tasks[{index}].intent", 4_000)
        evaluator = task.get("evaluator") or task.get("evaluatorId") or "external_evaluator"
        tasks.append({
            "id": task_id,
            "siteId": site_id,
            "intent": intent,
            "evaluator": _text(evaluator, f"tasks[{index}].evaluator", 240),
        })
    return {
        "provider": "webarena",
        "status": "config_ready",
        "readOnly": True,
        "allowExternalWrites": False,
        "runtimeStatus": "self_hosted_sites_required",
        "runtimeReady": False,
        "siteCount": len(sites),
        "taskCount": len(tasks),
        "sites": sites,
        "tasks": tasks,
        "actionPolicy": "trajectory_evaluation_only",
        "executionPolicy": "no_browser_start_no_external_writes",
    }


def probe_webarena_sites(config: dict[str, Any], timeout_ms: int = 1_500) -> dict[str, Any]:
    """Probe configured self-hosted sites with bounded GET-only health requests.

    This checks site reachability before an upstream runner is considered. It
    never follows redirects, submits forms, starts a browser, or changes the
    WebArena runtime readiness flag.
    """

    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 200 <= timeout_ms <= 5_000:
        raise WebArenaConfigError("timeoutMs must be an integer from 200 to 5000")
    validated = validate_webarena_self_hosted_config(config)
    results: list[dict[str, Any]] = []
    with httpx.Client(
        follow_redirects=False,
        timeout=timeout_ms / 1_000,
        headers={"User-Agent": "JingcaiOPC-WebArena-ReadOnlyProbe/1.33"},
    ) as client:
        for site in validated["sites"]:
            probe_url = f"{site['baseUrl'].rstrip('/')}{site['healthPath']}"
            started = time.perf_counter()
            item: dict[str, Any] = {
                "id": site["id"],
                "url": probe_url,
                "allowedHosts": site["allowedHosts"],
                "method": "GET",
                "readOnly": True,
            }
            try:
                with client.stream("GET", probe_url) as response:
                    status_code = response.status_code
                    location = response.headers.get("location")
                    redirect_host_allowed: bool | None = None
                    if location:
                        redirect_host = urlparse(urljoin(probe_url, location)).hostname
                        redirect_host_allowed = bool(
                            redirect_host
                            and redirect_host.lower().rstrip(".") in site["allowedHosts"]
                        )
                    item["httpStatus"] = status_code
                    item["redirectHostAllowed"] = redirect_host_allowed
                    if 200 <= status_code < 300:
                        item["status"] = "healthy"
                        item["ready"] = True
                    elif 300 <= status_code < 400 and redirect_host_allowed is False:
                        item["status"] = "redirect_blocked"
                        item["ready"] = False
                    else:
                        item["status"] = "http_error"
                        item["ready"] = False
            except httpx.TimeoutException:
                item["status"] = "timeout"
                item["ready"] = False
            except httpx.ConnectError:
                item["status"] = "unreachable"
                item["ready"] = False
            except httpx.HTTPError:
                item["status"] = "probe_error"
                item["ready"] = False
            item["durationMs"] = round((time.perf_counter() - started) * 1_000, 1)
            results.append(item)
    ready_count = sum(1 for item in results if item["ready"])
    all_ready = ready_count == len(results)
    return {
        "provider": "webarena",
        "status": "site_probe_complete",
        "siteCount": len(results),
        "readySiteCount": ready_count,
        "failedSiteCount": len(results) - ready_count,
        "siteReadiness": "sites_ready" if all_ready else "sites_not_ready",
        "runtimeStatus": "self_hosted_sites_ready_runtime_pending" if all_ready else "self_hosted_sites_not_ready",
        "runtimeReady": False,
        "sites": results,
        "timeoutMs": timeout_ms,
        "actionPolicy": "trajectory_evaluation_only",
        "executionPolicy": "get_only_no_browser_start_no_external_writes",
    }


def ingest_webarena_trajectory(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Bind an archived trajectory to a validated site/task configuration."""

    validated = validate_webarena_self_hosted_config(config)
    normalized = normalize_webarena_trajectory(payload)
    task_id = normalized.get("task", {}).get("id")
    task = next((item for item in validated["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise WebArenaConfigError("trajectory task id is not present in the self-hosted configuration")
    result = dict(normalized)
    result["configuration"] = {
        "status": validated["status"],
        "siteCount": validated["siteCount"],
        "taskCount": validated["taskCount"],
        "task": task,
    }
    result["runtimeStatus"] = validated["runtimeStatus"]
    result["runtimeReady"] = False
    result["actionPolicy"] = "trajectory_evaluation_only"
    return result
