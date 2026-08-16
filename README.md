# Regpilot法规合规领航员

一个本地优先的法规证据检索与分析工作台。它把本地法规资料、来源台账和大模型分析连接成可追溯的工作流，重点展示 AI 应用工程中的上下文构建、证据约束、流式交互与会话管理。

## 当前稳定边界

- 导入 `md/txt/html/json/csv/docx/xlsx` 以及可提取文本的 PDF。
- 对本地证据进行检索、引用和证据包构建。
- 使用 OpenAI-compatible API 生成受证据约束的法规分析与报告。
- 维护法规来源台账、去重、历史记录并导出 JSON/CSV。
- 本地保存会话和模型设置；API Key 不进入仓库。
- 内置确定性的模拟填报适配器，用于展示字段校验、人工介入和提交前停止合同。

公开版不会启动受控 Chrome、读取真实工作簿或访问生产填报网站。真实网页自动化、生产映射和 Cookie/浏览器 Profile 只存在于私有源码与本地机密数据区。

如在作品页面附带脱敏演示视频，视频展示的是完整本地集成原型；本公开仓库默认使用确定性模拟填报器。两者应在上传说明中明确区分。

## 快速启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
regpilot-launcher
```

默认在本机浏览器打开工作台。模型配置可在界面中填写，也可以先不配置模型，仅查看本地索引和来源台账能力。

## 文档

- [运行手册](docs/RUNBOOK.md)
- [架构说明](docs/ARCHITECTURE.md)
- [作品边界](docs/PORTFOLIO_SCOPE.md)
- [演示脚本](docs/DEMO_SCRIPT.md)
- [远期架构路线图](docs/roadmap/adr_regpilot_market_access_risk_prevention_architecture.md)

## 验证

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

测试中的路径、工作簿名和密钥均为合成占位值，不对应真实账号、客户文件或生产凭据。

## 发布状态

这是仅供作品评估的源码展示仓库，不是开源软件。请阅读 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
