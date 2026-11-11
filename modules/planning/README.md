# 固定场景的 Connector 离线专家绑定

本模块只把**已经构建并独立复审的场景**与已迁移的 episode 0 专家绑定，不构建第二个默认场景，不执行 Isaac、视觉模型或硬件。

- `create_plan(ctx) -> dict`：成功写 `ctx.output_dir/expert_plan.json`，失败返回 `needs_configuration`。必要输入是 `spec_path/spec_hash/spec_revision/scene_manifest_path/demo_path`；独立复审路径优先使用 `ctx.dependencies.scene_validation.report_path`，其次 `input.scene_validation_path`。
- `load_plan(root, path) -> dict`：执行前重新核验计划内容 hash、全部输入文件 SHA、task/profile/scene/source/evaluation 的绑定语义与闭包完整性；不通过抛异常。

仅支持 `connector_keyed_v1` 注册模板。robot、scene、frames、evaluation 与模板完全一致；对象除初始 pose 外所有字段一致，平移改变量不超过 50 mm、旋转角不超过 30°。这只是当前专家的接入边界，不保证边界内的每个姿态都能完成动作。新增物体、几何尺度、质量、障碍、机器人、功能 frame 或成功规则须注册兼容专家，不能静默忽略。

`demo_path` 必须显式指定项目内注册的 `data/reference/connector/converted/episode_000.npz`。它是保留的参考轨迹，**不是本轮模型视频分析新生成的动作**。NPZ 需符合 555 帧、30 Hz、左右关节/夹爪与刚体位姿格式；经配置的 `data_python` 只读解析 task spec 绑定的实体导入数据，逐项比较 episode 0 的反馈关节、宽度、时间。conversion.json 的人工 RGB/尺寸/水平假设与夹指注册仍是未验证物体位姿，不能因反馈谱系相符就升级为度量真值。源转换文件、导入 manifest、相关 Parquet 和元数据均冻结 hash。

必须有成功 scene validation，且 task hash/revision、manifest 和实际 USD 层/资源 SHA 闭包一致。旧报告缺少 `artifact_sha256` 时需独立 CPU 复审；不能在规划时重新给已被修改的场景打 hash 后冒充先前已验证。计划中的 `input_hashes` 包含场景闭包、任务/证据/配置、专家及机构代码和源数据。

计划声明 `binding_scope=offline_contact_expert_only`、`expert_perception=simulator_ground_truth`、`learned_policy=false`、`hardware_commands_sent=0`。这是一条带特权位姿的脚本专家诊断，不是 RGBD 学生泛化或真机验证。原 task spec 的 `contact_execution_authorized=false` 保持不变，不借仿真执行授予硬件权限。

执行器应真正打开 `scene_path`，以 `spec.initial_joint_positions` 初始化机器人并加载场景中已落稳物体；调用保留专家时显式传 demo 且 `reset=False`。不允许执行期重置成历史默认位姿。夹指 force drive/摩擦/接触、物体被动阻尼等改变列在 `execution_overlay`，由执行器记录实际应用值和导出证据；不能称执行物理与 preview 完全相同。

`evaluation_rules` 分开绑定动作与保持，保留 CONN-006。动作/夹爪打开完成不代表释放后双卡扣保持通过；独立评价器负责最终机械判据，本模块不放宽标准。

CPU 测试位于 `tests/planning/`。需要真实任务/场景工件的测试在工件缺失时明确 skip，不以合成 JSON 测试代替 Isaac 物理验收。
