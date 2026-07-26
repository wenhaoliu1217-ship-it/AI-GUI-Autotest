from pathlib import Path

from gui_agent.acceptance import AcceptanceRunner, load_scenarios


def _reject_unbound_execution(*_args):
    raise RuntimeError("ready 场景必须由真实企业目标站执行器运行，拒绝生成模拟验收")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "backend" / "benchmarks" / "gaealavic"
    AcceptanceRunner(load_scenarios(root / "scenarios")).run(
        _reject_unbound_execution,
        root / "acceptance",
    )
