# 架构说明

## 组件

| 层 | 目录 | 职责 |
| --- | --- | --- |
| Web UI | `static/` | 会话、证据状态、模型设置和流式输出 |
| HTTP 服务 | `src/regulation_agent/server.py` | 本地 API、静态资源和 SSE 连接 |
| 应用服务 | `src/regulation_agent/service.py` | 会话、分析任务、来源台账和工作流编排 |
| 模型运行时 | `src/regulation_agent/model_runtime.py` | Provider 配置、流式调用、重试和工具合同 |
| 模拟填报适配器 | `src/regulation_agent/formfill_bridge.py` | 复现真实 FillHarness 合同，但不访问浏览器或工作簿 |
| 证据与技能 | `resources/`、`skills/` | 本地资料、提示模板和领域工作流 |
| 本地状态 | 运行时目录 | 会话、索引和设置；不应提交到 Git |

## 关键数据流

1. 用户导入本地法规资料或登记公开来源。
2. 服务将资料标准化为可检索的证据记录。
3. 用户问题先经过证据检索和上下文组装。
4. 模型只在给定证据边界内生成分析，输出保留引用关系。
5. 会话、来源历史和导出文件落在本机运行时目录。

## 安全边界

- 服务默认仅供本机使用，不应直接暴露到公网。
- API Key 只能通过本地设置注入，不进入示例配置或 Git。
- 当前 HTTP 层没有面向公网的认证和跨域安全设计。
- 公开版只注册模拟 FillHarness；真实 FormFill、浏览器控制和生产映射不在仓库中。
- 模拟结果必须带 `demo_mode=true`，不能被解释为真实填报证据。
