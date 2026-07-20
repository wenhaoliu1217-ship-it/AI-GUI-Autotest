# 京彩OPC AI GUI 测试管理端

React/Vite GUI + FastAPI + Playwright Chromium 的本地真实执行版本。

## 启动

```powershell
npm.cmd ci
npm.cmd run start:real
```

也可以双击 `启动真实GUI测试.cmd`。首次运行将创建 `.venv-real`、安装后端依赖并安装 Chromium。

开发模式下 Vite 将 `/api` 代理到 `http://127.0.0.1:8787`。生产一键包由 FastAPI 在同一端口同时提供 GUI 与 `/api`。

## 关键目录

- `src/`：Web 管理端。
- `backend/src/gui_agent/`：计划模型、规则规划、Playwright 执行器、证据和 FastAPI。
- `backend/artifacts/`：真实运行产物。
- `scripts/verify-playwright.mjs`：GUI 全流程联调。

## 验证

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
& '.\.venv-real\Scripts\python.exe' -m pytest -q .\backend\tests
powershell -ExecutionPolicy Bypass -File .\scripts\run-real-verification.ps1 -RunName manual-check
```

## 限制

网页支持两种规划方式：确定性中文规则，以及用户在“AI 模型设置”中临时接入的 OpenAI Responses API / 兼容 Chat Completions。密钥只随单次请求传到本机后端，不落盘、不缓存；模型输出必须通过同一个受约束 `TestPlan` Schema，且经人工审核后才能执行。
