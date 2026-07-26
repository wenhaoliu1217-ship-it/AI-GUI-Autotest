# 测试运行报告：Occlusion check

- 运行 ID：`20260722-041023-158473c1`
- 总体状态：**错误**
- 基础地址：http://127.0.0.1:64877
- 开始：2026-07-22T04:10:23.310482+08:00
- 结束：2026-07-22T04:10:25.201212+08:00
- 耗时：1890 ms
- 退出码：1

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 477 |  |
| 2 | click | click @ css=#target | 错误 | 428 | 动作前稳定性检查失败：unoccluded |

## 失败信息

- 失败步骤：第 2 步
- 复现步骤：
  1. navigate -> /
  2. click @ css=#target

## 可能原因（启发式提示，非确定性诊断）

- **[locator]** 页面元素可能不存在、尚未加载或定位信息已变化，请检查页面和稳定定位属性。
  - 置信度：medium（启发式）
  - 证据：步骤/断言 2；动作前稳定性检查失败：unoccluded

## 结构化问题（待人工审核）

### 步骤 1 检测到交互控件中心点被其他元素遮挡，可能无法点击

- 分类：element_obscured
- 严重度／置信度：High／high
- 实际：交互控件中心点被其他元素遮挡，可能无法点击
- 预期：页面关键内容和交互控件完整可见且可操作
- 证据时间线：
  - 2026-07-22T04:10:24.347057+08:00 · before_action；截图 `screenshots/step-1-before.png`
    - 页面：无标题 · about:blank
  - 2026-07-22T04:10:24.622124+08:00 · after_action；截图 `screenshots/step-1-after.png`
    - 页面：无标题 · http://127.0.0.1:64877/
    - 交互控件中心点被其他元素遮挡，可能无法点击
    - 目标：button | text=Run

### 步骤 2 未完成：click

- 分类：locator
- 严重度／置信度：Medium／medium
- 实际：动作前稳定性检查失败：unoccluded
- 预期：动作成功并进入可验证的下一状态
- 证据时间线：
  - 2026-07-22T04:10:24.786930+08:00 · before_action；截图 `screenshots/step-2-before.png`
    - 页面：无标题 · http://127.0.0.1:64877/
  - 2026-07-22T04:10:25.050880+08:00 · after_action；截图 `screenshots/step-2-after-failure.png`
    - 页面：无标题 · http://127.0.0.1:64877/
    - 动作前稳定性检查失败：unoccluded
