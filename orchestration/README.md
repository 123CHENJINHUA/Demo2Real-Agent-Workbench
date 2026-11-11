# 持久工作流与模型提供者

`workflow.py` 定义9节点依赖图；`scheduler.py` 以隔离子进程执行注册工具，最多两个轻任务并发。任务状态、心跳、进度、尝试次数、结果与异常真实落SQLite。取消终止本任务进程组；重试只重做未成功节点；旧result.json在重试启动前删除。

工作流：导入→数据审查/经验检索/几何审查→任务语义→仿真→学习→独立评价→部署。上游不满足时下游blocked。能力未注册/模型凭据未配置时needs_configuration，绝不写假成功。LLM只得到真实本地证据，推断与物理验收分开。

`providers/compatible.py` 使用实际HTTP `/chat/completions`，支持文本/图像请求；无URL/model/API key明确待配置，本地无鉴权服务可填写占位key。密钥不进入工件、事件或公共配置。

首版worker仅运行受控注册函数；不接受网页Python入口、shell或生成代码。心跳代表worker仍存活，不等于算法有进展。重型插件应监督其子进程并产生实际进度/检查点。
