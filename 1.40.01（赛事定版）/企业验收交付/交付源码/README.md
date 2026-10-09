# 1.40.01（赛事定版）企业验收包

此目录是独立的 Windows x64 交付副本。原赛事目录及 GitHub 中既有版本未改动。

1. 完整解压 ZIP 到本机可写目录，不要直接在压缩包内运行。
2. 预先安装并启用 Docker Desktop、WSL 2／虚拟化及 Linux Engine；Docker 是企业环境前提，不在包内安装。
3. 双击“校验企业验收包.bat”，确认文件校验通过。
4. 双击“双击启动AI测试.bat”。启动器只使用本包 Python、Chromium 和离线 Runner 镜像，自动选择 8080～8090 端口并打开浏览器。
5. 在界面配置企业测试网站、授权账号／登录态、允许域名和所需模型服务。模型密钥由企业自己填写，不随包分发。
6. 按“企业验收指南.md”验证项目、执行、人工接管和报告。运行证据保存在 artifacts，企业配置保存在 data。
7. 按 Enter 正常关闭，或双击“双击关闭AI测试.bat”。本包只清理归属当前目录的服务和容器。

无需企业现场执行 npm、pip、Playwright 下载或 Docker build。首次启动需要从本地 TAR 导入离线镜像，耗时取决于磁盘。

发布标识及前后端版本为 1.40.01，来源源码内部原标识为 1.32.01。交付副本只做版本元数据、可移植启动和生命周期清理适配；业务逻辑保留来源快照。详见交付变更清单和验收验证结果。

仅诊断 UI：在 PowerShell 中执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -DiagnosticsOnly`。此选项不代表真实 Runner 可用，不会降级为宿主执行；正式验收使用默认启动入口。
