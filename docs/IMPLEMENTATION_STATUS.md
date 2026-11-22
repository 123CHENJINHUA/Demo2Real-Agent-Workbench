# 首版实施与验收状态

更新：2026-11-19。

工作台现以示教导入、仿真、训练、独立评价和离线策略包为主流程。机器人页面、API、Nero 适配入口和配置已移除。以下里程碑描述各自发生时的功能和证据。

## 可运行入口

- 前端与API：http://127.0.0.1:8787；`bash scripts/start.sh`。
- 外置环境唯一配置：config/environments.yaml；业务源码/资产/SDK/权重/数据均为本根实体文件。
- 使用：QUICKSTART.md。未完成验收：EXECUTION_CHECKLIST.md。

## 当前最新里程碑

示教→真实Codex任务分析→版本化task_spec→功能几何→真实Isaac场景→独立验证已跑通。Connector场景7a2f6bd23eae482a四节点成功，独立78/78；程序方块场景c9784f26971a43f7四节点成功，独立37/37，明确为合成复用夹具。两场景及旧失败页面的真实Chrome验收通过。当前后端87 passed/1 skipped，新增前端18组组件/SSR通过，0硬件发送。

操作、失败修正与证据详见[TASK_SCENE_WORKFLOW.md](TASK_SCENE_WORKFLOW.md)和runs/acceptance/scenes/end_to_end_report.json。以下初版数字是当时历史证据；最新结果以上述里程碑为准。完整动作专家绑定、扰动数据/学生泛化和真机仍未完成。

## 实际验收

| 范围 | 结果与证据 |
| --- | --- |
| 后端/数据/契约/策略/执行边界 | 36 passed，1 skipped；真实子进程取消含独立session后代、重启、HTTP模型接口、配置切换/过期动作/冻结包/经验修订 |
| 前端 | 构建通过；Chrome实际操作0异常；三路示教预览、81工件分类/搜索/限高、移动布局、空权重拒绝、离线脚本切换；runs/acceptance/frontend |
| 扰动参数 | 18个合法模式/干扰组合实际HTTP载荷，15种非法输入拒绝；隔离接收器，没有把该测试算成18次物理成功 |
| 原始示教 | e7320b6960634a63，复制/审计/经验/几何成功；32回合源中选episode0=555帧/18.5s，三RGB共18采样；语义待模型配置 |
| 数据环境 | app缺pyarrow导致单测1项skip；登记data_python实际解析成功，runs/acceptance/data/episode_metadata_validation.json |
| Isaac台架 | runs/connector_cli_20261110_234842；0/±90deg三个正负例通过，局部0.15N/0.5s保持；有测试夹具，非完整任务/硬件 |
| 完整PhysX基线 | runs/connector_cli_20261111_000106；seed712、XY±25mm/yaw±8deg、位姿噪声0/延迟0，脚本专家双臂摩擦抓取、协同插入、原位开爪通过；不是RGBD也不是学生策略 |
| 扩大变位/噪声 | runs/connector_cli_20261110_235443与235755失败，分别IK残差/插入未收敛；INC-ROBUST-001/002保留未解决 |
| RGBD回归 | run3035e6ca201844ef；实际相机/分割/FP网格与坐标轴已生成，完整回合结论见文末最终记录；初次导入路径故障保留并重试 |
| 高层学习 | e52954c6675744de，真实8→32→5 NumPy BC；合成实现验收夹具留出分类准确率1.0，不能当机器人能力；runs/acceptance/policy_fixture |
| 部署 | deployments/acceptance-fixture，权重/评价/hash/六版本签名，fixture=true、offline_shadow_only、hardware=false |
| 独立性 | 1465源文件约4.609GB实体复制；本项目业务无软链/旧源码硬编码。USD两条纹理越界已修；runs/acceptance/isolation/{doctor,usd_asset_audit,relocation_report}.json |
| 真机 | 0硬件命令；SDK复制，真实连接/使能/运动禁用；只做mock/shadow和拦截验收 |

## 过程中的失败与修复

- 第一次UI创建转换示教任务遇到registry语法错误；日志保留、修复后显式重试成功到语义门控。不能删除失败以制造全绿历史。
- 完整仿真启动发现指驱常量引入无关轨迹数据模块；抽成纯参数模块，完整基线证明范围内修复，INC-MIGRATION-001。
- USD目录改变导致纹理相对路径越出根；修正为项目内相对路径并复审，INC-MIGRATION-002。
- 视觉共享env的editable安装指向其他项目；新启动使用-S并显式加载复制SAM3/FP及环境site-packages，业务源不再借用旧项目。独立性不是只检查主脚本ROOT。
- 隔离视觉Worker起初遗漏本项目深度精修模块；保留真实失败快照，补充本根legacy路径后重试。
- 没有把噪声/时延条件失败修成“放宽插入判据”。0噪声基线与有噪声失败分别报告，避免继承旧成功率。

## 当前仍需完成

- 多模态服务与真实场景预览已完成；下一步实现绑定task_spec的运动规划/技能专家，不把场景成功当动作成功。
- 已有task/asset/frame/evaluation定义、离线冻结和通用场景构建；仍需完整接触/动作判据、第二条真实示教及新功能机构插件。
- 旧Connector观测/技能/规划/控制进一步拆分；通用连续动作网络及专家恢复数据的真实生成/学习/闭环留出。
- RGBD墙钟时延、相机标定与硬件测量噪声、真实接触/锁止参数、历史释放保持问题仍需验证。
- SDK实际反馈/固件/通道、被动读取、当前态接续、独立watchdog/停止、现场授权批次与分级真机试验。

前端能选择策略或导出包，不等于已满足上述真机条件。模型尚未配置不会伪装自动任务完成。

## RGBD最终回合

run3035e6ca201844ef重试后完成：真实渲染腕部RGBD→SAM3→FoundationPose与双视角精修；插入及原位开爪动作通过，released_from_fingers=true，但post_release_retained=false。没有继承成完整锁止可靠性或学生策略/真机成功。

646个工件含50个分割/FP组合panels快照。证据：runs/acceptance/vision/end_to_end_report.json，以及run目录第二次isaac_<ns>/summary.json。落桌保持失败已另归档INC-5c50c992361141dd，关联CONN-006；前端显示物理子项警告。初次视觉导入故障追加范围内修复修订，不删除原失败。

后续新仿真运行即使总体动作succeeded，若有保持验证失败也会返回validation_warnings/issues并由组装层自动归档；不会因为总体状态通过丢失失败子项。

## 最终服务

最终服务已重启加载扰动参数校验、最新问题修订合并和部分验证失败归档。http://127.0.0.1:8787健康检查、真实RGBD图像工件、问题最新修订、默认规则策略及硬件0发送状态全部通过，证据runs/acceptance/final_service_report.json。最终测试36 passed/1 skipped，原始输出runs/acceptance/test_output.txt。

## 2026-11-11 Codex模型接入更新

工作台已选择codex_cli，用户完成项目内官方ChatGPT登录；全局Codex凭据未复制或修改，系统HOME不改变。真实文本请求与三视角12图像的示教任务分析通过。原始任务e7320b6960634a63的task_analysis现为succeeded，产出带证据和未知项的5阶段任务提案。simulation仍needs_configuration（通用资产/功能坐标/冻结判据），后续学习等保持blocked。

CLI具体模型未回传，记录CLI default，不据此声称当前聊天同型号。新Provider不开放工具或机器人权限，保留HTTP模式。前端已显示实际项目登录状态和CLI版本。全套51 passed/1 skipped，前端组件22项通过，另有实际模型调用和浏览器证据。详见CODEX_INTEGRATION.md和runs/acceptance/codex。

## 场景里程碑续作完成

新增任务定义与功能几何/场景模块、四节点场景工作流、真实预览与历史版本界面。两项真实导出失败已追加r2经验修订，保留原始记录：OmniPBR外部MDL依赖、只引用World遗漏PhysicsScene。场景和相机图对应同一暂停时刻，独立重开USD验证刚体/相机/物理节点/关节连接与依赖hash。共享标定只是内部一致性通过，实机外参和未知障碍仍未验证。

真实全动作与RGBD回归仍使用已迁移诊断入口；本轮未重复执行旧动作，也未训练任务学生。下一步先绑定可执行专家及全动作判据，再做扰动恢复数据/留出学习。

## 冻结专家与位置留出验证

同一冻结版本的5例复核及10例新位姿留出全部动作通过，145项源码/配置/绑定输入逐阶段核验，未调参；保持单列。CPU137 passed/1 skipped，真实留出前端验收通过。当前仍是仿真真值脚本专家，学生未训练、真机未连接。报告见FROZEN_EXPERT_RESULTS.md。

## 状态恢复学生实作

用户明确跳过RGBD对照，进入实际PhysX监督学习。18回合/11666条决策，6/6/6分割，旧10例位置留出排除；真实首版24→48→32→5 MLP，验证macro F1选第500轮，独立测试macro F1=51.41%。训练/分类/导出四阶段与前端已实际运行。并非连续动作神经策略，也非RGBD或真机策略。

专家采集中15个可完成插接案例14通过，3个移走案例正确请求人工；重抓8次局部验证通过。失败保留、不改测试案例；未验证监督不混入。神经学生六场景闭环、数据、模型与分类经验见STATE_STUDENT_RESULTS.md。硬件发送0，外置环境只读，所有产物在本根。单开发Agent，无额外LLM请求。

首版学生闭环最终：六种条件5例预期响应通过；插接动作4/5，移走正确请求人工，左掉落IK失败未修复。全部模型/权重绑定校验通过，无测试调参，移走2次置信度守卫等待其余五例0。训练任务已挂实际闭环报告，包明确closed_loop_passed=false，仍仅仿真。

## 恢复规划与学生v2

原重抓失败通过显式v2分段接触中心/转姿规划修正，独立模块与原限位/精度；两项定向PhysX回归动作通过。新20回合/13996条训练第二版，验证选择450轮、先验alpha0.5，新测试原始macro F1=65.72%、守卫后76.68%；同输入冻结v1/v2守卫后61.57%/76.68%仅CPU分类核对。

新模型ef14d8adc1be490d的六种条件物理闭环6/6符合预期，5/5插接动作通过、1/1移走人工退出；仍仅仿真小试，训练中发现恢复后插接夹持异常未解决，稀有决策样本少。所有失败、现场重试与数量语义修订均保留。前端/手机实测通过，CPU148/1skip，硬件0；详见RECOVERY_V2_RESULTS.md。
