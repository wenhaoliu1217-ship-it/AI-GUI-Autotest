from __future__ import annotations

from gui_agent.planning.experience_store import (
    CrossActionExperienceCache,
    ExperienceJournal,
    SuccessExperienceStore,
)


def _accepted_payload() -> dict:
    return {
        "run_id": "run-accepted",
        "base_url_summary": "https://app.example.test/models",
        "scenario_goal": "create and verify a test model",
        "status": "passed",
        "goal_status": "achieved",
        "multimodal_required": True,
        "multimodal_decision_count": 2,
        "model_call_records": [
            {"protocol": "responses", "decision": "action", "multimodal": True},
            {"protocol": "responses", "decision": "complete", "multimodal": True},
        ],
        "steps": [
            {
                "index": 1,
                "action": "click",
                "target_summary": "runtime_id=ai_7",
                "description": "open current selector",
                "progress_assessment": "progress",
                "status": "passed",
            }
        ],
        "assertions": [
            {"type": "visible", "description": "created resource exists", "status": "passed"}
        ],
        "evidence_manifest": {"completeness": 1.0},
    }


def test_only_fully_accepted_multimodal_run_is_promoted(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)

    promoted = store.promote(_accepted_payload())

    assert promoted is not None
    assert promoted["kind"] == "success_only_multimodal_experience"
    assert promoted["advisoryOnly"] is True
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_failed_or_nonvisual_run_never_enters_experience_memory(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)
    failed = _accepted_payload()
    failed["status"] = "incomplete"
    nonvisual = _accepted_payload()
    nonvisual["run_id"] = "run-nonvisual"
    nonvisual["model_call_records"][0]["multimodal"] = False

    assert store.promote(failed) is None
    assert store.promote(nonvisual) is None
    assert list(tmp_path.glob("*.json")) == []


def test_retrieval_is_same_host_and_advisory_only(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)
    store.promote(_accepted_payload())

    matches = store.retrieve(
        "https://app.example.test/new", "create and verify model"
    )
    other_host = store.retrieve(
        "https://other.example.test/new", "create and verify model"
    )

    assert len(matches) == 1
    assert matches[0]["advisoryOnly"] is True
    assert other_host == []


def test_retrieval_isolated_by_origin_and_site_pack_version(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)
    payload = _accepted_payload()
    payload["base_url_summary"] = "http://192.168.31.218:7991/#/mineModelList"
    store.promote(payload)

    assert store.retrieve(
        "http://192.168.31.218:7991/#/mineModelList", "create model"
    )
    assert store.retrieve(
        "http://192.168.31.218:8080/#/mineModelList", "create model"
    ) == []


def test_cross_action_cache_matches_page_state_and_strips_ephemeral_target(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)
    payload = _accepted_payload()
    payload["steps"][0].update({
        "target_summary": "runtime_id=ai_7, role=button[name=继续]",
        "before": {
            "url": "https://app.example.test/models",
            "semantic_summary": {
                "page_key": "/models|Models",
                "route": "/models",
                "signature": "stable-page",
            },
        },
        "after": {
            "url": "https://app.example.test/editor",
            "semantic_summary": {
                "page_key": "/editor|Editor",
                "route": "/editor",
                "signature": "editor-page",
            },
        },
    })
    store.promote(payload)
    experiences = store.retrieve("https://app.example.test/models", "create model")
    cache = CrossActionExperienceCache(experiences)

    hints = cache.suggest({
        "url": "https://app.example.test/models",
        "semantic_summary": {
            "page_key": "/models|Models",
            "route": "/models",
            "signature": "new-runtime-signature",
        },
    })

    assert len(hints) == 1
    assert hints[0]["stateMatch"] == "page_key_route"
    assert hints[0]["requiresFreshGrounding"] is True
    assert "ai_7" not in hints[0]["target"]
    assert "<fresh>" in hints[0]["target"]
    assert cache.suggest({
        "semantic_summary": {
            "page_key": "/other|Other",
            "route": "/other",
            "signature": "stable-page",
        },
    }) == []


def test_cross_action_cache_does_not_recommend_action_failed_in_current_state(tmp_path) -> None:
    store = SuccessExperienceStore(tmp_path)
    payload = _accepted_payload()
    payload["steps"][0].update({
        "target_summary": "runtime_id=ai_7, role=button[name=继续]",
        "before": {
            "semantic_summary": {
                "page_key": "/models|Models",
                "route": "/models",
            },
        },
    })
    store.promote(payload)
    cache = CrossActionExperienceCache(
        store.retrieve("https://app.example.test/models", "create model")
    )
    current = {
        "semantic_summary": {
            "page_key": "/models|Models",
            "route": "/models",
        },
    }
    history = [{
        "status": "error",
        "target_summary": "runtime_id=ai_99, role=button[name=继续]",
        "before": current,
    }]

    assert cache.suggest(current, history) == []
    assert store.retrieve(
        "https://192.168.31.218:7991/#/mineModelList", "create model"
    ) == []


def test_legacy_host_only_experience_is_not_reused(tmp_path) -> None:
    legacy = {
        "schemaVersion": 1,
        "kind": "success_only_multimodal_experience",
        "host": "app.example.test",
        "scenarioGoal": "create model",
        "advisoryOnly": True,
    }
    (tmp_path / "legacy.json").write_text(__import__("json").dumps(legacy), encoding="utf-8")

    assert SuccessExperienceStore(tmp_path).retrieve(
        "https://app.example.test/models", "create model"
    ) == []


def test_every_run_snapshot_is_journaled_without_becoming_verified_success(tmp_path) -> None:
    journal = ExperienceJournal(tmp_path)
    payload = _accepted_payload()
    payload.update({
        "status": "incomplete",
        "goal_status": "incomplete",
        "completion_reason": "model_service_unavailable",
        "system_error": "HTTP 502",
        "steps": [{
            **payload["steps"][0],
            "screenshot": "screenshots/step-1-after.png",
            "before": {"url": "https://app.example.test/models", "semantic_summary": {"page_key": "/models", "signature": "before"}},
            "after": {"url": "https://app.example.test/editor", "semantic_summary": {"page_key": "/editor", "signature": "after"}},
        }],
    })

    record = journal.record(payload, phase="final")

    assert record is not None
    assert record["advisoryOnly"] is True
    assert record["status"] == "incomplete"
    assert (tmp_path / "runs.jsonl").is_file()
    assert (tmp_path / "runs" / "run-accepted.json").is_file()
    matches = journal.retrieve("https://app.example.test/editor", "create and verify a test model")
    assert len(matches) == 1
    assert matches[0]["steps"][0]["afterState"]["pageKey"] == "/editor"


def test_backfill_imports_existing_artifacts_idempotently(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    run_dir = artifacts / "historical-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run-state.json").write_text(
        __import__("json").dumps({
            "run_id": "historical-run",
            "plan_name": "historical workflow",
            "base_url_summary": "https://app.example.test/models",
            "scenario_goal": "resume model workflow",
            "status": "incomplete",
            "completion_reason": "runner_restart_reconciled",
            "goal_status": "incomplete",
            "steps": [{"index": 1, "action": "click", "status": "passed"}],
        }),
        encoding="utf-8",
    )

    journal = ExperienceJournal(tmp_path / "journal")

    assert journal.backfill_from_artifacts(artifacts) == ["historical-run"]
    assert journal.backfill_from_artifacts(artifacts) == []
    record = journal.retrieve(
        "https://app.example.test/models", "resume model workflow"
    )[0]
    assert record["phase"] == "backfill"
    assert record["status"] == "incomplete"
    assert record["advisoryOnly"] is True
