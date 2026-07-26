"""Spawned Runner process, IPC bridge, and Windows process-tree limits."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, replace
from datetime import datetime
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..domain.models import TestPlan
from .runner import RunnerConfig, run_plan


Runner = Callable[[TestPlan, RunnerConfig], tuple[object, Path]]


def isolated_worker(
    plan_payload: dict,
    config: RunnerConfig,
    cancel_event,
    connection: Connection,
    start_gate,
    runner: Runner = run_plan,
) -> None:
    """Child entry point. Configuration and secrets travel through spawn IPC only."""
    run_dir = Path(config.artifacts_root).resolve() / str(config.run_id)
    temp_dir = run_dir / "_runner_tmp"
    run_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(run_dir)
    os.environ["TMP"] = str(temp_dir)
    os.environ["TEMP"] = str(temp_dir)
    _sanitize_environment(config, plan_payload, temp_dir)
    start_gate.wait(15)
    plan = TestPlan.model_validate(plan_payload)

    def progress(payload: dict) -> None:
        connection.send({"type": "progress", "payload": payload})

    def confirm(step, index: int, rule: str) -> bool:
        confirmation_id = f"confirmation-{uuid4().hex[:12]}"
        requested = {
            "id": confirmation_id,
            "step_index": index,
            "action": step.action.value,
            "target": step.description
            or (step.locator.describe() if step.locator else step.target)
            or step.action.value,
            "rule": rule,
            "requested_at": datetime.now().astimezone().isoformat(),
        }
        connection.send({"type": "confirmation_requested", "payload": requested})
        decision = "rejected"
        actor = "cancel_request"
        while not cancel_event.is_set():
            if not connection.poll(0.25):
                continue
            message = connection.recv()
            if message.get("type") != "confirmation_decision":
                continue
            if message.get("id") != confirmation_id:
                continue
            decision = str(message.get("decision", "rejected"))
            actor = str(message.get("actor", "local_user"))
            break
        resolved = {
            **requested,
            "decision": decision,
            "actor": actor,
            "decided_at": datetime.now().astimezone().isoformat(),
        }
        config.confirmation_history.append(resolved)
        connection.send({
            "type": "confirmation_resolved",
            "payload": resolved,
        })
        return decision == "approved"

    def clarify(question: str, round_number: int) -> str | None:
        clarification_id = f"clarification-{uuid4().hex[:12]}"
        requested = {
            "id": clarification_id,
            "round": round_number,
            "question": question,
            "requested_at": datetime.now().astimezone().isoformat(),
        }
        connection.send({"type": "clarification_requested", "payload": requested})
        answer = None
        actor = "cancel_request"
        while not cancel_event.is_set():
            if not connection.poll(0.25):
                continue
            message = connection.recv()
            if message.get("type") != "clarification_answer" or message.get("id") != clarification_id:
                continue
            answer = message.get("answer")
            actor = str(message.get("actor", "local_user"))
            break
        connection.send({
            "type": "clarification_resolved",
            "payload": {
                **requested,
                "answer": answer,
                "actor": actor,
                "answered_at": datetime.now().astimezone().isoformat(),
            },
        })
        return str(answer) if answer else None

    try:
        effective = replace(
            config,
            cancel_event=cancel_event,
            progress_callback=progress,
            confirmation_callback=confirm,
            clarification_callback=clarify,
        )
        result, _ = runner(plan, effective)
        connection.send({"type": "result", "payload": result.model_dump(mode="json")})
    except BaseException as exc:
        connection.send({
            "type": "error",
            "error": str(exc),
            "errorType": type(exc).__name__,
        })
    finally:
        connection.close()


def _sanitize_environment(config: RunnerConfig, plan_payload: dict, temp_dir: Path) -> None:
    required = {
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC",
        "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
        "USERPROFILE", "USERNAME", "USERDOMAIN", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    }
    required.update(target.upper() for _, target in config.secret_refs)
    secret_targets = dict(config.secret_refs)
    for step in plan_payload.get("steps", []):
        secret_name = step.get("value_from_secret")
        if secret_name:
            required.add(secret_targets.get(secret_name, secret_name).upper())
    pending = [plan_payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            required.add(value[2:-1].upper())
    required.update(key.upper() for key in os.environ if key.upper().startswith("PLAYWRIGHT_"))
    retained = {key: value for key, value in os.environ.items() if key.upper() in required}
    os.environ.clear()
    os.environ.update(retained)
    os.environ["TMP"] = str(temp_dir)
    os.environ["TEMP"] = str(temp_dir)


@dataclass
class WindowsJob:
    handle: int | None
    assigned: bool

    @classmethod
    def assign(cls, process_id: int, memory_limit_mb: int) -> "WindowsJob":
        if os.name != "nt":
            return cls(None, False)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return cls(None, False)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x0200 | 0x0400
        limits.JobMemoryLimit = memory_limit_mb * 1024 * 1024
        configured = kernel32.SetInformationJobObject(
            ctypes.c_void_p(handle), 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        process = kernel32.OpenProcess(0x0100 | 0x0001 | 0x1000, False, process_id)
        assigned = bool(
            configured
            and process
            and kernel32.AssignProcessToJobObject(ctypes.c_void_p(handle), ctypes.c_void_p(process))
        )
        if process:
            kernel32.CloseHandle(ctypes.c_void_p(process))
        if not assigned:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return cls(None, False)
        return cls(int(handle), True)

    def terminate(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.WinDLL("kernel32", use_last_error=True).TerminateJobObject(
                ctypes.c_void_p(self.handle), 1
            )

    def close(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                ctypes.c_void_p(self.handle)
            )
            self.handle = None
