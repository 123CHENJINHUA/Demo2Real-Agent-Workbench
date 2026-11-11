# 机器人后端

真实 SDK 实体复制在 vendor/pyAgxArm，许可证原样保留。`nero_sdk` 是唯一允许未来导入它的业务模块。当前 `NeroRobotBackend` 明确拒绝 connect/send/hold，不调用硬件；SDK connect 可能主动发送配置或查询，不能标成被动监控。mock 与 shadow 均可运行，影子只对显式提交观测产生建议并记录，不读取虚构硬件数据。

实机联调仍需核验固件 `firmeware_version`（SDK字段原拼写）、CAN通道、实际关节反馈而非leader回显、关节/夹爪单位、标定、停止/保持语义与现场试验授权。SDK `move_js` 没有默认平滑规划；不能因新策略是神经网络就直接启用此接口。
