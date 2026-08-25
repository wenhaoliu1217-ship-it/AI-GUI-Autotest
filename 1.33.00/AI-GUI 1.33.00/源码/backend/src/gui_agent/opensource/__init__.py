"""Open-source GUI-Agent reference and adapter catalog."""

from .adapters import (
    adapter_catalog_payload,
    normalize_agentlab_experiment,
    normalize_browser_use_agent_result,
    normalize_browsergym_trajectory,
    normalize_ui_tars_action_result,
    normalize_webarena_trajectory,
    normalize_openadapt_checkpoint,
    normalize_playwright_cli_trace,
    normalize_playwright_mcp_observation,
    normalize_playwright_mcp_tool_result,
    normalize_stagehand_candidate_result,
    observe_playwright_mcp_url,
)
from .catalog import catalog_payload
from .evaluation import evaluation_fixture_catalog_payload, run_evaluation_fixture
from .integration import (
    EXECUTION_PROVIDERS,
    adapt_decision,
    create_execution_summary,
    execution_observation_context,
    execution_profile,
    execution_profiles_payload,
    execution_prompt_rules,
    normalize_execution_provider,
    record_decision,
    record_step,
)
from .runtime import OpenSourceRuntimeError, open_source_runtime_status, run_open_source_runtime
from .webarena import (
    WebArenaConfigError,
    import_webarena_task_config,
    ingest_webarena_trajectory,
    probe_webarena_sites,
    validate_webarena_self_hosted_config,
)

__all__ = [
    "adapter_catalog_payload",
    "catalog_payload",
    "normalize_agentlab_experiment",
    "normalize_browser_use_agent_result",
    "normalize_ui_tars_action_result",
    "normalize_browsergym_trajectory",
    "normalize_webarena_trajectory",
    "normalize_playwright_mcp_observation",
    "normalize_playwright_mcp_tool_result",
    "normalize_stagehand_candidate_result",
    "normalize_openadapt_checkpoint",
    "normalize_playwright_cli_trace",
    "evaluation_fixture_catalog_payload",
    "run_evaluation_fixture",
    "EXECUTION_PROVIDERS",
    "adapt_decision",
    "create_execution_summary",
    "execution_observation_context",
    "execution_profile",
    "execution_profiles_payload",
    "execution_prompt_rules",
    "normalize_execution_provider",
    "record_decision",
    "record_step",
    "OpenSourceRuntimeError",
    "open_source_runtime_status",
    "run_open_source_runtime",
    "observe_playwright_mcp_url",
    "WebArenaConfigError",
    "import_webarena_task_config",
    "ingest_webarena_trajectory",
    "probe_webarena_sites",
    "validate_webarena_self_hosted_config",
]
