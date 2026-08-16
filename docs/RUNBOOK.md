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

在页面右上角设置中填写 `Base URL`、`Model` 和 `API Key`。密钥只应保存在本机配置中。公开演示时使用专门的低权限测试 Key，并在录屏前隐藏设置面板。

## 停止

关闭启动器窗口，或在运行服务的终端按 `Ctrl+C`。不要把运行中生成的配置、会话和导出资料提交到 Git。

## 故障排查

- 页面打不开：检查启动终端中的监听地址和端口冲突。
- 模型调用失败：先验证 Base URL、模型名、Key 和代理网络。
- PDF 无文本：扫描型 PDF 需要先 OCR，本项目不内置 OCR。
- 报告缺少引用：确认资料已完成导入并能在证据检索中命中。

