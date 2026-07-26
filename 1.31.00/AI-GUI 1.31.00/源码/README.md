# AI-GUI 1.31.00 Agent-first 跨网站通用母版

面向 Web 应用的 AI GUI 自动化测试框架。管理端使用 React/Vite，后端使用 FastAPI，所有浏览器动作由 Playwright Chromium 执行。

本版本直接从冻结的 1.30.31 派生，并整合站点无关的 Agent-first、澄清、异步、复杂组件、生命周期和统一验收能力。

## 当前能力

- URL 优先的项目接入、L0～L3 分级和安全只读兼容性扫描。
- 项目、环境、登录态和自然语言场景持久化。
- 目标优先的新建测试路径，以及项目级业务范围、术语、对象、状态、示例和操作边界上下文。
- 默认逐步 Agent 执行；固定计划仅用于调试、stable 回放和 CI。
- 最多三轮结构化澄清，在同一 run、浏览器、页面和登录态恢复。
- 模型连接、Schema、多轮能力探针，以及逐网站 DOM／截图传模授权。
- 可选截图视觉适配、Canvas 相对坐标和 App Bridge 数据模型。
- 动作前后截图、Trace、DOM／Accessibility、Console、Network 和模型调用证据。
- 结构化 Finding、人工审核、A～D 稳定性和 Playwright TypeScript 生成。
- 运行进度、取消、报告下载和历史报告批量删除。

## 启动

```powershell
npm.cmd ci
npm.cmd run start:real
```

也可以双击 `启动真实GUI测试.cmd`。开发启动需要 Docker Desktop（WSL2 Linux Engine），使用 `ai-gui-runner:1.31.00`；Docker 不可用时拒绝降级。

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
- 项目默认禁止访问 RFC1918／ULA 私网、回环、链路本地和 CGNAT 地址；只有明确受控的本地测试项目可以开启私网例外。
- Playwright 导航和子资源请求均经过网络范围检查；公网／私网解析范围切换按疑似 DNS 重绑定拒绝并记录原因。
- 项目／场景 `forbiddenActions` 命中的动作直接拒绝；危险动作使用单次人工确认。真实支付始终禁止；正式站提交未支付订单还需完整专项授权和自动取消验证。
- 确认编号绑定运行与具体步骤，只能使用一次；拒绝、取消、错误编号或重复编号都不会执行动作，决定写入运行报告。
- 固定计划、逐步 Agent 和回放在每次运行独立的 Docker 容器内执行；根目录只读，只挂载本次工件目录，并限制为 2 GiB 内存、2 CPU 和 256 PID。
- 容器默认在内核出站层阻断私网、回环、链路本地、CGNAT、组播和保留地址，仅放行 Docker DNS 的 53 端口；显式受控私网项目才启用例外。防火墙初始化后 Runner 降为 UID 10001，能力集清零并启用 `NoNewPrivs`。

## 当前限制

- 视觉 fallback 只有模型真实视觉探针通过并取得当前网站截图授权后才可启用。
- 站点专用路由、selector、Adapter 和业务事实必须由各网站版本或项目上下文提供，不写入通用核心。
- 兼容性扫描和交互登录录制是受域名／私网策略保护的宿主辅助流程，不属于容器 Runner。
- 本地自动测试通过不等于任何第三方或企业内网站的现场验收通过。
