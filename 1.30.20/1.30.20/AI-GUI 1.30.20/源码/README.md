# AI-GUI 1.30.20 Cesium ion 验收开发版

面向 Web 应用的 AI GUI 自动化测试框架。管理端使用 React/Vite，后端使用 FastAPI，所有浏览器动作由 Playwright Chromium 执行。

本版本已加入 Cesium ion C01-C60 验收目录和关键执行能力，但当前真实通过数仍为 0。缺少固定数据、隔离身份、外部云资源或授权的场景必须保持 blocked，不能视为完整 Cesium ion 发布版。

## 当前能力

- URL 优先的项目接入、L0～L3 分级和安全只读兼容性扫描。
- 递归 open Shadow DOM 扫描、host 路径、slot、可见/隐藏实例和候选 locator 清单。
- 项目、环境、登录态和自然语言场景持久化。
- 目标优先的新建测试路径，以及项目级业务范围、术语、对象、状态、示例和操作边界上下文。
- 确定性固定计划和逐步 DOM Agent 两种执行路径。
- 可选截图视觉适配、Canvas 相对坐标和 App Bridge 数据模型。
- 动作前后截图、Trace、DOM／Accessibility、Console、Network 和模型调用证据。
- 结构化 Finding、人工审核、A～D 稳定性和 Playwright TypeScript 生成。
- 运行进度、取消、报告下载和历史报告批量删除。
- 受控文件仓库上传、隔离下载、SHA-256/MIME/ZIP 安全校验和业务状态轮询。
- Cesium ion C01-C60 验收页、真实页面地图、测试数据缺口、资源台账和结构化副作用策略门。

## 启动

```powershell
npm.cmd ci
npm.cmd run start:real
```

也可以双击 `启动真实GUI测试.cmd`。需要先安装并启动 Docker Desktop（WSL2 Linux Engine）；启动器会构建 `ai-gui-runner:1.30.20`，Docker 不可用时拒绝降级。首次运行还会创建 `.venv-real` 并安装项目扫描所需的宿主 Chromium。

上传固定数据时设置 `GUI_TEST_DATA_ROOT`，该目录会以只读方式传入容器；计划中的 `file_ids` 只能解析到该目录内。

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
- 项目／场景 `forbiddenActions` 命中的动作直接拒绝；删除、退款、支付、提交订单／生产表单、发布和邀请等内置危险动作在未被禁止时进入单次人工确认。
- 确认编号绑定运行与具体步骤，只能使用一次；拒绝、取消、错误编号或重复编号都不会执行动作，决定写入运行报告。
- 固定计划、逐步 Agent 和回放在每次运行独立的 Docker 容器内执行；根目录只读，只挂载本次工件目录，并限制为 2 GiB 内存、2 CPU 和 256 PID。
- 容器默认在内核出站层阻断私网、回环、链路本地、CGNAT、组播和保留地址，仅放行 Docker DNS 的 53 端口；显式受控私网项目才启用例外。防火墙初始化后 Runner 降为 UID 10001，能力集清零并启用 `NoNewPrivs`。

## 当前限制

- C01-C60 尚未达到每场景 5 次、P0 100%、全场景 95% 和两小时 Viewer 稳定性门槛。
- D01-D12、N01-N10 正式数据包、隔离 E2E 身份、Owner/Member、临时 S3/Azure 凭据和故障注入环境尚未提供。
- REST `Link` 分页、UI/API 一致性、Viewer/scene/tiles 深层证据和 60 个独立 Playwright 场景仍需继续实现。
- 项目级垂直业务术语／知识上下文包尚未实现。
- FR-11 在当前单用户 MVP 的测试执行范围已闭环；兼容性扫描和交互登录录制仍是受同一域名／私网策略保护的宿主辅助流程，不属于容器 Runner。
- `Windows一键运行` 中若仍包含 `ai-gui-runner-1.30.00.tar`，该目录是历史冻结包，不属于 1.30.20；必须重建并实测 1.30.20 镜像后才能发布离线包。
- 只有指导书的最终门槛全部满足后，才能声明 1.30.20 已完整接入 Cesium ion。
