# OTA平台填报 Agent Skill Reference

## 使用边界

这个 skill 面向法规人员呈现为一个业务能力：OTA平台填报。内部节点用于后端编排和审计，不在主界面 Skills 列表中展示。

默认命令是高层 Skill Command：`run_until_stop`，运行策略为 `until_before_final_submit`。模型和编排层都不能越过保存/提交前的安全边界。

## 必要输入

- OTA 总表路径
- 工作表
- 值所在列
- OTA 附件文件夹
- 受控 Chrome 与目标页面可用性

缺少或歧义输入时，后端应保留结构化输入请求，由下一轮用户消息先补齐该请求，再进入普通 skill 选择。

## 当前页原则

OTA 平台存在多个可见标签页或步骤。RegPilot 只能处理当前打开的页面或标签页；隐藏 OTA 标签页不视为当前步骤，不应提前填。

## 附件文件夹

附件必须来自操作员明确提供或后端解析确认的本地附件文件夹。模型不能远程下载、生成、猜测或用工作簿所在目录替代附件文件夹。

## 内部节点

- `prepare_fill_run`：确认 OTA 总表、工作表、值所在列、附件文件夹、页面和受控 Chrome 可用，并建立一次 Fill Workflow Run。
- `fill_current_page`：只处理当前打开的 OTA 页面或标签页字段和验证结果。
- `handle_page_attachments`：只在当前页需要附件时，从确认的附件文件夹中处理附件。
- `advance_after_current_page`：点击下一步及必要确认，并在页面提示或阻塞时停止。
- `manual_correction_review`：操作员已在当前页修正后复核并尝试推进，不重新写入当前页字段。

## 人工修正后继续

当页面提示重复记录、缺少附件、红灯项或其他需要人工处理的阻塞时，RegPilot 应明确提示操作员只修正当前页阻塞项，修正后点击“人工修正后继续”，不要自行点击下一步或确认。

该回调进入 `manual_correction_review`，应复核当前页并在允许时推进，不应重新执行 `fill_current_page` 导致覆盖操作员修正。
