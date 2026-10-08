# 跨平台验收矩阵

这个矩阵用于判断“适配器已实现”和“平台已验收”之间的差别。只有同一平台
上的必需项全部通过，才能把该平台标记为 Certified；预检失败、测试被跳过或
依赖不可用都不能算通过。

## 平台组合

| 平台 | CI Runner | 浏览器 | Runner 模式 | Docker 要求 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| Windows 10/11 | `windows-2022` | Chromium | 进程模式 | 可选（独立 Docker job 验收） | 已有本地基线，需持续回归 |
| Ubuntu 22.04/24.04 | `ubuntu-22.04` | Chromium | Docker Runner | 必需 | CI 验收入口已建立 |
| macOS 13+ | `macos-13` | Chromium | 进程模式 | 可选 | CI 验收入口已建立 |

Firefox、WebKit、Linux/macOS 的真实钥匙串和带 GPU 的 3D 场景不因代码能导入
就自动认证，必须在对应主机补跑真实验收并保留证据。

## 必测项目

每个矩阵单元都必须执行以下检查：

1. 平台预检：Python、Playwright、浏览器和基础运行环境。
2. API/健康检查与后端完整测试。
3. 前端 TypeScript 检查和单元测试。
4. 表单、弹窗、下拉框、上传下载、动态页面重观察与恢复测试。
5. Canvas/WebGL 非空像素、上下文状态和非空截图证据测试。
6. 运行记录、失败分类、审计证据和副作用门禁测试。

Ubuntu 单元额外执行 `--require-docker` 预检，确认 Docker CLI、Engine 和
Runner 镜像可用。Windows 或 macOS 若要宣称 Docker Runner 支持，必须在有
Docker Engine 的主机上显式执行同一项预检和启动/清理验收。

## CI 入口

工作流位于 `.github/workflows/platform-acceptance.yml`，在 `main` 推送或手动
触发时运行。矩阵故意把 Ubuntu Docker Runner 与 Windows/macOS 进程模式分开，
这样没有 Docker Engine 的主机不会被误报为 Docker 验收通过。

CI 失败即表示该矩阵单元未验收，不会自动改变 `docs/platform-support.md` 中的
认证等级。认证等级只能在对应平台的完整矩阵、真实登录态保存/恢复和 3D 证据
均通过后人工更新。

## 本地执行

后端和前端依赖安装后，可在项目根目录执行：

```sh
python tools/platform_preflight.py --pretty
python -m pytest -q --basetemp .pytest-ci
npm ci --prefix frontend
npm run lint --prefix frontend
npm test --prefix frontend -- --run
```

需要 Docker Runner 时，再执行：

```sh
python tools/platform_preflight.py --require-docker --pretty
```

所有命令都应保留终端输出及 `artifacts/` 中的运行证据；任何步骤因依赖缺失
而跳过，都必须在验收报告中标记为未验收。
