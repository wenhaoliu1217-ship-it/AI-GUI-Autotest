# 测试运行报告：FR-11 container browser smoke

- 运行 ID：`20260721-163303-5bb5da8e`
- 总体状态：**错误**
- 基础地址：http://example.com
- 开始：2026-07-21T08:33:04.875179+00:00
- 结束：2026-07-21T08:33:11.781957+00:00
- 耗时：6906 ms
- 退出码：1
- Runner 隔离：docker_container ｜ Windows Job：未绑定 ｜ 内存上限：2048 MB ｜ 强制终止：否

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | open public target -> / | 错误 | 6188 | 页面返回 HTTP 502：http://example.com/ |

## 失败信息

- 失败步骤：第 1 步
- 复现步骤：
  1. open public target -> /

## 可能原因（启发式提示，非确定性诊断）

- **[locator]** 页面元素可能不存在、尚未加载或定位信息已变化，请检查页面和稳定定位属性。
  - 置信度：medium（启发式）
  - 证据：步骤/断言 1；页面返回 HTTP 502：http://example.com/

## 结构化问题（待人工审核）

### 步骤 1 未完成：open public target

- 分类：locator
- 严重度／置信度：Medium／medium
- 实际：页面返回 HTTP 502：http://example.com/
- 预期：动作成功并进入可验证的下一状态
- 证据时间线：
  - 2026-07-21T08:33:05.667677+00:00 · before_action；截图 `screenshots/step-1-before.png`
    - 页面：无标题 · about:blank
  - 2026-07-21T08:33:11.639616+00:00 · after_action；截图 `screenshots/step-1-after-failure.png`
    - 页面：无标题 · http://example.com/
    - 页面返回 HTTP 502：http://example.com/

### 步骤 1 观察到运行时异常

- 分类：runtime
- 严重度／置信度：Medium／high
- 实际：采集到 2 条控制台、页面或网络异常
- 预期：关键页面操作期间不出现未忽略的运行时异常
- 证据时间线：
  - 2026-07-21T08:33:05.667677+00:00 · before_action；截图 `screenshots/step-1-before.png`
    - 页面：无标题 · about:blank
  - 2026-07-21T08:33:11.639616+00:00 · after_action；截图 `screenshots/step-1-after-failure.png`
    - 页面：无标题 · http://example.com/
    - Failed to load resource: the server responded with a status of 502 (Bad Gateway)
    - 服务端请求失败：HTTP 502 GET http://example.com/

### 步骤 1 检测到页面加载完成但没有可见文本、元素或图形内容

- 分类：blank_page
- 严重度／置信度：High／high
- 实际：页面加载完成但没有可见文本、元素或图形内容
- 预期：页面关键内容和交互控件完整可见且可操作
- 证据时间线：
  - 2026-07-21T08:33:05.667677+00:00 · before_action；截图 `screenshots/step-1-before.png`
    - 页面：无标题 · about:blank
  - 2026-07-21T08:33:11.639616+00:00 · after_action；截图 `screenshots/step-1-after-failure.png`
    - 页面：无标题 · http://example.com/
    - 页面加载完成但没有可见文本、元素或图形内容
    - 目标：document
