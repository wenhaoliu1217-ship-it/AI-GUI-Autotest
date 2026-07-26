"""Isolation-only concurrency and callback idempotency assurance engines."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: Literal["A", "B"]
    outcome: Literal["reserved", "rejected"]
    request_sha256: str = Field(alias="requestSha256", pattern=r"^[0-9a-f]{64}$")
    started_at: str = Field(alias="startedAt")
    ended_at: str = Field(alias="endedAt")


class InventoryRaceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_before: int = Field(alias="stockBefore", ge=0)
    stock_after: int = Field(alias="stockAfter", ge=0)
    attempts: list[InventoryAttempt] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_sessions(self):
        if {item.session for item in self.attempts} != {"A", "B"}:
            raise ValueError("库存并发验证必须包含 A、B 两个独立会话")
        return self


class CallbackObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["payment", "refund"]
    idempotency_key_sha256: str = Field(alias="idempotencyKeySha256", pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(alias="payloadSha256", pattern=r"^[0-9a-f]{64}$")
    result: Literal["applied", "duplicate_ignored", "rejected"]
    observed_at: str = Field(alias="observedAt")


class CommerceAssuranceError(RuntimeError):
    pass


def evaluate_inventory_race(evidence: InventoryRaceEvidence) -> dict:
    reserved = [item for item in evidence.attempts if item.outcome == "reserved"]
    expected_after = evidence.stock_before - len(reserved)
    no_oversell = (
        len(reserved) <= evidence.stock_before
        and evidence.stock_after == expected_after
        and evidence.stock_after >= 0
    )
    single_winner = evidence.stock_before != 1 or len(reserved) == 1
    if not no_oversell or not single_winner:
        raise CommerceAssuranceError("双会话库存屏障验证失败：检测到超卖、重复赢家或库存不守恒")
    return {
        "verified": True,
        "sessions": [item.session for item in evidence.attempts],
        "winnerCount": len(reserved),
        "stockBefore": evidence.stock_before,
        "stockAfter": evidence.stock_after,
        "noOversell": True,
    }


def run_two_session_inventory_barrier(
    action_a: Callable[[], InventoryAttempt],
    action_b: Callable[[], InventoryAttempt],
    *,
    stock_before: int,
    stock_after: Callable[[], int],
) -> tuple[InventoryRaceEvidence, dict]:
    gate = Barrier(3)

    def run(action):
        gate.wait()
        return action()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="inventory-race") as pool:
        futures = [pool.submit(run, action_a), pool.submit(run, action_b)]
        gate.wait()
        attempts = [future.result() for future in futures]
    evidence = InventoryRaceEvidence(
        stockBefore=stock_before,
        stockAfter=stock_after(),
        attempts=attempts,
    )
    return evidence, evaluate_inventory_race(evidence)


def evaluate_callback_idempotency(observations: list[CallbackObservation]) -> dict:
    if len(observations) < 2:
        raise CommerceAssuranceError("回调幂等验证至少需要原始回调和一次重复回调")
    keys = {item.idempotency_key_sha256 for item in observations}
    payloads = {item.payload_sha256 for item in observations}
    applied = [item for item in observations if item.result == "applied"]
    duplicates = [item for item in observations if item.result == "duplicate_ignored"]
    if len(keys) != 1 or len(payloads) != 1:
        raise CommerceAssuranceError("重复回调的幂等键或载荷哈希不一致")
    if len(applied) != 1 or len(duplicates) != len(observations) - 1:
        raise CommerceAssuranceError("回调产生重复记账或未明确忽略重复事件")
    return {
        "verified": True,
        "kind": observations[0].kind,
        "callbackCount": len(observations),
        "appliedCount": 1,
        "duplicateIgnoredCount": len(duplicates),
        "duplicateSideEffects": 0,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
