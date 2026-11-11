# Demo2Real Agent Workbench

本地可运行首版：选择示教目录，查看实际任务分配、工具/模型角色、阶段、进度、证据与失败；独立模块对接仿真、策略训练和受控部署。

当前地址：**http://127.0.0.1:8787**。仅监听本机。

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
bash scripts/start.sh
```

## 当前已能运行

- 示教实体复制、SHA256、LeRobot元信息、episode级三视角真实RGB采样。已验收第0回合555帧/18.5秒。
- SQLite持久任务/Agent/事件/工件，独立子进程、并发、取消/重试、重启中断记录。页面区分工具Worker和模型Agent，不生成假进度。
- 分类经验检索、不可变失败证据、追加修订和新任务检查表；新incident也能检索，未审核不升级成已验证案例。
- 环境/模型配置页面；真实兼容HTTP多模态调用。未配置模型服务则在语义阶段等待，不自动编造任务。
- 已迁移Connector的真实Isaac台架、位姿代理和RGBD诊断入口；可设置初始变位、角度、噪声、延迟及六类干扰。台架和无噪声完整物理基线已通过；RGBD插入松手完成但落桌保持失败；有噪声/扩大范围的失败保留。
- 高层规则/NumPy MLP分角色替换。第二版使用20个实际PhysX回合、13996条状态决策；新测试原始/守卫后macro F1为65.72%/76.68%。固定权重六种条件6/6响应符合预期：插接动作5/5，移走人工1/1。仍为小规模仿真小试，插接阶段夹持异常等未解决项见RECOVERY_V2_RESULTS.md。
- 版本化task_spec编辑/离线冻结、功能几何分析、真实Isaac场景构建和独立USD/相机/局部稳定性验证；前端显示实际全景/腕图与版本历史。Connector及程序方块两场景已通过，方块是合成复用夹具。
- SDK实体副本、mock/shadow后端和动作契约检查；真实connect/使能/发送保持关闭。

## 当前边界

**尚未实现“任意一条示教→自主构建任意新场景→训练通用策略→点击执行真机”的完整能力。** 当前链路已到任务定义、功能几何、真实场景预览及局部独立验收；已验证模板仍有限，未知功能/接触机构和规划专家仍需配置或插件。目前仅有Connector状态恢复学生的小规模闭环验证；其他新任务与真机仍未验收。Connector历史松手后保持失败仍不能算已解决。

模型服务现已接入本机Codex，用户已完成本项目独立ChatGPT登录；真实文本请求及原始示教12图像的任务分析已通过。无需另填API key。当前聊天历史/工具不会自动继承；具体模型未被CLI回传时显示CLI默认。模型提案仍不等于物理或真机验证。接入说明见docs/CODEX_INTEGRATION.md。

## 独立性与环境

所有业务源码、SDK、资产、权重、示教、数据库、日志、缓存、临时文件和测试副本都在本根目录。已实体复制约4.61GB源资料并记录hash。Isaac/Python/视觉/CUDA/Node/ffmpeg只读复用，路径统一在 [config/environments.yaml](config/environments.yaml)，可从前端修改。

视觉环境位于旧项目的envs子目录，但只复用解释器/依赖；SAM3、FoundationPose、权重与任务实现均为本项目副本。视觉启动跳过外部editable .pth，避免加载其他业务源码。迁移来源仅记录在清单，不作为运行依赖。

`.gitignore` 忽略缓存、导入数据、运行产物和大型视觉源/权重；当前实体文件夹包含它们，**仅git源码不包含这些大文件**。外置环境和宿主驱动也不随文件夹复制。

## 文档与验证

- [新版失败退出：松爪归位与实际物理回归](docs/PHASE_RECOVERY_V3_RESULTS.md)

- [恢复规划与学生v2：实际结果和等待参数](docs/RECOVERY_V2_RESULTS.md)
- [首版状态恢复学生：数据、训练、闭环与限制](docs/STATE_STUDENT_RESULTS.md)
- [使用说明与模块入口](docs/QUICKSTART.md)
- [同版本5例复核＋10例留出验证](docs/FROZEN_EXPERT_RESULTS.md)
- [小范围位姿测试与定向复测结果](docs/PLACEMENT_TEST_RESULTS.md)
- [运动专家最新状态与续作位置](docs/MOTION_EXPERT_STATUS.md)
- [示教→任务定义→真实场景：操作、证据与下一步](docs/TASK_SCENE_WORKFLOW.md)
- [本次实施与验收报告](docs/IMPLEMENTATION_STATUS.md)
- [本机Codex接入与项目内登录](docs/CODEX_INTEGRATION.md)
- [持续执行清单与未完成阶段](docs/EXECUTION_CHECKLIST.md)
- [完整实施计划](docs/IMPLEMENTATION_PLAN.md)
- [模块架构与策略替换](docs/MODULAR_ARCHITECTURE.md)
- [失败经验库](knowledge/README.md)
- [迁移来源、大小和源hash](docs/migration_manifest.json)
- [后续Agent约定](AGENTS.md)

```bash
bash scripts/test.sh -q
.runtime/app-venv/bin/python scripts/doctor.py
```
