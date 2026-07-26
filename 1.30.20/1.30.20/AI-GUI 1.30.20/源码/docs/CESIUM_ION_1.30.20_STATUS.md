# AI-GUI 1.30.20 Cesium ion 验收状态

日期：2026-07-22

## 结论

1.30.20 已形成 C01-C60 结构化验收目录、页面地图、测试数据缺口、资源台账、副作用策略和 GUI 状态页。当前真实通过数为 0；只读盘点、blocked、unverified、loading 和 dry-run 均不计为通过。

## 本轮真实站点盘点

- 使用用户已登录的浏览器会话，执行只读导航、查询/过滤/排序、临时行选择与恢复、Viewer Home，以及 DOM/ARIA/Shadow DOM 读取；未提交写操作。
- My Assets：11 个现有非 E2E 资产，存储 0.00 GiB / 5.00 GiB；确认 open host `ion-assets-page`。
- My Assets：单次验证搜索、类型过滤、名称排序和行选择/取消，操作后恢复筛选与选择状态；分页因仅一页而保持禁用。
- 资产详情：读取 Cesium OSM Buildings 元数据、Attribution 和代码示例；Viewer open Shadow DOM 中存在 464x250 画布，Home 控件可操作；未执行全屏或剪贴板复制。
- Asset Depot：读取列表与详情结构，未执行 Add to my assets；Clips 为 0，配额显示 0/10，未创建 Clip。
- Usage：单次验证 token 范围过滤并恢复到 All；未读取或保存 token 值，未将 token 标识写入证据文件。
- Authorized Applications：空状态；Account 仅读取字段名、控件类型和 open host `ion-account-page`，未读取表单值。
- Teams：0 个团队；确认 open host `ion-teams-page`。
- Viewer 控制台捕获到一次 `Failed to load asset 1` 错误，C25/C50 因此不能判定通过。
- 未创建、上传、编辑、分享、轮换、删除或购买任何资源。
- 未读取、保存或输出 token 值、cookie、密码、邮箱、账单资料或表单值。

## 已实现能力

- `upload_files`、`download`、`wait_until`，受控文件路径、哈希、MIME、ZIP 路径穿越检查和异步时间线。
- placeholder/name/href/exact、Shadow host 路径和容器作用域 locator。
- 递归 open Shadow DOM 扫描，记录 slot、可见/隐藏控件、候选 locator、副作用入口、网络契约和脱敏 URL。
- 固定运行、Agent 动态动作和 replay 均接入 Cesium 结构化副作用策略。
- 破坏性目标必须同时匹配 `E2E-` 名称和资源台账 ID；高风险动作进入单次人工确认；禁止动作在启动前拒绝。
- `GUI_TEST_DATA_ROOT` 已接入三个 API Runner 入口和 Docker 只读挂载。

## 验证结果

- 后端全套：130 passed。
- 前端 Vitest：18 passed。
- TypeScript lint：passed。
- Vite 生产构建：passed。
- 本地同源 GUI/API：`http://127.0.0.1:8787` 联调通过，API 返回 60 个场景、0 个真实通过。
- 场景状态：41 blocked、14 observed_read_only、5 unverified、0 passed；只读观察不计通过。
- 390x844：页面宽度 390、文档 scrollWidth 390、越界表单控件 0。

## 未通过与阻塞

- Docker CLI 不存在，未构建或验证 `ai-gui-runner:1.30.20`。
- D01-D12、N01-N10 正式固定数据包尚未提供，不能执行上传类闭环。
- 缺少隔离 E2E 账号、Team Owner/Member、OAuth 测试租户、临时 S3/Azure 凭据、Clip/Archive 配额和故障注入环境。
- 尚未完成 60 个独立 Playwright 场景、每场景 5 次、两小时 Viewer 稳定性、REST `Link` 分页、UI/API/文件/WebGL 四类一致性和最终零残留证明。
- 桌面交付中的历史 `Windows一键运行` 若仍引用 `ai-gui-runner:1.30.00`，不得作为 1.30.20 离线发布包。
