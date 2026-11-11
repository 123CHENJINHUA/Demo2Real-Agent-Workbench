# 学习模块

首版提供真实NumPy高层BC：8→32→5 MLP，episode级训练/验证/测试分割，验证集选权重，保留测试集。NPZ需features/labels/episode_ids/split/metadata，metadata需features列表、observation_source=camera_estimate、dependency_signature。特征不能包含仿真真值或扰动标签；标签可由专家生成。

该模型只学习技能分类，不是双臂连续策略，不代表任务已泛化。需要额外闭环、扰动、墙钟时延、硬件范围评价；无有效本任务数据时明确待配置。
