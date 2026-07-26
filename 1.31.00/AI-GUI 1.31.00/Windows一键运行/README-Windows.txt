京彩OPC AI GUI 自动化测试平台（1.31.00 Agent-first 通用母版）

前提：
1. 完整解压交付包，不要只复制 start.bat。
2. 企业 IT 已安装并配置 Docker Desktop；启动器会在需要时自动启动并等待 Linux Engine。
3. Windows 与 Docker Desktop 需满足 WSL2、虚拟化及管理员安装要求。

使用：
1. 双击 start.bat。
2. 首次运行会从包内离线归档加载 ai-gui-runner:1.31.00；已经存在时直接复用。
3. 浏览器打开 http://127.0.0.1:8080/。
4. 确认顶部显示“真实执行服务已连接”。
5. 只对已获授权的测试网站执行计划。

注意：启动窗口需要保持打开。关闭窗口或按 Enter 后，本地服务会停止。
Uvicorn 服务绑定到 Windows Job Object；直接关闭启动窗口时，Windows 会回收本包服务进程树并释放端口。
若窗口异常消失但仍需确认服务状态，可双击 stop.bat。停止器只读取 server.pid，且必须同时匹配
本包路径、runtime/python/python.exe、进程启动时间和 Uvicorn 命令行才会停止进程；不会按端口或进程名
终止其他版本及其他程序。正常停止后 server.pid 会自动删除。
启动器固定使用包内 runtime/python 和 runtime/ms-playwright，不使用系统 Python，
也不会联网执行 pip install、playwright install 或 docker build。缺少任何运行时文件时会直接报错。
若启动失败，窗口会保留错误提示；服务日志写入 server-stderr.log。

便携目录契约：
1. runtime/python/python.exe：随包 Python 3.12 运行时及全部应用依赖。
2. runtime/ms-playwright：与 Playwright 1.49.1 匹配的 Chromium、headless shell、FFmpeg 和 winldd。
3. runtime/images/ai-gui-runner-1.31.00.tar：离线 Runner 镜像归档；也接受 .tar.gz 或 .tgz。
4. backend、dist、artifacts 和 data：应用代码、页面资源、证据及持久数据。

Docker Desktop 是系统级隔离运行时，不能作为普通便携文件夹随包启动。
若 Docker Engine 未启动，启动器会先尝试拉起 Docker Desktop；失败时平台拒绝降级到宿主进程。

本版本不会生成 Mock 结果。浏览器未实际完成的步骤不会显示成功。
每一步截图位于 artifacts/<运行编号>/screenshots/，GUI 中的“截图”按钮可直接打开。
测试执行与回放只在隔离容器内运行；Docker 不可用时启动器直接报错，不会降级到宿主进程。

AI 接入：
1. 点击左侧“AI 模型设置”。
2. 填写协议、Base URL、模型和重新生成的新 Key。
3. “运行模型能力探针”通过连接、结构化输出和多轮上下文后，才可启动逐步 Agent。
4. Key 只保留在当前页面内存，刷新后清除，不会写入本文件夹。

逐步 Agent 是默认执行入口。模型接收登录后脱敏 DOM 或截图前，必须按当前网站分别授权；视觉能力未通过真实探针时截图 fallback 保持禁用。信息不足时最多澄清 3 轮，并在同一运行和登录态中继续。

电商边界：真实支付在任何环境都绝对禁止。正式站提交未支付订单还必须同时具备专用账号、固定测试商品和地址、书面授权、自动取消与零残留验证，否则停在提交前。

固定计划只用于调试、stable 回放和 CI，执行前必须审核；不能冒充 Agent 通过。最终结果始终来自真实浏览器执行。
