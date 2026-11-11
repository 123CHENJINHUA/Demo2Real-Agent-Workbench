# 任务分析

`run(ctx)` 接收导入清单及可选 `semantic_model_result`。没有真实模型输出时只报告元信息事实，并返回 needs_configuration/needs_multimodal_analysis，绝不根据目录名生成动作语义。模型由编排 provider 调用，不在本模块保存凭据。

`MODEL_INSTRUCTION` 和 `validate_proposal` 给出受限 JSON 提案契约。提案必须含任务、物体、阶段/证据、成功条件、缺项、资产和置信度；输出始终是待验证提案，不自动冻结验收，不执行模型生成代码。
