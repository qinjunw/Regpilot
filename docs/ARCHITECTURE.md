# 架构说明

> 本文描述当前公开代码的实现边界。`docs/roadmap/` 下的 ADR 和架构图是远期目标设计，不代表相关企业集成或风险工作台已经交付；公开版范围以 [作品集公开版说明](PORTFOLIO_EDITION.md) 为准。

## 组件

| 层 | 目录 | 职责 |
| --- | --- | --- |
| Web UI | `static/` | 会话、证据状态、模型设置和流式输出 |
| HTTP 服务 | `src/regulation_agent/server.py` | 本地 API、静态资源和 SSE 连接 |
| 应用服务 | `src/regulation_agent/service.py` | 会话、分析任务、来源台账和工作流编排 |
| 模型运行时 | `src/regulation_agent/model_runtime.py` | Provider 配置、流式调用、重试和工具合同 |
| 模拟填报适配器 | `src/regulation_agent/formfill_bridge.py` | 复现真实 FillHarness 合同，但不访问浏览器或生产填报工作簿 |
| 证据、产物与技能 | `source_documents.py`、`artifacts.py`、`prompts/`、`skills/` | 本地资料证据、报告产物、提示模板和领域工作流 |
| 本地状态 | `%LOCALAPPDATA%\RegPilot\`（默认） | 会话、证据、索引、产物和设置；不应提交到 Git |

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
- 为保留界面和 API 合同，受控 Chrome 路由仍存在，但默认探测器和启动器不会连接或创建浏览器；测试中的浏览器行为由注入替身提供。
- `.xlsx` 可作为用户明确提供的法规资料被证据摄取模块读取；模拟 FillHarness 不会读取或写入生产填报工作簿。
- 模拟结果必须带 `demo_mode=true`，不能被解释为真实填报证据。
