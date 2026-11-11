# 本地使用与维护

## 启动

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
bash scripts/start.sh
```

浏览器打开 http://127.0.0.1:8787。端口占用通常表示已有实例；同项目第二个调度器会被锁拒绝，不同时启动两份。可用 `bash scripts/start.sh --port 8788` 更换监听端口，但应先关闭原服务。

初次重建轻量应用环境/前端时运行 `bash scripts/install.sh`。它只在本项目创建环境、安装依赖和缓存，不向共享Conda/Isaac环境安装包。外置环境路径统一填入config/environments.yaml。已有dist运行无需Node；构建需要登记的Node/npm。NumPy/Scipy首版允许只读系统库，实际版本由验收报告记录。

## 导入示教

工作台→新建任务→浏览本机服务允许的目录→选择示教文件夹。示例原始输入已复制到：`data/reference/connector/source_demo`。episode索引选0；模型路径使用本项目 `assets/objects/connector`。源目录只读，导入后任务使用自己的副本。

默认允许本项目和Downloads。需要其他源目录时，在config/import_roots.yaml登记allowed_roots；原SDK/模型/示教可以只读复制，运行时不能引用外部业务资产。外部模型目前先实体复制到本项目，再填写其项目内路径，尚无网页模型上传/复制向导。

每个运行有独立runs/<id>/，查看阶段依赖、实际Worker和模型角色、处理数量、日志和报告。三个示教contact sheet用于检查录制内容，不冒充仿真相机。工件默认报告/预览，可切换全部原始数据或路径搜索。

未配置多模态模型时，前四阶段成功后语义阶段待配置，这是实际阻塞。设置页保存兼容HTTP服务URL、模型名和后端密钥后，运行真实连接测试，再显式重试。密钥不持久化在浏览器，不写进environments.yaml。模型提案仍需要任务/功能几何/评价契约核验；未知任务不自动套用Connector参数。

## 任务定义与真实场景预览

已接入本机Codex；原始示教任务e7320b6960634a63的真实图像分析已成功。进入任务定义区域查看/编辑task_spec，离线冻结或构建当前版本场景；新场景job显示四个实际工具阶段、真实全景/腕部图像、USD和独立检查。Connector成功场景为7a2f6bd23eae482a。程序方块c9784f26971a43f7仅为复用测试夹具，不是另一条真实示教或抓放成功。修改定义后须重新构建，旧图片只绑定旧版本。

详见[TASK_SCENE_WORKFLOW.md](TASK_SCENE_WORKFLOW.md)。场景局部通过不代表完整动作或策略已训练，离线冻结也不授予接触/真机执行权限。

## 仿真诊断

前端提供已迁移Connector的三种真实诊断，均不调用硬件：

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

取消会回收Worker及其Isaac/视觉子进程组；服务重启不会自动恢复机器人或重放动作。GPU互斥保证本应用内重型诊断不争用同一槽。

## 策略与部署

高层Supervisor输出技能建议，低层MotionPolicy输出运动提议，执行层处理epoch/顺序/过期/限位和步长。前端可离线切换同角色插件。神经策略必须给本项目内真实权重和正确冻结签名；不兼容时拒绝且保留原配置。

modules/learning说明真实BC数据schema，modules/evaluation说明留出范围，modules/deployment导出绑定权重hash和评价的包。`deployments/acceptance-fixture`仅为合成实现验收，manifest明确fixture，不能用于真机或当作Connector任务学生。

真实SDK位于vendor/pyAgxArm。当前没有真实使能/运动按钮；NeroRobotBackend.connect/send/hold均拒绝，SDK connect主动行为、固件、CAN、真实反馈、坐标、夹爪、独立watchdog及现场停止还没有验收。影子模式只对显式观测产生建议，无硬件连接和发送。真机步骤见执行清单P6/P7，不能把离线建框架批准理解成硬件试验批准。

## 经验复用与排错

经验页按现象/机构/阶段检索，含六条历史Connector案例和新incident。新incident始终标未审核；修复结论用追加修订保留原始失败/证据。每次新任务生成自己的检查表，不能直接套用90度、孔径或弹簧参数。CLI诊断产物也可经经验模块归档，API Worker失败自动归档。

运行 `bash scripts/test.sh -q`、环境自检及报告路径见实施状态文档。系统ROS的pytest插件与本项目冲突，所以测试脚本设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。

搬迁时复制完整本文件夹，保留大文件；${PROJECT_ROOT}会重新展开。外置环境重新配置，不复制其原路径到业务模块。应用venv/Node依赖可在新目录重建。核心业务重定位测试的副本也只在本根.runtime下生成。

## 首版状态恢复学生

打开http://127.0.0.1:8787，选择运行97418110f98f4579查看四个实际训练阶段、样本分布、验证/测试macro F1和checkpoint下载。部署页的recovery-97418110f98f4579是仿真专用包；不用于直接发送真实机械臂命令。

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
