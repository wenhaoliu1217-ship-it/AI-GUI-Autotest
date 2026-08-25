from datetime import datetime, timezone

from gui_agent.domain.results import Observation, Status, StepResult
from gui_agent.planning.agent_planner import AgentScenario
from gui_agent.planning.jd_strategy import JDDecisionStrategy, _extract_product_query
from gui_agent.planning.site_strategy import default_site_strategy


def test_extracts_product_from_budgeted_cart_goal() -> None:
    assert _extract_product_query("我想买一个200元以内的鼠标，给我添加3个性价比比较高的到购物车里") == "鼠标"


def test_jd_promotional_page_enters_canonical_read_only_search() -> None:
    strategy = default_site_strategy("https://re.m.jd.com/page/homelike?re_dcp=2y8&cu=true")
    assert isinstance(strategy, JDDecisionStrategy)
    decision = strategy.pre_model_decision(
        AgentScenario(
            name="京东购物车任务",
            goal="我想买一个200元以内的鼠标，给我添加3个性价比比较高的到购物车里",
        ),
        Observation(url="https://re.m.jd.com/page/homelike?re_dcp=2y8&cu=true", title="京东热卖"),
        [],
        "https://re.m.jd.com/page/homelike?re_dcp=2y8&cu=true",
    )
    assert decision is not None and decision.action is not None
    assert decision.action.action.value == "navigate"
    assert decision.action.effect_level.value == "read_only"
    assert decision.action.target == "https://search.jd.com/Search?keyword=%E9%BC%A0%E6%A0%87&enc=utf-8"


def test_jd_search_entry_is_not_repeated_after_success() -> None:
    strategy = JDDecisionStrategy()
    now = datetime.now(timezone.utc)
    history = [StepResult(
        index=1,
        action="navigate",
        target_summary="使用京东站内搜索结果页查找商品：鼠标 -> https://search.jd.com/Search",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
    )]
    decision = strategy.pre_model_decision(
        AgentScenario(name="京东购物车任务", goal="购买200元以内的鼠标并加入购物车"),
        Observation(url="https://www.jd.com/", title="京东"),
        history,
        "https://www.jd.com/",
    )
    assert decision is None


def test_jd_desktop_search_block_recovers_once_through_category_page() -> None:
    strategy = JDDecisionStrategy()
    now = datetime.now(timezone.utc)
    history = [StepResult(
        index=1,
        action="navigate",
        target_summary="使用京东站内搜索结果页查找商品：鼠标 -> https://search.jd.com/Search",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
    )]
    observation = Observation(
        url="https://search.jd.com/Search?keyword=%E9%BC%A0%E6%A0%87&enc=utf-8",
        title="鼠标 - 商品搜索 - 京东",
        accessibility_summary="抱歉由于网络异常导致无法搜索，请稍后再试！",
        failed_requests=["HTTP 403 GET https://api.m.jd.com/api"],
    )
    decision = strategy.pre_model_decision(
        AgentScenario(name="京东购物车任务", goal="购买200元以内的鼠标并加入购物车"),
        observation,
        history,
        "https://re.m.jd.com/page/homelike",
    )
    assert decision is not None and decision.action is not None
    assert decision.action.target == "https://list.jd.com/list.html?cat=670,686,690"
    assert decision.action.effect_level.value == "read_only"

    history.append(StepResult(
        index=2,
        action="navigate",
        target_summary="桌面搜索受限后改用京东分类页：鼠标 -> https://list.jd.com/list.html",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
    ))
    assert strategy.pre_model_decision(
        AgentScenario(name="京东购物车任务", goal="购买200元以内的鼠标并加入购物车"),
        observation,
        history,
        "https://re.m.jd.com/page/homelike",
    ) is None
