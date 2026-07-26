# 测试运行报告：FR-11 authorized private container browser

- 运行 ID：`20260721-163358-cbec1fad`
- 总体状态：**通过**
- 基础地址：http://172.17.0.2:18992
- 开始：2026-07-21T08:33:59.431664+00:00
- 结束：2026-07-21T08:34:00.925170+00:00
- 耗时：1493 ms
- 退出码：0
- Runner 隔离：docker_container ｜ Windows Job：未绑定 ｜ 内存上限：2048 MB ｜ 强制终止：否

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | open authorized target -> / | 通过 | 431 | open authorized target |
| 2 | screenshot | capture evidence | 通过 | 344 | capture evidence |
