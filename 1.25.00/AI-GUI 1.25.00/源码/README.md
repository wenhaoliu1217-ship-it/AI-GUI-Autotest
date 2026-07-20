# AI-GUI 1.25.00

面向 Web 应用的 AI GUI 自动化测试框架。管理端使用 React/Vite，后端使用 FastAPI，所有浏览器动作由 Playwright Chromium 执行。

当前版本仍处于企业 MVP 完成阶段，不能视为完整的通用 Computer Use、Cesium 适配或企业发布版。

## 当前能力

- URL 优先的项目接入、L0～L3 分级和安全只读兼容性扫描。
- 项目、环境、登录态和自然语言场景持久化。
- 确定性固定计划和逐步 DOM Agent 两种执行路径。
- 可选截图视觉适配、Canvas 相对坐标和 App Bridge 数据模型。
- 动作前后截图、Trace、DOM／Accessibility、Console、Network 和模型调用证据。
- 结构化 Finding、人工审核、A～D 稳定性和 Playwright TypeScript 生成。
- 运行进度、取消、报告下载和历史报告批量删除。

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

## 安全边界

- 只在获得授权的测试环境运行。
- API Key 只随单次请求进入本机后端内存，不写入项目文件。
- 密码、Token、Cookie 和 storageState 不得提交到 Git。
- 删除、退款、支付、发布等高风险动作必须由项目策略阻断或进入人工确认，不能因为自然语言目标出现这些词就视为授权。

## 当前限制

- 通用视觉／Computer Use、完整 Cesium Bridge 和真实自适应找回尚未完成。
- 项目级垂直业务术语／知识上下文包尚未实现。
- FR-11 的隔离执行、私有网络默认阻断、危险动作确认和自动工件清理仍未闭环。
- 当前正式版本是 `1.25.00`；只有企业需求和 Definition of Done 全部通过后才能发布 `1.30.00`。
