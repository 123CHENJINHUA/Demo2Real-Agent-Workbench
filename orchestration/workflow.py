"""Task DAG. These are registered capabilities, never arbitrary user code."""
STAGES = [
 {'id':'demo_import','role':'数据导入','deps':[]},
 {'id':'data_audit','role':'数据审计','deps':['demo_import']},
 {'id':'experience','role':'历史经验检索','deps':['demo_import']},
 {'id':'geometry','role':'资产与功能几何审查','deps':['demo_import','experience']},
 {'id':'task_analysis','role':'任务语义分析','kind':'llm','deps':['data_audit','experience','geometry']},
 {'id':'task_spec','role':'结构化任务定义','deps':['task_analysis','geometry']},
 {'id':'functional_geometry','role':'功能几何证据审查','deps':['task_spec','experience','geometry']},
 {'id':'scene_build','role':'场景构建与预览','deps':['task_spec','functional_geometry']},
 {'id':'scene_validation','role':'场景结构与局部物理检查','deps':['scene_build']},
 {'id':'simulation','role':'接触专家验证','deps':['scene_validation']},
 {'id':'learning','role':'泛化数据与策略训练','deps':['simulation']},
 {'id':'evaluation','role':'独立验收','deps':['learning']},
 {'id':'deployment','role':'部署包检查','deps':['evaluation']},
]
