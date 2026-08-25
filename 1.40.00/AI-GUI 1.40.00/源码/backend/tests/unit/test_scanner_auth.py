from gui_agent.onboarding.scanner import _resolve_authentication_state


def auth_facts(**overrides):
    facts = {
        "challengeDetected": False,
        "loginFormDetected": False,
        "loginEntryDetected": False,
        "loggedInEvidence": False,
    }
    facts.update(overrides)
    return facts


def test_saved_session_with_only_public_login_entry_is_nonblocking() -> None:
    state = _resolve_authentication_state(
        [auth_facts(loginEntryDetected=True)],
        has_storage_state=True,
    )

    assert state == "session_nonblocking"


def test_strong_logged_in_evidence_wins_over_public_login_entry() -> None:
    state = _resolve_authentication_state(
        [auth_facts(loginEntryDetected=True, loggedInEvidence=True)],
        has_storage_state=True,
    )

    assert state == "authenticated"


def test_visible_login_form_or_challenge_still_blocks_saved_session() -> None:
    assert _resolve_authentication_state(
        [auth_facts(loginFormDetected=True)],
        has_storage_state=True,
    ) == "blocking_login"
    assert _resolve_authentication_state(
        [auth_facts(), auth_facts(challengeDetected=True)],
        has_storage_state=True,
    ) == "challenge"


def test_authenticated_page_without_imported_state_is_recognized() -> None:
    assert _resolve_authentication_state(
        [auth_facts(loggedInEvidence=True)],
        has_storage_state=False,
    ) == "authenticated"
