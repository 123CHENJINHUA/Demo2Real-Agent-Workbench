# 任务分析

`run(ctx)` 接收导入清单及可选 `semantic_model_result`。没有真实模型输出时只报告元信息事实，并返回 needs_configuration/needs_multimodal_analysis，绝不根据目录名生成动作语义。模型由编排 provider 调用，不在本模块保存凭据。

`MODEL_INSTRUCTION` 和 `validate_proposal` 给出受限 JSON 提案契约。提案必须含任务、物体、阶段/证据、成功条件、缺项、资产和置信度；输出始终是待验证提案，不自动冻结验收，不执行模型生成代码。

新示教运行按以下步骤分析。

1. 总控读取采样帧与数据、经验和资产审查结果，输出 `agent_dispatch/agent_plan.json`。派工需要示教与动作、验收与风险两个角色。总控可按输入增加几何与接触、观测与标定两个角色。
2. 调度器登记获选角色，每个专责 Agent 独立调用项目配置的模型服务，并输出本角色的 `specialist_report.json`。每条图像引用须来自此次输入。
3. 所有专责报告成功后，总控复核各报告，将分歧与验证缺口写进原有 `task_analysis/task_proposal.json`。文件包含派工单及成员报告的引用，供任务定义阶段追溯。

派工与报告契约位于 `contracts/analysis_agents.py`，模型调用由 `application/analysis_agents.py` 绑定。已完成的派工和报告在重试时保留。此前创建的运行继续沿用其保存的依赖图。
