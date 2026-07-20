import pytest

from gui_agent.security.policy import DomainPolicy, SecurityError, resolve_env_placeholder, resolve_secret
from gui_agent.security.redaction import REDACTION_MASK, Redactor


def test_domain_policy_rejects_cross_domain() -> None:
    policy = DomainPolicy("https://demo.example.com")
    with pytest.raises(SecurityError):
        policy.check_url("https://other.example.com/admin")


def test_redactor_scrubs_nested_values() -> None:
    redactor = Redactor()
    redactor.register("secret-value")
    result = redactor.scrub_mapping(
        {"message": "token=secret-value", "nested": ["secret-value", {"x": "secret-value"}]}
    )
    assert result == {
        "message": f"token={REDACTION_MASK}",
        "nested": [REDACTION_MASK, {"x": REDACTION_MASK}],
    }
    assert b"secret-value" not in redactor.scrub_bytes(b"token=secret-value")


def test_environment_values_and_secret_aliases_resolve_per_run(monkeypatch) -> None:
    monkeypatch.setenv("QA_LOGIN_PASSWORD", "runtime-only-secret")
    redactor = Redactor()

    assert resolve_env_placeholder("${TEST_BASE_URL}", {"TEST_BASE_URL": "https://qa.example.com"}) == "https://qa.example.com"
    assert resolve_secret("LOGIN_PASSWORD", redactor, {"LOGIN_PASSWORD": "QA_LOGIN_PASSWORD"}) == "runtime-only-secret"
    assert redactor.scrub("password=runtime-only-secret") == f"password={REDACTION_MASK}"
