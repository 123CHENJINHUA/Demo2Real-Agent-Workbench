# 离线运动专家

`service.run(ctx)`只接受`modules/planning`核验并固定的plan，隔离启动Isaac Worker；加载已构建USD，而不是重建旧默认场景。输出完整运行记录，随后由独立expert_validation检查动作与保持。工具运行结束不等于任务成功。

当前实现明确为Connector脚本专家、仿真真值观测，无RGBD/学习策略/硬件成功声明。指爪接触与被动阻尼属于显式execution overlay，保留其配置、源码hash、前后USD和轨迹；不覆盖原场景快照。真实机器人接口不在本模块。
