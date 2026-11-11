# 策略插件

高层 `RuleSupervisor` 和 `StateSupervisor` 同为8维相机/机器人状态→5类技能建议。脚本与神经实现可互换，设备发送权限始终留在 execution。特征定义见 neural/state_supervisor.FEATURES。禁止加入仿真真值、扰动标签或成功标签作为actor输入。

低层 `DemoMotion` 产生关节位置建议，仅为示教参考跟踪接口；不宣称自身具备位置变化或接触泛化。旧Connector闭环适配器当前仍在 tasks/connector/legacy 中隔离运行，尚未完全拆成这些通用插件。新的连续网络可以实现 MotionPolicy，但不能把高层MLP当作低层控制器。

所有模型/规则尚无真机验证。切换必须核验任务/观测/动作/资产/标定/评价的组合签名。
