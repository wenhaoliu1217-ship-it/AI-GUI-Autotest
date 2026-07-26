京彩OPC AI GUI 自动化测试平台 1.30.10（便携真实执行版）

交付方式：
1. 完整压缩包已包含专用 Python、固定依赖、Chromium 和离线 Runner 镜像。
2. 不需要安装系统 Python，也不需要配置 py、python、pip 或 Node.js。
3. 启动过程不会联网安装依赖、下载 Chromium 或构建 Runner 镜像。
4. 请完整解压后再运行，不要直接在压缩包预览窗口中双击。

机器前提：
1. Windows 10／11 x64，硬件虚拟化已启用。
2. Docker Desktop 及其 WSL 2／Virtual Machine Platform 前提已由管理员完成安装。
3. Docker Desktop 的 Linux Engine 可用，当前用户拥有 Docker Engine 使用权限。
4. Docker Desktop 的安装、企业许可、WSL 启用和可能的重启不属于应用解压过程。

使用：
1. 双击 start.bat。
2. 启动器会校验包内运行时，并在需要时自动启动 Docker Desktop。
3. 若本机尚无 ai-gui-runner:1.30.10，启动器会从包内离线归档加载；不会访问镜像仓库。
4. 浏览器打开启动窗口显示的 http://127.0.0.1:端口/。
5. 确认顶部显示“真实执行服务已连接”，只对已获授权的测试网站执行计划。
6. 正常按 Enter 或直接关闭启动窗口时，Windows Job Object 会回收本包启动的服务进程并释放端口。
7. 若启动窗口异常消失但服务仍在，可双击 stop.bat 手动停止；它只会停止经路径和命令行验证属于当前包的服务。

注意：
- 启动窗口需要保持打开。关闭窗口或按 Enter 后，本地服务会停止。
- 若提示 bundled Python、Chromium 或 offline Runner image missing，请重新获取并完整解压交付包。
- 若 Docker 在 3 分钟内未就绪，请打开 Docker Desktop 处理 WSL／虚拟化／权限提示，
  确认 Linux Engine 为 Running 后再次双击 start.bat。
- Docker 不可用时不会降级到宿主进程。本版本不会生成 Mock 结果。
- server.pid 仅用于当前包的服务生命周期；不得复制其他版本的 PID 文件，也不要手工填写进程号。
- stop.bat 不按端口批量结束进程，校验不一致时会拒绝停止，避免影响其他版本和其他程序。
- 每一步截图位于 artifacts/<运行编号>/screenshots/，GUI 中的“截图”按钮可直接打开。
- server-stdout.log 和 server-stderr.log 保存本地服务日志。

AI 接入：
1. 点击左侧“AI 模型设置”。
2. 填写协议、Base URL、模型和重新生成的新 Key。
3. “测试模型连接”成功后即可使用 AI 生成测试计划。
4. Key 只保留在当前页面内存，刷新后清除，不会写入本文件夹。

规则规划器无法理解时会明确拒绝；AI 计划也必须先审核，最终结果始终来自真实浏览器执行。
