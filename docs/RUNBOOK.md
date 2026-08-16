# 运行手册

## 环境

- Windows 10/11
- Python 3.11+
- 可选：OpenAI-compatible 文本模型 API

## 首次运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
regpilot-launcher
```

如果 PowerShell 禁止激活脚本，可先执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 模型配置

在页面右上角设置中填写 `Base URL`、`Model` 和 `API Key`。设置默认写入 `%LOCALAPPDATA%\RegPilot\provider_settings.json`；API Key 是本机明文 JSON 配置，不使用操作系统凭据保险库。公开演示时使用专门的低权限测试 Key，并在录屏前隐藏设置面板，演示后按需删除或轮换。

未配置模型时，工作台、会话、Skills 清单和规则驱动的模拟填报仍可运行。通过自然语言执行法规资料检索、来源台账整理、报告生成和证据约束分析需要可用的模型 API；底层能力可通过测试离线验证。

配置模型后，提问、工具上下文和选定证据会发送到所填 `Base URL`。只使用允许交给该模型提供方处理的资料。

## 本地数据

会话、资料解析结果、原始来源路径、法规索引和导出产物默认保存在 `%LOCALAPPDATA%\RegPilot\`。法规资料摄取可以读取用户明确提供的 `.xlsx`；模拟填报器不会打开或写入生产填报工作簿。公开演示应使用合成或已获授权资料，并在演示后检查本地状态目录。

## 停止

关闭启动器窗口，或在运行服务的终端按 `Ctrl+C`。不要把运行中生成的配置、会话和导出资料提交到 Git。

## 可选：构建 Windows 作品演示包

打包必须在已激活的干净虚拟环境中执行。脚本会拒绝使用全局 Python，避免其他 editable 项目或用户 site 包污染依赖分析：

```powershell
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_regpilot_package.ps1
```

输出默认写入 `dist\Regpilot法规合规领航员\`。该演示包未签名，不应直接作为面向公网或企业生产环境的发布物。

## 故障排查

- 页面打不开：检查启动终端中的监听地址和端口冲突。
- 模型调用失败：先验证 Base URL、模型名、Key 和代理网络。
- PDF 无文本：扫描型 PDF 需要先 OCR，本项目不内置 OCR。
- 报告缺少引用：确认资料已完成导入并能在证据检索中命中。
