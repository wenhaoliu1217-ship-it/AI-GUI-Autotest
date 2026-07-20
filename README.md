# AI-GUI-Autotest

AI GUI 自动化测试框架的版本仓库。当前 Web 权威版本为 `AI-GUI 1.25.00`，企业需求和 Definition of Done 全部完成后发布 `1.30.00`。

## Version Layout

```text
1.20.00/
├─ AI-GUI 1.20.00/          Original V2 baseline
└─ 1.20.00功能说明.md

1.25.00/
├─ AI-GUI 1.25.00/          Current development baseline
└─ 1.25.00版本更新与需求状态.md
```

## Versions

### 1.20.00

原始 V2 历史基线。支持规则／AI 将明确业务流程一次性生成固定 Playwright 计划、人工审核、真实 Chromium 执行、截图、Trace 和运行报告；不具备逐步 Agent、完整项目配置或 Cesium 适配。

详见 [`1.20.00功能说明.md`](./1.20.00/1.20.00功能说明.md)。

### 1.25.00

当前权威版本。增加项目／环境／场景、兼容性扫描、Run Orchestrator、逐步 DOM Agent、截图视觉适配、结构化 Finding、人工审核、Playwright 代码生成和完整结果管理。

详见 [`1.25.00版本更新与需求状态.md`](./1.25.00/1.25.00版本更新与需求状态.md)。

当前已完成单用户 MVP 范围的 FR-01、FR-02、FR-03、FR-07、FR-08、FR-10。FR-04、FR-05、FR-06、FR-09、FR-11 及整体企业 Definition of Done 尚未全部完成。

## Development

当前源码目录：

```text
1.25.00/AI-GUI 1.25.00/源码
```

在该目录运行：

```powershell
npm.cmd ci
npm.cmd test -- --run
npm.cmd run lint
npm.cmd run build
```

Windows 本地成品入口：

```text
1.25.00/AI-GUI 1.25.00/Windows一键运行/start.bat
```

## Repository Policy

本仓库不提交虚拟环境、`node_modules`、构建产物、运行数据、截图、Trace、日志、登录态、DPAPI 文件、API Key、密码、Cookie、Token 或内网地址。它们必须在本地或受控交付渠道中管理。

只可对已获授权的测试系统运行本项目。自然语言中的退款、支付、删除、发布等目标不构成执行高风险动作的授权。
