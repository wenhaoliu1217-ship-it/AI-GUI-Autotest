"""MATC-8.17 可接入项目的 AI GUI 自动化测试框架。

顶层子包依赖方向：
    cli -> planning / execution / assertions / artifacts -> domain / security
domain 与 security 不依赖 Playwright、FastAPI 或任何模型 SDK。
"""

__version__ = "0.1.0"
