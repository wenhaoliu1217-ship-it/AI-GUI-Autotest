"""通过用户临时提供的模型配置生成受约束 TestPlan。

密钥只存在于单次 HTTP 请求对象中：不落盘、不缓存、不进入日志或测试报告。
模型输出必须再次通过 Pydantic TestPlan 校验，不能直接交给执行器。
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr, ValidationError

from ..domain.models import TestPlan


Protocol = Literal["responses", "chat_completions"]


class AIProviderError(RuntimeError):
    """模型连接或输出不满足约束。"""


@dataclass(frozen=True)
class AISettings:
    protocol: Protocol
    base_url: str
    model: str
    api_key: SecretStr

    def validated(self) -> "AISettings":
        parsed = urlparse(self.base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AIProviderError("API Base URL 必须是完整的 http:// 或 https:// 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AIProviderError("API Base URL 不能包含账号、密码、查询参数或片段")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AIProviderError("非本机模型服务必须使用 HTTPS")
        if not self.model.strip():
            raise AIProviderError("请填写模型名称")
        if not self.api_key.get_secret_value().strip():
            raise AIProviderError("请填写新的 API Key")
        return self


@dataclass(frozen=True)
class AIPlanResult:
    plan: TestPlan
    model: str
    protocol: Protocol
    elapsed_ms: int


def test_connection(settings: AISettings) -> dict[str, Any]:
    settings = settings.validated()
    started = time.perf_counter()
    prompt = "只回复 OK。"
    data = _post(settings, prompt, schema=None, connection_test=True)
    text = _extract_text(settings.protocol, data)
    if not text.strip():
        raise AIProviderError("模型已响应，但没有返回文本")
    return {
        "connected": True,
        "model": settings.model.strip(),
        "protocol": settings.protocol,
        "elapsedMs": round((time.perf_counter() - started) * 1000),
    }


def plan_with_ai(
    *,
    settings: AISettings,
    name: str,
    target_url: str,
    flow: str,
    role: str | None,
    preconditions: str | None,
    expectation: str | None,
) -> AIPlanResult:
    settings = settings.validated()
    started = time.perf_counter()
    schema = _strict_schema(TestPlan.model_json_schema())
    prompt = _planning_prompt(
        name=name,
        target_url=target_url,
        flow=flow,
        role=role,
        preconditions=preconditions,
        expectation=expectation,
        schema=schema,
    )
    data = _post(settings, prompt, schema=schema)
    text = _extract_text(settings.protocol, data)
    raw = _parse_json_object(text)
    # 这些字段属于用户输入，不允许模型改写测试目标或身份说明。
    raw["name"] = name.strip() or "未命名测试"
    raw["base_url"] = target_url.strip()
    raw["role"] = role.strip() if role and role.strip() else None
    if preconditions and preconditions.strip():
        raw["preconditions"] = [{"description": preconditions.strip()}]
    else:
        raw["preconditions"] = []
    try:
        plan = TestPlan.model_validate(raw)
    except ValidationError as exc:
        raise AIProviderError(f"模型返回的测试计划未通过安全 Schema 校验：{_validation_summary(exc)}") from exc
    if not plan.assertions:
        raise AIProviderError("模型计划没有任何可验证断言，已拒绝执行")
    return AIPlanResult(
        plan=plan,
        model=settings.model.strip(),
        protocol=settings.protocol,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )


def _post(
    settings: AISettings,
    prompt: str,
    *,
    schema: dict[str, Any] | None,
    connection_test: bool = False,
) -> dict[str, Any]:
    base = settings.base_url.strip().rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    if settings.protocol == "responses":
        endpoint = f"{base}/responses"
        payload: dict[str, Any] = {
            "model": settings.model.strip(),
            "instructions": "你是 GUI 自动化测试规划器。严格遵守输出约束，不编造执行结果。",
            "input": prompt,
        }
        if schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "gui_test_plan",
                    "schema": schema,
                    "strict": True,
                }
            }
        elif connection_test:
            payload["max_output_tokens"] = 32
    else:
        endpoint = f"{base}/chat/completions"
        payload = {
            "model": settings.model.strip(),
            "messages": [
                {"role": "system", "content": "你是 GUI 自动化测试规划器。严格输出 JSON，不编造执行结果。"},
                {"role": "user", "content": prompt},
            ],
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        elif connection_test:
            payload["max_tokens"] = 16
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise AIProviderError("模型 API 连接超时，请检查地址、网络或服务状态") from exc
    except httpx.HTTPError as exc:
        raise AIProviderError("无法连接模型 API，请检查 Base URL 和网络") from exc
    if response.status_code >= 400:
        hint = {
            401: "API Key 无效或已撤销",
            403: "当前 Key 没有该模型或接口权限",
            404: "接口或模型不存在，请检查 Base URL、协议和模型名",
            429: "请求频率或账户额度受限",
        }.get(response.status_code, "模型服务返回错误")
        raise AIProviderError(f"{hint}（HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError as exc:
        raise AIProviderError("模型服务返回的不是 JSON") from exc


def _extract_text(protocol: Protocol, data: dict[str, Any]) -> str:
    if protocol == "chat_completions":
        try:
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, str):
                return content
        except (KeyError, IndexError, TypeError):
            pass
        raise AIProviderError("兼容接口响应中缺少 choices[0].message.content")

    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for output in data.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise AIProviderError("Responses API 响应中缺少 output_text")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIProviderError("模型没有返回有效 JSON 测试计划") from exc
    if not isinstance(value, dict):
        raise AIProviderError("模型返回内容必须是一个 JSON 对象")
    return value


def _planning_prompt(
    *,
    name: str,
    target_url: str,
    flow: str,
    role: str | None,
    preconditions: str | None,
    expectation: str | None,
    schema: dict[str, Any],
) -> str:
    request = {
        "name": name,
        "target_url": target_url,
        "flow": flow,
        "role": role,
        "preconditions": preconditions,
        "expectation": expectation,
    }
    return (
        "把下面的中文测试需求转换为可执行的 Playwright 测试计划。\n"
        "要求：第一步必须 navigate 到 /；只使用 schema 中允许的动作、定位器和断言；"
        "优先 label、role+name、test_id、text，最后才使用 CSS；不得输出测试成功/失败结论；"
        "不得猜测或输出 API Key、密码等秘密；至少生成一个断言；无法确定的内容不要虚构。\n\n"
        f"用户需求：{json.dumps(request, ensure_ascii=False)}\n\n"
        f"必须输出且只输出符合此 JSON Schema 的对象：{json.dumps(schema, ensure_ascii=False)}"
    )


def _validation_summary(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(item) for item in error.get("loc", [])) or "root"
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    return "；".join(parts)


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把 Pydantic Schema 收紧为 Structured Outputs 所要求的全字段 required。"""
    result = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result
