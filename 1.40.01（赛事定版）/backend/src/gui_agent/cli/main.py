"""GUI Agent v0.1 命令行。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..demo.server import DemoServer, find_available_port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gui-agent", description="受约束、可审核的 AI GUI 自动化测试框架 v0.1"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="从首版支持的自然语言流程生成 YAML 计划")
    plan.add_argument("text", help="业务流程描述")
    plan.add_argument("-o", "--output", default="examples/generated-plan.yaml")

    show = sub.add_parser("show", help="查看可审核的计划摘要")
    show.add_argument("path")

    validate = sub.add_parser("validate", help="校验计划 Schema，不要求密钥已配置")
    validate.add_argument("path")

    run = sub.add_parser("run", help="执行已经审核的 YAML/JSON 计划")
    run.add_argument("path")
    run.add_argument("--yes", action="store_true", help="跳过交互确认，供 CI 使用")
    run.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    run.add_argument("--artifacts", default="artifacts")

    demo = sub.add_parser("demo", help="启动本地演示站并执行完整示例")
    demo.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    demo.add_argument("--artifacts", default="artifacts")

    serve = sub.add_parser("serve-demo", help="只启动本地演示站，便于查看页面")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve-demo":
            server = DemoServer(port=args.port).start()
            print(f"演示站已启动：{server.url}")
            print("演示账号：admin / admin123。按 Ctrl+C 停止。")
            try:
                while True:
                    server._thread.join(timeout=1)  # noqa: SLF001
            except KeyboardInterrupt:
                server.stop()
                print("\n演示站已停止。")
            return 0

        from ..execution import RunnerConfig, run_plan
        from ..planning.demo_planner import (
            UnsupportedIntentError,
            plan_from_text,
            save_plan,
        )
        from ..planning.loader import PlanLoadError, load_plan, summarize_plan

        if args.command == "plan":
            plan = plan_from_text(args.text)
            path = save_plan(plan, args.output)
            print(summarize_plan(plan))
            print(f"\n计划已保存：{path}")
            return 0
        if args.command == "show":
            print(summarize_plan(load_plan(args.path, check_secrets=False)))
            return 0
        if args.command == "validate":
            plan = load_plan(args.path, check_secrets=False)
            print(f"计划有效：{plan.name}（{len(plan.steps)} 个步骤，{len(plan.assertions)} 个断言）")
            return 0
        if args.command == "run":
            plan = load_plan(args.path)
            print(summarize_plan(plan))
            if not args.yes:
                answer = input("\n确认执行以上计划？[y/N] ").strip().lower()
                if answer not in {"y", "yes"}:
                    print("已取消，未执行任何浏览器操作。")
                    return 2
            result, run_dir = run_plan(
                plan,
                RunnerConfig(Path(args.artifacts), headless=not args.headed),
            )
            _print_result(result.status.value, run_dir)
            return result.exit_code
        if args.command == "demo":
            return _run_demo(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 2


def _run_demo(args) -> int:
    from ..execution import RunnerConfig, run_plan
    from ..planning.demo_planner import plan_from_text
    from ..planning.loader import summarize_plan

    port = find_available_port()
    with DemoServer(port=port) as server:
        os.environ["TEST_BASE_URL"] = server.url
        os.environ.setdefault("ADMIN_USERNAME", "admin")
        os.environ.setdefault("ADMIN_PASSWORD", "admin123")
        plan = plan_from_text("管理员登录后新建客户并分配给员工")
        print(summarize_plan(plan))
        result, run_dir = run_plan(
            plan,
            RunnerConfig(Path(args.artifacts), headless=not args.headed, slow_mo_ms=250 if args.headed else 0),
        )
    _print_result(result.status.value, run_dir)
    return result.exit_code


def _print_result(status: str, run_dir: Path) -> None:
    print(f"\n运行结果：{status.upper()}")
    print(f"报告：{run_dir / 'report.html'}")
    print(f"结构化结果：{run_dir / 'run.json'}")
    print(f"浏览器跟踪：{run_dir / 'trace.zip'}")


if __name__ == "__main__":
    raise SystemExit(main())
