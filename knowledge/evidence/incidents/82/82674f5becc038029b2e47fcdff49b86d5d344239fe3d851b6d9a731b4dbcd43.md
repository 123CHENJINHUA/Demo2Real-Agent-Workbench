# 阶段恢复v3与失败松爪归位：开发回归结果

用户最新明确要求：此类失败终止任务，松开双夹爪，返回本回合载入的初始关节姿态，不请求人工。已实现于显式`phase_version=insertion_guard_v3`，历史v1/v2数据、权重和记录不改。

## 实际退出流程

1. 终止当前技能和送料；将双夹爪目标设为65mm。
2. 物理步确认实际开度至少55mm连续3次；等待0.3秒释放。
3. 预检关节限位、实际手部网格与桌面间隙；尝试双臂一起或分臂关节路径。
4. 每关节目标速率上限0.45rad/s，经原物理驱动归位，保持开爪；至少稳定0.3秒，实际最大关节误差小于0.002rad才通过。
5. 保存失败原因、实际轨迹、松爪/归位报告。任务失败和退出通过分别判定，不自动开始下一回合。开爪/路径/跟踪失败则明确归位未完成。

没有物体重置、关节瞬移或固定抓取。归位预检只覆盖手部/桌面和关节限位，尚未验证任意障碍物与全臂碰撞；本轮仅PhysX，真机发送0。

## 三项开发回归

批次：`placement_1794414470642477881`，单开发Agent，无新增模型请求/训练/RGBD对照。使用冻结v2基策略或原规则专家，加显式脚本阶段护栏；不能称为新v3神经策略。

| 案例 | 插接动作 | 实际退出结果 | 预期阶段行为 |
| --- | --- | --- | --- |
| known_insertion_grip_loss | 通过 | action | 通过 |
| nominal_with_v2_base | 通过 | action | 通过 |
| dual_loss_release_home | 失败 | abandon_reset | 通过 |

双侧丢失案例最终双夹爪开度约65mm，初始关节姿态最大误差分别为0.00067332, 0.00007284rad。独立轨迹核验开爪和归位阶段均存在，最后手指/物体接触力为0。该回合仍是失败回合，不能加入插接成功率。

原失败条件回归完成插接，但其中一次原位重夹只有位姿/宽度确认，独立双指接触力记录为0；没有可靠物理重夹成功证据，禁止作为成功重抓监督。仍需独立接触/传力验证与不使用力输入的更好夹持可观测性。正常插接回归完成严格对键、锁止与原位松手；松手后落桌保持仍仅记录，未解决。

这是定向开发回归，不是新的独立留出泛化成绩。未扩充扰动训练或开展真机只读/影子通信，宿主未发现CAN接口，仍待后续配置。

## 失败保留与分类经验

首轮批次`placement_1794413645393332595`保留：原位重夹候选超限退出；正常分支缺少simulation_time_s导致记录异常；双侧夹持丢失正确放弃归位。旧阈值/源快照在该批`reproducibility/pilot_before_fixes.tar.gz`。新批源码配置及hash在`runs/placement_1794414470642477881/reproducibility/`。

候选超限修正插接入口锚点及圆柱轴向夹持带，保持当前腕方向，避免把自由滚转直接变成大幅手腕运动；插接对键条件未放宽。日志分支补齐物理时间。分类追加修订：INC-e12fcb8b2a1e46ea.r0002（workflow_integrity）、INC-35d861969fb64513.r0003（planning_control，仍部分验证）。原失败全部保留。

## 代码与复现

- `modules/skills/failure_exit.py`：物理松爪和归位。
- `modules/planning/return_home.py`：受限关节路径与手部/桌面预检。
- `modules/policies/scripted/phase_guard`：阶段决策，不导入SDK。
- `modules/evaluation/phase_recovery.py`：从独立轨迹核验退出，保持原插接判据。
- `config/phase_recovery_v3.json`：退出/阶段参数。
- 前端任务详情新增阶段面板，显示实际动作、失败原因与归位报告；失败任务状态不隐藏。

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
bash scripts/start.sh
# 单项复测，会真正提交一个新的离线PhysX回合
.runtime/app-venv/bin/python scripts/placement_suite.py --suite connector_phase_recovery_regression_v3 --cases dual_loss_release_home --wait
```

CPU154 passed/1 skipped（app环境缺pyarrow），前端构建通过。实际浏览器验收结果位于本批`frontend-phase`；浏览器只读，不提交实验/模型请求/硬件动作。全部业务文件、归档和产物在本独立项目，外置Isaac等环境只读复用。
