# 本地使用与维护

## 启动

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
bash scripts/start.sh
```

浏览器打开 http://127.0.0.1:8787。端口占用通常表示已有实例；同项目第二个调度器会被锁拒绝，不同时启动两份。可用 `bash scripts/start.sh --port 8788` 更换监听端口，但应先关闭原服务。

初次重建轻量应用环境/前端时运行 `bash scripts/install.sh`。它只在本项目创建环境、安装依赖和缓存，不向共享Conda/Isaac环境安装包。外置环境路径统一填入config/environments.yaml。已有dist运行无需Node；构建需要登记的Node/npm。NumPy/Scipy首版允许只读系统库，实际版本由验收报告记录。

## 工作台与导入示教

首页卡片显示实际示教帧、Isaac 场景或仿真 RGBD 预览。默认展示示教、场景和正在执行的任务。可切换“全部运行”或按名称与运行 ID 搜索。点开卡片后，从“概览”看图像和异常，从“运行记录”查看 Agent、阶段和事件，从“产物文件”定位输出目录并搜索文件。

进行中的任务排在首页前面。打开任务的「概览」可看到已完成阶段数、正在执行的 Worker，以及总控派工、专责分析、汇总的实时状态。卡片显示 Agent 收到的分工、模型事件和正在等待的上游阶段。派工后实际登记的专责阶段会计入总数。「运行记录」里可展开已归档的派工单和报告。

在工作台新建任务，点击“浏览”从本机目录选择示教，也可以直接输入绝对路径。示例原始输入位于 `data/reference/connector/source_demo`，episode 索引选 0，模型路径使用本项目 `assets/objects/connector`。源目录只读，导入后任务使用自己的副本。

展开“示教索引、Agent 与物体模型”，可指定 2、3 或 4 个专责 Agent，也可用默认的自动派工。总控 Agent 单独计数，“运行记录”显示所选数量和实际派工结果。

示教目录按服务进程的本机读取权限选择，可从“根目录”进入其他磁盘的挂载目录。浏览到项目的上级目录时，请继续打开具体的示教文件夹再导入。外部物体模型先实体复制到本项目，再填写其项目内路径。

每次导入创建一个示教目录 `runs/<示教ID>/`。导入副本、审查、任务提案和版本都按阶段放在这个目录中。后续场景、专家执行、训练数据、权重和离线策略包位于 `derived/<运行ID>/`，实验批次清单位于 `experiments/<批次ID>/`。运行详情显示实际产物目录，工件列表可搜索路径。已有运行可继续按原路径查看。

跨任务复用的经验、失败记录和证据快照保存在 `knowledge/`。项目共享的资产模板通过任务定义引用。单一示教的产物随源示教归档，混合多条示教的训练使用独立运行目录。

导入、审计、经验检索和几何审查完成后，总控 Agent 为新示教派工。示教与动作、验收与风险固定参与，几何与接触、观测与标定按任务证据选用。“运行记录”中的“多 Agent 分析”可以打开派工单、专责报告和最终提案。各角色共用设置页中的模型服务配置，分别发起请求。

模型服务或示教图像缺失时，派工会显示待配置。可在设置页配置本机 Codex 或兼容 HTTP 服务，核对示教采样后重试。已有工作流使用其原始依赖图。模型提案仍需核验任务定义、功能几何和评价契约。

本机 Codex 的模型名称和推理强度可在「环境与模型配置 → 模型服务」填写并保存。总控与专责 Agent 使用项目保存的参数。推理强度留空时由 CLI 选择默认值，测试结果区保留请求强度。

## 任务定义与真实场景预览

数据审计默认采样前视、左腕、右腕和侧视等最多四路 RGB。多视角 Agent 为每路视角分配模型输入帧。缺少几何的未绑定任务可在「任务定义 / 场景」构建近似预览。近似几何、输入帧及源任务版本记录在场景估算报告中，实际渲染和 USD 位于同一个派生运行目录。

新导入的示教会先构建初版场景，随后根据录制视角和上一版渲染自动进行两轮独立修正。每轮都会重新渲染并检查 USD 和局部物理，最后生成实景对照报告。提供项目内 OBJ 或 STL 模型时，工作流绑定模型文件，记录哈希并沿用已有绑定。已登记的任务模板保留其资产。缺少模型的对象使用带证据引用的代理几何。未提供明确单位时，尺度属于预览估算。

「概览」可展开逐轮渲染与修正记录。对于仍有可见差异的派生场景，可在「任务定义 / 场景」从这一版继续修正。实景对照记录未确认的区域和可见差异。

已接入本机Codex；原始示教任务e7320b6960634a63的真实图像分析已成功。进入任务定义区域查看/编辑task_spec，离线冻结或构建当前版本场景；新场景job显示四个实际工具阶段、真实全景/腕部图像、USD和独立检查。Connector成功场景为7a2f6bd23eae482a。程序方块c9784f26971a43f7仅为复用测试夹具，不是另一条真实示教或抓放成功。修改定义后须重新构建，旧图片只绑定旧版本。

详见[TASK_SCENE_WORKFLOW.md](TASK_SCENE_WORKFLOW.md)。场景局部通过不代表完整动作或策略已训练，离线冻结也不授予接触/真机执行权限。

## 仿真诊断

在工作台首页底部展开“离线诊断”，可访问已迁移 Connector 的三种诊断：

- 机构台架：0/±90度正负例，局部0.15N/0.5s保持；带测试夹具，不等于完整机器人任务。
- 位姿代理：PhysX摩擦抓取、双臂对键插入及原位开爪。0噪声/0延迟是迁移基线；不是RGBD或鲁棒性证明。
- RGBD：标定腕部相机→SAM3→FoundationPose→双视角精修，生成分割/网格/坐标轴面板，推理可慢于真实墙钟。

折叠参数可设XY范围(≤50mm)、yaw(≤30度)、噪声(≤5mm)、延迟(≤0.5s)和push/drop/relocate/occlude/remove。范围只是受理上限，**不是已通过的泛化范围**。已出现更大变位和0.3mm/66ms条件失败；故障按分类存档。

命令行完全相同插件：

```bash
.runtime/app-venv/bin/python scripts/connector_sim.py --mode keyed_bench
.runtime/app-venv/bin/python scripts/connector_sim.py --mode physics --seed 712 --spread 0.025 --yaw-spread 8 --pose-noise 0 --pose-delay 0
.runtime/app-venv/bin/python scripts/connector_sim.py --mode rgbd --gui
```

取消会回收 Worker 及其 Isaac 和视觉子进程组。服务重启标记中断任务，可在页面中重试。重型诊断通过 GPU 互斥串行运行。

## 离线策略

高层Supervisor输出技能建议，低层MotionPolicy输出运动提议，执行层处理epoch/顺序/过期/限位和步长。前端可离线切换同角色插件。神经策略必须给本项目内真实权重和正确冻结签名；不兼容时拒绝且保留原配置。

在“离线策略”页选择兼容的插件并查看归档策略包。modules/learning 说明 BC 数据格式，modules/evaluation 说明留出范围，modules/deployment 导出绑定权重 hash 和评价的离线包。`deployments/acceptance-fixture` 使用合成数据验证导出流程。

## 经验复用与排错

经验页按现象/机构/阶段检索，含六条历史Connector案例和新incident。新incident始终标未审核；修复结论用追加修订保留原始失败/证据。每次新任务生成自己的检查表，不能直接套用90度、孔径或弹簧参数。CLI诊断产物也可经经验模块归档，API Worker失败自动归档。

运行 `bash scripts/test.sh -q`、环境自检及报告路径见实施状态文档。系统ROS的pytest插件与本项目冲突，所以测试脚本设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。

搬迁时复制完整本文件夹，保留大文件；${PROJECT_ROOT}会重新展开。外置环境重新配置，不复制其原路径到业务模块。应用venv/Node依赖可在新目录重建。核心业务重定位测试的副本也只在本根.runtime下生成。

## 首版状态恢复学生

打开http://127.0.0.1:8787，选择运行97418110f98f4579查看四个实际训练阶段、样本分布、验证/测试macro F1和checkpoint下载。可在“离线策略”页查看仿真包 recovery-97418110f98f4579 的清单。

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
# 读取既有采集批次，不重新启动仿真
.runtime/app-venv/bin/python scripts/placement_suite.py --batch placement_1794402994935086887 --wait
# 使用实际采集数据另开一次训练（并非打开页面所需）
.runtime/app-venv/bin/python scripts/recovery_student.py --batches placement_1794402603405676901 placement_1794402994935086887 --wait
```

原权重与六场景套件已冻结。重训不覆盖原权重；新的闭环版本需另建登记套件，不能静默改写原测试。详细结果与尚未完成项见STATE_STUDENT_RESULTS.md。

## 第二版恢复学生

前端http://127.0.0.1:8787选择ef14d8adc1be490d，查看实际训练、原始/守卫后指标、权重及六种条件物理闭环报告。仿真包deployments/recovery-ef14d8adc1be490d；validation_limits.json列已测范围和未解决问题。

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
# 只重新汇总既有6例，不启动新仿真
.runtime/app-venv/bin/python scripts/recovery_closed_loop.py --learning-run ef14d8adc1be490d --version v2 --batch placement_1794410253144378528
```

当前等待每33ms复判；遮挡测试2s；掉落稳定0.2s，技能确认0.25s且最多4s；规则专家不可见约8s标人工，学生连续wait硬上限15s。均仿真时间，和初始化/GPU排队不同。参数本轮未改，详细定义见RECOVERY_V2_RESULTS.md。
