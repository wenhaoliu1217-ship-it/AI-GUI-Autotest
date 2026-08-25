"""Child-process runner for the optional BrowserGym and AgentLab probes."""

from __future__ import annotations

import json
import sys
from typing import Any


def _observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
    urls = observation.get("open_pages_urls") or []
    titles = observation.get("open_pages_titles") or []
    return {
        "url": str(observation.get("url") or "")[:2_000],
        "openPageCount": len(urls) if isinstance(urls, list) else 0,
        "openPageTitles": [str(item)[:500] for item in titles[:10]] if isinstance(titles, list) else [],
        # These marker keys let the product normalizer retain presence facts
        # without serializing the screenshot, DOM, or accessibility payload.
        "screenshot": "screenshot" in observation,
        "axtree_object": "axtree_object" in observation,
        "dom_object": "dom_object" in observation,
        "lastActionError": str(observation.get("last_action_error") or "")[:800],
    }


def _run_browsergym() -> dict[str, Any]:
    from browsergym.core.env import BrowserEnv
    from browsergym.core.task import AbstractBrowserTask

    class LocalContractTask(AbstractBrowserTask):
        @classmethod
        def get_task_id(cls):
            return "jingcai-local-contract"

        def __init__(self, seed: int):
            super().__init__(seed)
            self.viewport = {"width": 1000, "height": 700}
            self.slow_mo = 0
            self.timeout = 3_000

        def setup(self, page):
            page.set_content(
                """
                <main><h1>Jingcai BrowserGym Contract</h1>
                <p id="status">ready</p>
                <button id="complete" data-testid="complete">完成本地校验</button>
                <output id="result" aria-label="结果">等待</output>
                <script>
                document.querySelector('#complete').addEventListener('click', () => {
                  document.querySelector('#status').textContent = 'done';
                  document.querySelector('#result').textContent = 'browsergym-ok';
                });
                </script></main>
                """
            )
            return "在本地隔离页面完成一次可验证的浏览器动作。", {"fixture": "browsergym-local-contract"}

        def validate(self, page, chat_messages):
            done = page.locator("#status").inner_text() == "done"
            return (
                1.0 if done else 0.0,
                done,
                "本地 BrowserGym 任务已完成。" if done else "",
                {
                    "status": page.locator("#status").inner_text(),
                    "result": page.locator("#result").inner_text(),
                },
            )

    env = BrowserEnv(task_entrypoint=LocalContractTask, headless=True, action_mapping=None, pre_observation_delay=0.05)
    try:
        reset_obs, reset_info = env.reset(seed=7)
        obs, reward, terminated, truncated, step_info = env.step("page.locator('#complete').click()")
        return {
            "taskId": "jingcai-local-contract",
            "instruction": "在本地隔离页面完成一次可验证的浏览器动作。",
            "seed": 7,
            "environment": {"name": "BrowserEnv", "taskName": "jingcai-local-contract", "headless": True, "maxSteps": 1},
            "steps": [
                {"step": 0, "kind": "reset", "obs": _observation_summary(reset_obs), "reward": 0, "rawReward": 0, "terminated": False, "truncated": False},
                {"step": 1, "kind": "step", "action": "page.locator('#complete').click()", "obs": _observation_summary(obs), "reward": reward, "rawReward": reward, "terminated": terminated, "truncated": truncated, "taskInfo": step_info.get("task_info")},
            ],
            "summary": {"stepCount": 2, "totalReward": reward, "terminated": terminated, "truncated": truncated, "success": bool(reward == 1.0 and terminated)},
            "evaluation": {"success": bool(reward == 1.0 and terminated), "score": reward, "message": step_info.get("task_info", {}).get("result")},
            "runtimeExecution": True,
            "runtimeActionPolicy": "local_fixture_only",
        }
    finally:
        env.close()


def _run_agentlab() -> dict[str, Any]:
    from agentlab.experiments.loop import StepInfo

    fields = list(StepInfo.__dataclass_fields__)
    first = StepInfo(step=0, obs={"url": "about:blank"}, reward=0.0, raw_reward=0.0, terminated=False, truncated=False, action=None, stats={})
    final = StepInfo(step=1, obs={"url": "about:blank", "last_action": "local_contract"}, reward=1.0, raw_reward=1.0, terminated=True, truncated=False, action="local_contract", agent_info={"source": "runtime_probe"}, stats={})
    return {
        "exp_args": {"env_args": {"task_name": "jingcai-local-contract", "task_seed": 7, "headless": True, "max_steps": 1}, "agent_args": {"agent_name": "runtime-probe"}},
        "steps_info": [
            {"step": first.step, "obs": first.obs, "reward": first.reward, "raw_reward": first.raw_reward, "terminated": first.terminated, "truncated": first.truncated, "action": first.action, "stats": first.stats},
            {"step": final.step, "obs": final.obs, "reward": final.reward, "raw_reward": final.raw_reward, "terminated": final.terminated, "truncated": final.truncated, "action": final.action, "agent_info": final.agent_info, "stats": final.stats},
        ],
        "summary_info": {"terminated": True, "truncated": False, "cum_reward": 1.0, "cum_raw_reward": 1.0, "n_steps": 2},
        "status": "done",
        "runtimeExecution": "upstream_module_probe",
        "runtimeActionPolicy": "metadata_only_no_model_call",
        "runtimeEvidence": {"stepInfoFields": fields},
    }


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"browsergym", "agentlab"}:
        raise SystemExit("provider must be browsergym or agentlab")
    result = _run_browsergym() if sys.argv[1] == "browsergym" else _run_agentlab()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"errorType": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))
        raise
