# ISARC 论文构思｜用一条示教部署可复用的双臂施工技能

配套材料：[工作台完整流程](WORKBENCH_WORKFLOW.md) · [可分享的流程 PDF](Demo2Real_Workbench_Workflow.pdf)

## 研究定位

**在每种新工序只提供一条真实机器人示教的预算下，能否让同一套通用双臂机器人适应新工位，连续完成准重复的建筑装配，并在异常时判断恢复或停止？**

瓷砖铺贴、定位打孔和板件安装都包含可重复的操作，构件初始位姿、目标位置、工装和局部工作空间则可能随工位变化。针对单一工序开发专机可以利用任务中的结构，但扩展到更多工序时，硬件和软件开发投入会累积。现场持续采集覆盖各种布局和失败状态的多轮示教也有成本。本文的部署假设是，**复用通用双臂硬件，每种工序只采一条真实示教，把大部分候选试验和恢复数据采集转移到仿真。** 工序之间可各提供一条示教。本文先以光伏板搬运与入槽为主案例，讨论同一工序内的布局泛化及连续执行。

以 OpenVLA 为例，通用视觉语言动作模型可以从大规模跨任务数据预训练，也支持对新场景做适配。本研究关注施工团队为**一个新工序实际新增多少现场示教和设置工作**，因此应把预训练数据规模与新工序的现场采集投入分开报告。数据预算下降是否带来更低部署成本，要依据现场示教、建模、标定和调参总工时验证。[OpenVLA](https://arxiv.org/abs/2406.09246)

本文的一条示教是一次完整的真实机器人操作，包含图像、双臂关节、夹爪和时间信息。可用的 CAD/代理几何、机器人模型、手眼和工具标定、操作员澄清与仿真计算单独记账。光伏板源清单有 11 个回合，本案例读取索引 2 的一条 788 帧记录。正式对照应预先登记示教选择规则，不用剩余回合调参或选模，并报告真正使用的人类回合数。[光伏板示教清单](../../runs/d89cc5bb00a24ba9/demo_import/manifest.json)

## 拟议方法与贡献

### 1. 从同一条操作同时建立可执行场景和动作先验

多 Agent 依据示教图像、轨迹和已有资产理解操作对象、接触阶段、双臂分工、功能几何和独立完成判据。工作台据此构建 Isaac 场景，通过多视角对照、几何与局部物理检查及必要的人工澄清修订场景。动作记录和场景一起绑定到任务版本。光伏板靠近时参考板件或白色支座，落位时参考黄色目标槽，轨迹中体现的夹爪时序和双臂协同为后续试验提供运动先验。**Real-to-Sim 的交付物是可执行、可复审的装配任务包**，包含场景、机器人动作参考和验收条件。[场景与任务工程流程](WORKBENCH_WORKFLOW.md#3-功能几何场景构建与对照)

### 2. 以示教为先验，在物理仿真中迭代装配专家

从连续示教回放或分阶段的末端目标点出发，按物体和工装的实际参照调整接近、搬运、夹爪时序和落位。仿真根据首次非预期碰撞、滑落、位姿残差和验收失败生成后续候选。在训练变位中筛选后，冻结专家并在未参与选择的布局中复测。示教先验对动作的作用可通过**有无示教轨迹约束的同预算对照**检验。除装配通过率，还应测量关节峰值速度、轨迹平滑性、双臂同步和非预期接触，验证仿真候选能否满足实际工位的运动约束。

现有实现包含录制轨迹的插值与雅可比修正，以及较新的阶段目标点、IK 和仿真反馈迭代。后者使用录制首帧作为 IK 初值、参考实际夹爪时序，保留完整轨迹作对照。论文可称其为**示教引导的专家搜索**。后续采用专家状态和动作对监督拟合神经策略时，使用“行为克隆”。[轨迹调整](../../modules/learning/action_program.py) · [目标点专家](../../modules/learning/waypoint_expert.py) · [候选筛选](../../application/action_training.py)

若将“先模仿、再自主微调”作为算法贡献，可用示教拟合阶段运动参考，再让仿真只搜索各阶段的有限修正量。冻结关键时序、可达范围和验收指标，并报告修正前后对抓取参照、双臂相对姿态及落位目标的偏差。这样可以具体检验示教先验在物理试验中起了什么作用。

### 3. 用专家回合监督高层与低层，并用真实物体位姿实例化技能

| 层级 | 输入与输出 | 现状及拟验证内容 |
| --- | --- | --- |
| 高层状态与恢复学生 | 从阶段、近期视觉位姿、关节、夹爪等可部署观测判断进度与异常，输出继续、等待、重抓、重定位、退出或请求人工等决策 | Connector 已验证高层恢复 MLP 的小规模仿真。任务成功、滑落和碰撞状态的可观测性与独立评价需要补证，成功还须核对目标支撑与释放后保持。 |
| 低层连续动作 | 根据高层选择的技能、双臂状态与构件相对位姿，输出连续双臂工具目标或关节与夹爪动作 | 已有脚本执行器可用作对照。拟用专家的物理合格轨迹训练 ACT 或 Diffusion Policy 作为低层学生。需测量接触控制效果与推理延迟。 |
| 统一执行与独立验收 | 核对观测时效、轨迹限位、碰撞和停止条件，独立复核动作和真实装配结果 | 保留脚本与学习策略的统一接口。碰撞触发后的独立停止与最终装配验收有单独的可核对证据。 |

同一专家采集**正常完成和异常恢复两类数据**。低层动作监督来自通过相应物理检查的动作段，高层监督可用原始失效事件和经验证的恢复决策。仿真真值或接触状态可生成标签，学生输入应能在部署时获得。随后，SAM 3 提供物体掩膜，FoundationPose 联合尺度已知的 CAD、RGB-D 和内参估计构件位姿，经标定转到共同工作坐标，再更新抓取与落位的相对参照。同一策略接口可接收仿真与真实观测，摩擦、刚度、观测时延和遮挡仍需逐层验证。[控制层级与接口](../MODULAR_ARCHITECTURE.md#4-策略要分两个层级) · [ACT](https://arxiv.org/abs/2304.13705) · [Diffusion Policy](https://arxiv.org/abs/2303.04137) · [SAM 3](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/) · [FoundationPose](https://openaccess.thecvf.com/content/CVPR2024/html/Wen_FoundationPose_Unified_6D_Pose_Estimation_and_Tracking_of_Novel_Objects_CVPR_2024_paper.html)

## 与最接近工作的关系

| 已有研究 | 本文可检验的切入点 |
| --- | --- |
| Liang 等从人类示教学吊顶板安装的几何适应，Liu 等从一次示教学多阶段装配。 | 借鉴准重复和物体参照，验证施工构件与工装**分别变位**时的双臂接触装配、重复作业及部署投入。[Liang 等](https://doi.org/10.1016/j.autcon.2020.103370) · [Liu 等](https://www.nature.com/articles/s44334-025-00030-3) |
| RialTo 使用现场示教和数字孪生强化策略，DexMimicGen 从少量示教合成双臂数据，WANDA 从一条演示重建场景、重用交互动作并扩增纠错状态。 | 已有 Real-to-Sim-to-Real 方法把场景和动作结合。本文可验证的是**同一条示教、任务专属的接触约束、可复核的专家搜索、分层恢复与重复作业**如何具体解决准重复建筑工序的部署问题。[RialTo](https://www.roboticsproceedings.org/rss20/p015.pdf) · [DexMimicGen](https://arxiv.org/abs/2410.24185) · [WANDA 预印本](https://arxiv.org/abs/2607.13154) |
| ISARC 2026 已有木构装配的主从遥操作采集与扩散策略实机验证。 | 对比每种新工序所需真实示教、现场设置工时、布局变化范围以及释放后保持等指标。[Yang 等](https://doi.org/10.22260/ISARC2026/0074) |

## 主实验设计

**H1 · 一条示教能否提高接触装配的布局泛化。** 同任务、同一套判据和仿真预算下，比较固定轨迹回放、无连续示教轨迹约束的场景目标点搜索、示教引导的阶段适配与物理迭代。条件允许时再拆出「仅阶段适配」。原工位用于复核，留出只动起点、只动目标、两者独立移动三类布局。分组时保持初始板件与支座的承托关系，工装只在允许方向内变化。冻结参数后报告完整通过率、最早失败阶段、落位误差、非预期接触、夹爪撤离和释放后保持。旧光伏板计划曾将板件及两槽作为一个移动组，正式对照需纳入相对位移。

**H2 · 分层策略能否提高连续作业中的恢复能力。** 用同一批专家数据和留出扰动，比较规则高层与学习高层，保持底层固定。再固定高层，比较脚本底层与 ACT 或 Diffusion Policy 中一种已训练的低层策略。记录滑落、遮挡和碰撞事件的判断质量、正确恢复率、错误重试、人工介入及每批连续合格件数。运行多个周期，固定每个循环的释放确认和下一件初始化条件。

**H3 · 能否用可部署观测保持性能，并降低新工序的投入。** 先在相同任务、布局和控制条件下对照仿真真值与仿真 RGB-D 位姿，再做实物相机测姿、影子验证和经授权的机器人台架试验。分别记录实体位姿误差、端到端时延、装配通过和释放保持。统计真实示教条数和分钟数、CAD/标定、人工修正、仿真回合与耗时、换工位是否重新示教。若要证明同一套双臂平台能以较低成本切换工序，可增加一项不同工序并单独给一条示教，测量新增准备工时。

## 当前证据和可发表的范围

| 证据 | 目前能支持的结论 |
| --- | --- |
| 光伏板单条示教 | 788 帧和四路 RGB 支撑场景、任务定义及动作计划。早期完整留出 **0/3**。已记录白座碰撞代理修正及多轮接近轨迹试验，其中第 4 次正式尝试仍出现非预期接触，未进入新留出。可用作接触几何与动作对齐的案例。[早期留出报告](../../runs/d4d5f71444d7462b/simulation/action_program_1795341700145055854/expert_learning_report.json) · [第 4 次尝试](../../runs/d89cc5bb00a24ba9/simulation/action_program_1795358625915068307/episodes.json) |
| 新目标点迭代 | 隔离 Isaac 自由空间夹具由 14.5 mm 开口逐轮调整至 47.5 mm，满足 40 mm 判据并独立复测。证明了反馈、冻结和复验链路，光伏板整段抓取、转移与入槽尚未用此模式验收。[夹具报告](../../.runtime/tests/waypoint-isaac-final/probe_result.json) · [实施记录](../EXECUTION_CHECKLIST.md#基于场景目标点的动作专家迭代) |
| Connector 冻结脚本专家 | 仿真真值驱动。10 个留出布局有 **10/10** 通过抓取、插接、原位开爪，**6/10** 通过另记的释放保持。范围为两个物体各自 XY 每轴 ±10 mm、水平转角 ±5°。[冻结专家结果](../FROZEN_EXPERT_RESULTS.md) |
| Connector 高层恢复学生 | 20 个 PhysX 回合生成监督数据。固定高层 MLP 与脚本底层在六种条件下均给出预期响应，其中需插接的 **5/5** 通过动作检查，移走的一例请求人工。此项使用状态代理，低层神经学生仍待训练和验收。[恢复学生 v2](../RECOVERY_V2_RESULTS.md) |
| RGB-D 与实机 | 另一个仿真回合用 SAM 3 和 FoundationPose 估计位姿，插入和原位松爪通过，释放后保持失败。旧项目 D435i 实物测试处理 **86 帧，82 帧通过视觉质检**，在相机坐标系内工作，未测绝对位姿精度。真实视觉驱动的双臂装配与连续生产仍待验收。[仿真 RGB-D 报告](vision-evidence/connector_rgbd_report.json) · [D435i 跟踪汇总](vision-evidence/d435i_tracking_summary.json) |

按现有证据，稿件定位为**架构、初步仿真与视觉验证**。优先补齐一个工序在原布局和留出布局的完整回合，并与同预算对照比较。确认效益后，再把连续循环、分层策略和实体执行的对应证据写进结果和摘要。

## 论文结构与图表

1. **Introduction**　从建筑准重复工序、专机研发投入和现场数据预算出发，提出每种工序一条示教的部署问题。
2. **Related Work**　讨论建筑准重复装配、数据高效的 Real-to-Sim-to-Real 和双臂模仿学习，交代已有方法使用动作信息的方式。
3. **Method**　讲清楚同一次操作如何产生场景和运动先验，如何做受物理约束的专家迭代，如何监督两层策略，以及 SAM 3 和 FoundationPose 如何更新物体参照。
4. **Evaluation**　以示教成本、留出布局、接触装配、连续作业与分级视觉验证组织结果。
5. **Discussion**　用光伏板案例分析仿真与现实的接触差异，说明缺失几何、手眼、夹持、时延与保持验证如何影响部署。

主图建议用双输入分支体现示教的价值。上支路是示教图像和已有几何进入多 Agent 场景构建，下支路是双臂轨迹与夹爪时序进入动作专家，两路在物理仿真中汇合。专家输出高层决策标签和低层轨迹两类数据，现场视觉更新共享的物体参照。第二张图给出未见布局及完整装配判据。

**暂定英文题目**

*Single-Demonstration Bimanual Skill Learning for Quasi-Repetitive Construction Assembly*

**关键词**　Construction robotics · Learning from demonstration · Bimanual manipulation · Hierarchical control · Real-to-sim-to-real

## 英文摘要初稿

> Quasi-repetitive construction operations follow a recurring procedure while component and fixture poses vary between workstations. Dedicated automation can address individual operations, but collecting many on-site demonstrations and engineering separate systems for each procedure can make deployment costly. We present a framework that uses one recorded bimanual operation per procedure. Multiple agents use visual evidence and available geometry to construct a task scene in physics simulation. The recorded joint and gripper trajectories provide motion references for phase-specific adaptation and expert refinement, with independent checks for contact and assembly completion. Expert rollouts are intended to supervise a high-level recovery policy and a low-level motion policy. For transfer, SAM 3 and FoundationPose provide object-relative references from calibrated RGB-D observations. Preliminary experiments demonstrate scene construction and identify contact failures during photovoltaic panel placement. In a separate connector benchmark, a simulation expert using ground-truth poses completed insertion and release in ten held-out layouts, with post-release retention in six. A high-level recovery policy was evaluated separately with a scripted controller in simulation. Learned low-level control, sustained operation across layouts, and vision-driven physical assembly remain to be evaluated.

## 会议信息

ISARC 2027 官网的主题是推动施工自动化从研究实验室走向现场。官网日程列出 **2026 年 12 月 10 日全文提交**。本文的会议切入点是降低准重复建筑工序的部署成本，并用可复核的接触、恢复和用工指标评价。[ISARC 2027 官方页面](https://www.iaarc.org/isarc-2027)
