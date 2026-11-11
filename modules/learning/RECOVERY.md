# 真实仿真状态恢复学习

recovery_dataset 从已登记PhysX回合的 supervision.npz 构建数据集，保留每回合来源与SHA、失败、实际重抓验证。拒绝旧10个位置留出回合、重复回合、混用采集器源码版本、相机/力/事件特征、合成夹具以及任一分组缺少五类监督。

recovery_bc 使用训练回合的归一化与类别均衡采样训练24→48→32→5 MLP（NumPy Adam），仅根据验证回合macro F1选择权重；测试回合不参与拟合。类别均衡重复采样不会增加独立示例数，必须结合每类样本数审查结果。

默认18回合仅为第一版流程小试：每组6回合，扰动/视图代理零噪声、无延迟。训练样本少且来自规则专家，不能证明大范围泛化、实机恢复或连续神经控制。后续应扩充训练/验证扰动种子、时机、形状、位姿与感知误差，并建立新的冻结评测版本。

稳定输入/输出定义在 contracts/recovery.py；策略在 modules/policies/neural/recovery_supervisor；独立分类和闭环评价在 modules/evaluation/recovery*.py；部署模块仅导出仿真包。

入口：

```bash
.runtime/app-venv/bin/python scripts/recovery_student.py --batches <实际采集批次> --wait
.runtime/app-venv/bin/python scripts/recovery_closed_loop.py --learning-run <训练运行ID> --wait
```

闭环报告分开记录正常/遮挡/重抓后的动作完成，以及物体不可达后的人工请求。物体消失时正确终止不能写成插接完成。保留原始网络动作、观测守卫覆盖、物理重抓与独立动作报告；不会用规则标签替换学生输出。
