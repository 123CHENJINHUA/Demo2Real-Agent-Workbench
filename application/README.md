# 应用组装

`registry.py` 是具体业务插件绑定处。稳定入口为 `run(ctx)->dict`；ctx包括root/run_id/output_dir/input/config/dependencies/emit/check_cancel。输入含已导入的import_result，下游消费项目内工件。返回status、质量/缺口和工件信息；completed规范化为succeeded不代表整个任务独立验收通过。

`runtime.py` 将统一环境服务注入调度器；其他调度代码不自行寻找旧项目或重型环境。增加插件时更改受控注册表并添加契约/能力测试，不改HTTP实现。
