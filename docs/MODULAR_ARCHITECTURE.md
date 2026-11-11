# 模块化与插件架构：按子任务拆分，按契约替换

状态：已获实施批准的架构规范。目录、接口和验收要求按执行清单落实；当前已实现与尚未实现的部分见 IMPLEMENTATION_STATUS.md。

## 1. 目标和基本原则

让任务分析、几何、感知、仿真、策略、规划、控制、训练、评价和真实机器人驱动可以分别维护与更换。最重要的替换场景是：同一任务在相同观测/动作契约下，由脚本策略切换到神经网络策略，而不修改前端、仿真场景或机器人 SDK 执行代码。

仅分文件夹不够，必须同时限制 import、运行时通信、文件所有权和配置职责。采用“模块化应用 + 重型 Worker 进程隔离”，首版不为了分离目录就把所有小函数做成网络微服务。

## 2. 规划的目录边界

~~~text
Demo2Real-Agent-Workbench/
  frontend/                       # 页面、交互、事件展示；不含机器人控制算法
  backend/                        # HTTP/API、会话、数据库、工件访问
  orchestration/                  # 任务 DAG、资源预约、Agent 生命周期
    agents/                       # 角色与工具权限
    providers/                    # 多模态/语言模型服务
  contracts/                      # 纯消息/schema/接口；不依赖算法或硬件包
    observations/
    actions/
    policies/
    tasks/
    events/
    artifacts/
    experience/                   # 问题事件、案例与检索结果
  modules/
    demo_import/                  # 导入、解码、同步、源数据审查
    task_analysis/                # 任务假设、阶段、技能图
    geometry/                     # 资产、功能坐标、机构和标定核验
    perception/                   # RGBD→测量/状态估计
      segmentation/               # SAM3 等独立实现
      pose_estimation/            # FoundationPose 等独立实现
      depth_refinement/           # 几何精修
      state_estimation/           # 时间/身份/历史与不确定性
    simulation/                   # 场景、物理、扰动、仿真后端
      isaac/
      disturbances/
    policies/                     # 只做决策/动作提议，不发送设备命令
      scripted/
        rule_supervisor/          # 高层规则选择与恢复
        demo_motion/              # 示教参考/脚本局部运动提议
      neural/
        state_supervisor/         # 学习的高层技能选择
        local_motion/             # 学习的局部连续运动
      adapters/                   # 显式、可测试的观测/动作表示转换
    skills/                       # 抓取、对准、插入、搜索/重抓等技能执行逻辑
    planning/                     # 自由空间规划、参考变形、IK、时间参数化
    control/                      # 目标跟踪、限幅、模式和控制周期处理
    execution/                    # 唯一命令权威、epoch/过期检查、watchdog
    robot_backends/
      nero_sdk/                   # 唯一允许导入 pyAgxArm 的业务模块
      mock_robot/                 # 无硬件与测试替身
    camera_backends/              # 真实相机采集实现
    learning/                     # 数据集、BC、迭代纠正、训练算法插件
    evaluation/                   # 冻结判据、负例、留出与统计
    experience/                   # 独立问题/经验模块，其他模块经端口使用
      ingestion/
      curation/
      retrieval/
      checklists/
    deployment/                   # 策略包导出/安装/兼容检查，不直接控制电机
  workers/                        # 薄进程入口，调用模块，不复制算法
  application/                    # 组装入口：把接口绑定到具体实现
  config/
    environments.yaml             # 外置环境路径；已创建的唯一环境路径入口
    policies.yaml                 # 规划中的策略选择配置
    robots/                       # 机器人/工具/标定配置
    workflows/                    # 工作流配置
  tasks/
    connector/                    # 仅本任务的 spec/资产引用/判据和技能参数
    rigid_pick_place/             # 第二任务，验证复用
  vendor/                         # 实体复制的 SDK/第三方组件
  assets/
  weights/
  knowledge/                      # 分类、模板、版本化案例与证据
  data/
  runs/<run_id>/                  # 运行产物，不写回源码目录
    demo_import/
    task_analysis/
    geometry/
    perception/
    simulation/
    policy/
    learning/
    evaluation/
    deployment/
  deployments/
  docs/
  scripts/
~~~

上述 neural 子目录表示扩展边界，不承诺首版同时训练所有模型。训练模型逐个接入，以实际需要和验证结果为准。

每个主要模块拥有自己的 README、默认配置/schema、测试和对外入口；依赖单独声明。首版可在同一代码仓库中组织，不强制每个小目录独立发行一个 Python 包。重型/冲突依赖采用单独环境和 Worker，避免 API 进程启动时加载 Torch、Isaac 或 SDK。

## 3. 依赖只能向约定方向流动

~~~mermaid
flowchart TD
    UI[frontend] --> API[backend API]
    API --> O[orchestration]
    APP[application 组装入口] --> O
    APP --> M[具体模块/插件]
    O --> PORT[contracts / 注册接口]
    M --> PORT
    P[策略插件] --> PORT
    P --> RESULT[动作提议]
    RESULT --> E[execution / control]
    E --> B[仿真或机器人后端]
~~~

约束：

- contracts 只定义数据、单位、版本、错误和接口，不 import Isaac、Torch、SDK、FastAPI、数据库或具体模块。
- 前端只调用 API/事件；后端只做应用服务，不嵌入模型推理、运动规划或 CAN 发送。
- orchestration 调注册接口和工件协议；不写 if task_id == connector 的散落分支。
- 策略不 import simulation、robot_backends 或 pyAgxArm，不从设备主动取状态，不直接发命令。
- simulation 不 import scripted/neural 策略实现；只接受规范化执行输入并输出观测/事件。
- planning/control/skills 通过接口使用感知、规划和执行能力，不直接依赖某个特定策略类。
- robot_backends/nero_sdk 内封装具体 SDK、固件和通信细节；更换策略不更改该模块。
- application 是明确的组装位置，可引用具体实现；其他模块通过依赖注入/注册接口获得能力。
- 学习算法插件可声明对相应模型定义的依赖，复用训练与推理的模型结构；训练调度器不能绑定单一网络。
- 公共工具只放单位、序列化、版本等稳定基础逻辑，不建立一个任意塞业务代码的 common/utils 大模块。

模块间确需共享的不是内部 Python 对象，而是小而稳定的契约。重型图像/数组使用项目内工件引用或受控共享传输，不在每次事件中重复塞入全部视频。

## 4. 策略要分两个层级

| 层级 | 脚本实现 | 神经网络实现 | 输出契约 |
| --- | --- | --- | --- |
| SupervisorPolicy | 规则选择继续/等待/重抓/退出 | MLP/时序网络选择技能与恢复 | SkillDecision：技能 ID、有限参数和状态 |
| MotionPolicy | 示教/脚本生成局部运动目标 | 连续局部动作或残差网络 | MotionProposal：声明坐标、时间与限幅的运动目标 |

现有 24→48→32→5 MLP 属于第一行。它不能直接取代第二行的双臂连续轨迹控制。神经网络局部动作也不能自动替代稳定跟踪、碰撞检查或停止机制。

策略角色、输入和输出契约必须兼容，才能只改配置切换。若表示不同，需要显式适配器并重新验证；不是所有名为 predict() 的函数都可互换。

## 5. 稳定的策略接口

以下是接口设计示意，不是当前可运行代码：

~~~python
class Policy:
    def describe(self):
        """角色、版本、观测/动作契约、所需能力、状态保存语义。"""

    def load(self, package, services):
        """加载本策略的配置/预处理/权重；services 不含设备发送权限。"""

    def reset(self, episode_context):
        """清本回合状态，例如示教游标或循环网络隐状态。"""

    def predict(self, observation_packet, policy_state):
        """返回 PolicyResult：状态、动作提议、新隐状态和诊断。"""

    def close(self):
        """释放本实现的模型和资源，不改变真实机器人模式。"""
~~~

- ObservationPacket 包含时间、机器人状态、估计对象/关系、可见性/不确定性、当前技能、允许的参考上下文及数据契约版本。不得加入仿真真值或评估结果。
- PolicyResult 可为 ready、wait、needs_information 或 error。没有可用动作时返回明确状态，不用全零关节值或随机动作填补。
- 动作提议绑定 action_contract、task/episode/skill epoch、观测序号、坐标、单位和有效期，由执行层验证后才有可能执行。
- 模型预处理、归一化、输出转换和隐状态属于该策略包，不放进全局共享变量。新回合、技能变化、重抓和策略切换按契约 reset。
- 策略不以仿真 tick 代替真实墙钟；超时和旧 epoch 的结果由执行层丢弃。

## 6. 用配置换策略，不在各处改 if/else

规划中的 config/policies.yaml 示例：

~~~yaml
schema_version: 1
active:
  supervisor: rule_supervisor_v1
  motion: demo_motion_v1

implementations:
  rule_supervisor_v1:
    plugin_id: policies.scripted.rule_supervisor
    role: supervisor
    observation_contract: belief_state.v1
    action_contract: skill_decision.v1
    environment_key: app_python

  learned_supervisor_v1:
    plugin_id: policies.neural.state_supervisor
    role: supervisor
    observation_contract: belief_state.v1
    action_contract: skill_decision.v1
    environment_key: policy_python
    package: deployments/example_supervisor

  demo_motion_v1:
    plugin_id: policies.scripted.demo_motion
    role: motion
    observation_contract: local_skill.v1
    action_contract: tool_goal.v1
    environment_key: app_python

  learned_local_motion_v1:
    plugin_id: policies.neural.local_motion
    role: motion
    observation_contract: local_skill.v1
    action_contract: tool_goal.v1
    environment_key: policy_python
    package: deployments/example_local_motion
~~~

在契约兼容且通过验证后，将 active.supervisor 改为 learned_supervisor_v1，或将 active.motion 改为 learned_local_motion_v1。前端从注册器读取可选策略与能力，不硬编码某个网络。

首版只在任务开始前或受控停止后选择/更换策略，不开放机器人运行中的热切换。每次选择生成新的运行记录，重置策略状态并重新核验契约/部署范围。注册器先读轻量描述文件，只有选中并通过兼容检查后，才在指定 Worker 中加载具体模型。

如果网络原始输出为残差而框架要求绝对工具目标，策略包内必须声明、测试 residual_to_tool_goal 适配器，并提供相同版本的参考上下文；不能由 SDK 后端猜输出含义。

plugin_id 由应用中受控注册器解析，不允许网页传任意 Python import 路径或未验收脚本。策略包和环境校验失败就报告不兼容，不静默回退脚本还显示“神经网络运行中”。

## 7. 决策、技能、规划、控制和后端的分界

~~~text
观测提供者
    → SupervisorPolicy 选择技能/恢复
    → SkillExecutor 维护技能生命周期
    → MotionPolicy 提出局部目标，必要时调用规划接口
    → planning/control 做轨迹、IK、限幅和跟踪
    → execution 验证权限、epoch、时效和设备状态
    → SimulationBackend 或 NeroRobotBackend
~~~

各环节可以在初期同一进程中通过接口组合，或因依赖/时延需求分 Worker；逻辑边界不随进程布局改变。不是每个动作都必须重做全局规划，控制周期不等待离线 Agent。

真机独有的模式/固件、CAN、使能、急停和状态解码只放在机器人后端及受控执行服务。传感器与估计也有明确 Provider；策略不需要知道观测来自 Isaac 还是实际相机。

## 8. 每个模块的工件所有权

每次运行使用 runs/<run_id>/<module_id>，不把模型临时输出、日志或产物写进源码。工件 manifest 记录输入 hash、契约/模块/配置版本、输出、质量、失败和依赖。

上下游只消费已完成并校验的工件，不通过猜文件名或读另一模块临时目录通信。大文件引用具有稳定 ID；项目内业务引用使用受控相对路径。

- 任务插件只定义该任务的对象角色、技能图、几何/参数与验收，不复制整个感知或控制模块。
- 模型权重与实验结果不和模块源码混存。
- 各模块测试放各自 tests；跨模块契约/端到端测试另放根 tests，避免测试必须启动全部系统。
- 模块依赖与工件依赖均要声明，不依靠隐式单例、跨模块可变全局变量或当前工作目录。

## 9. 更换模块的验收

必须为替换场景建立测试，而不是只看新目录是否存在：

1. 不安装/导入 Torch 或 Isaac 也能载入契约、启动 API、执行脚本策略的基础测试。
2. 不连接真实硬件也能用 MockRobot 测策略/控制和生命周期。
3. 同一观测 fixture 分别运行脚本与神经网络插件，输出契约和版本均合法；输出不要求数值相同。
4. 新策略的观测、动作、坐标、控制模式、环境、预处理或权重不兼容时显式拒绝。
5. 同一已验收策略通过仿真后端和假硬件后端执行，策略代码不改动；真实后端另外走实际条件和试验验收。
6. 策略挂起、超时、崩溃和切换不会绕过执行层或自动恢复真实运动。
7. 静态检查模块依赖图和禁止 import，阻止策略导入 SDK、控制导入神经网络内部或 API 导入 Isaac。
8. 重抓/阶段切换后，旧推理输出即使尚未到有效期也被 epoch 检查拒绝。
9. 替换策略后的完整留出评价重新运行，不能把接口测试通过当任务性能通过。

测试可以使用明确标记的替身或小模型检查接口，但不能把它们当已训练策略或实际物理成功展示给用户。

## 10. 迁移顺序

先冻结 contracts 和模块边界，再把旧脚本拆出“观测、决策、目标生成、控制、设备调用”；保证脚本版本回归后再接神经网络实现。不能先把原有大脚本放进 policies/scripted 就认为完成解耦。

最小实现顺序：契约 → 注册/组装 → Mock 与脚本插件 → 单模块测试 → 仿真回归 → 同契约神经插件 → 重新评价 → SDK 适配与受控真机测试。

## 11. 失败经验不依赖具体策略实现

记录、归并和检索由独立 experience 模块提供；感知、仿真、学习、规划和机器人执行发布标准问题事件，不互相读取内部日志格式或直接写经验数据库。证据保存在项目内，可重建检索索引；新任务的相关问题清单写入本次工件。

检索结果仅是带适用条件的诊断/检查建议，不是动作命令、模型参数或修改验收的权限。真实控制不等待经验模块。完整生命周期、模板和首批 Connector 案例见 [经验模块设计](EXPERIENCE_AND_FAILURE_MEMORY.md)。
