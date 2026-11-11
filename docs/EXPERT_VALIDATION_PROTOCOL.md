# 固定专家版本验证协议

当前阶段：确认脚本专家是否具备继续开展RGBD/扰动数据生成的基础，不训练学生，不连接真机。

## 冻结范围

- 配置：config/experiments/connector_validation_v1.json。
- 版本锁：config/experiments/connector_validation_v1.lock.json，145项源码/配置/绑定资产和证据文件。
- 原5个开发位姿作为regression，另10个位姿作为holdout；seed=2026111101，按六维Latin-hypercube预先生成，运行前已经固定。
- 所有位姿处于XY每轴±10mm、世界Z轴水平角±5°范围；不改变高度、物理参数、示教或动作验收阈值。
- 每阶段、专家进程启动及独立验收时复核hash；测试期间不修控制器、不筛掉失败案例。IsaacLab生成器实际加载hash另外对照记录。

## 验收与范围

实验通过只要求抓取、对键插入、原位松手及证据完整性。保持与完整任务事实照常记录，保持不阻塞本轮。5例复核与10例留出分开汇总，不继承旧版本成功率。

先要求5/5复核和10/10留出均通过，才把本轮标记为可进入下一阶段RGBD小试；这只是保守的流程关卡，不是实机可靠性证明，也不能从10个离散点推出整个范围稳定。失败时保留该冻结版本结果，修复应另开版本，不能覆盖本轮留出成绩。

当前使用仿真真值脚本专家，接触力可供专家与评价使用，不作为未来学生的实机观测。学生仍按用户条件使用相机估计、关节和夹爪状态；固定位置通过后才接回RGBD，确认误差和时延，再生成扰动/恢复数据。

## 运行

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
.runtime/app-venv/bin/python scripts/placement_suite.py --suite connector_validation_v1 --wait
```

本次批次：runs/placement_1794399251647479270。submitted.json记录15个实际run_id及锁hash，report.json由真实调度结果更新；每例在前端具有8个实际工具阶段。没有调用语言模型分析节点。
