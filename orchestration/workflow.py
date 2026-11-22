"""Task DAG. These are registered capabilities, never arbitrary user code."""
from contracts.analysis_agents import DISPATCH_STAGE, SPECIALISTS, SYNTHESIS_STAGE, validate_assignments


STAGES = [
 {'id':'demo_import','role':'数据导入','deps':[]},
 {'id':'data_audit','role':'数据审计','deps':['demo_import']},
 {'id':'experience','role':'历史经验检索','deps':['demo_import']},
 {'id':'geometry','role':'资产与功能几何审查','deps':['demo_import','experience']},
 {'id':DISPATCH_STAGE,'role':'总控 Agent · 派工','kind':'llm','deps':['data_audit','experience','geometry']},
 {'id':SYNTHESIS_STAGE,'role':'总控 Agent · 汇总','kind':'llm','deps':[DISPATCH_STAGE]},
 {'id':'task_spec','role':'结构化任务定义','deps':['task_analysis','geometry']},
 {'id':'scene_layout','role':'多视角场景估算','kind':'llm','deps':['task_spec','data_audit']},
 {'id':'functional_geometry','role':'功能几何证据审查','deps':['task_spec','scene_layout','experience','geometry']},
 {'id':'scene_build','role':'场景构建与预览','deps':['task_spec','functional_geometry']},
 {'id':'scene_validation','role':'场景结构与局部物理检查','deps':['scene_build']},
 {'id':'simulation','role':'动作专家学习与位置泛化测试','deps':['scene_validation']},
 {'id':'learning','role':'泛化数据与策略训练','deps':['simulation']},
 {'id':'evaluation','role':'独立验收','deps':['learning']},
 {'id':'deployment','role':'离线策略包归档','deps':['evaluation']},
]


def delegated_stages(result, count=None):
    assignments = validate_assignments(result["plan"], count)
    return [
        {"id": item["role"], "role": SPECIALISTS[item["role"]][0], "kind": "llm", "deps": [DISPATCH_STAGE]}
        for item in assignments
    ]
