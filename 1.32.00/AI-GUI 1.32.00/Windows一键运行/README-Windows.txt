京彩OPC AI 网站测试助手（1.32.00 小白优先 Web 版）

前提：
1. 完整解压交付包，不要只复制“双击启动AI测试.bat”。
2. Windows 客户端可直接使用包内 Python、Playwright 和 Chromium；一键 BAT 默认不要求 Docker Desktop。

使用：
1. 双击“双击启动AI测试.bat”。
   如果客户 Windows 无法正确显示中文文件名，也可以双击 `Start-AI-GUI.bat`，功能完全相同。
2. 一键 BAT 默认使用已经验收过的包内本地 Runner，不联网安装依赖，也不要求 Docker。
3. 启动器会打开 http://127.0.0.1:8080/。process 模式使用系统默认浏览器显示 GUI；登录录制会另外打开一个干净的 Microsoft Edge 窗口。
4. 确认顶部显示“真实执行服务已连接”。
5. 只对已获授权的测试网站执行计划。
6. 需要登录时，目标网站会在干净的 Edge 窗口中打开；不会复用可能恢复旧标签页的 GUI 浏览器会话。

注意：启动窗口需要保持打开。需要结束时双击“双击关闭AI测试.bat”停止后台服务；系统默认浏览器窗口可手动关闭。
Uvicorn 服务绑定到 Windows Job Object；直接关闭启动窗口时，Windows 会回收本包服务进程树并释放端口。
若窗口异常消失但仍需确认服务状态，可双击“双击关闭AI测试.bat”。停止器只读取 server.pid，且必须同时匹配
本包路径、runtime/python/python.exe、进程启动时间和 Uvicorn 命令行才会停止进程；不会按端口或进程名
终止其他版本及其他程序。正常停止后 server.pid 会自动删除。
启动器固定使用包内 runtime/python 和 runtime/ms-playwright，不使用系统 Python，
也不会联网执行 pip install、playwright install 或 docker build。缺少任何运行时文件时会直接报错。
若启动失败，窗口会保留错误提示；服务日志写入 server-stderr.log。

便携目录契约：
1. runtime/python/python.exe：随包 Python 3.12 运行时及全部应用依赖。
2. runtime/ms-playwright：与 Playwright 1.49.1 匹配的 Chromium、headless shell、FFmpeg 和 winldd。
3. Docker 隔离版另行提供 runtime/images/ai-gui-runner-1.32.00.tar；本一键客户包不包含 Docker 镜像。
4. backend、dist、artifacts 和 data：应用代码、页面资源、证据及持久数据。

Docker 隔离版需要单独的 Docker 发布包；本包固定使用已验收的 process Runner，避免客户双击后还要安装 Docker。

本版本不会生成 Mock 结果。浏览器未实际完成的步骤不会显示成功。
每一步截图位于 artifacts/<运行编号>/screenshots/，GUI 中的“截图”按钮可直接打开。
一键客户模式使用包内本地 Runner 执行真实浏览器测试；不会生成 Mock 结果。

AI 接入：
1. 点击左侧“高级设置”，再进入“更换 AI 服务”。
2. 填写协议、Base URL、模型和重新生成的新 Key。
3. “运行模型能力探针”通过连接、结构化输出和多轮上下文后，才可启动逐步 Agent。
4. Key 只保留在当前页面内存，刷新后清除，不会写入本文件夹。

逐步 Agent 是默认执行入口。模型接收登录后脱敏 DOM 或截图前，必须按当前网站分别授权；视觉能力未通过真实探针时截图 fallback 保持禁用。信息不足时最多澄清 3 轮，并在同一运行和登录态中继续。

电商边界：真实支付在任何环境都绝对禁止。正式站提交未支付订单还必须同时具备专用账号、固定测试商品和地址、书面授权、自动取消与零残留验证，否则停在提交前。

固定计划只用于调试、stable 回放和 CI，执行前必须审核；不能冒充 Agent 通过。最终结果始终来自真实浏览器执行。
