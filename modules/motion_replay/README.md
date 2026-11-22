# 录制关节回放审查

读取项目内已校验的 LeRobot 示教副本，按 episode 提取双臂关节时间序列。使用项目内 Nero URDF 校对关节范围，读取当前场景的双臂绑定。轨迹、报告和运行日志归档在该示教 `simulation/replay_check_*/`。

模块使用 `config/environments.yaml` 指定的数据 Python 读取 Parquet。`recorded_trajectory.npz` 保存录制原值。超限关节按场景绑定的 URDF 截到最近上下限，另存 `bounded_replay_trajectory.npz`。报告的 `trajectory_path` 指向离线回放应使用的版本，并记录原始和回放两份轨迹的哈希、修正帧数与复查结果。时间戳和夹爪反馈沿用录制数值。接触动作和策略验收由独立流程负责。

数据审计通过 `timing.py` 从实测序列识别夹爪开合和末端低速停顿。在 `data_audit.json` 中保存候选时段、录制帧号、各臂基座坐标下的工具位姿和相关视频帧。场景布局根据可见交互关联工件及接触点，再用当前场景的机器人配准换算世界坐标。开合或停顿可作为定位线索，实际接触需额外证据。
