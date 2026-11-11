from pathlib import Path
from contracts.policies import OBSERVATION, SUPERVISOR_ACTION, MOTION_ACTION

def list_policies(root):
    return [
      dict(id="rule_supervisor_v1",role="supervisor",kind="scripted",observation_contract=OBSERVATION,action_contract=SUPERVISOR_ACTION,available=True,hardware_verified=False,detail="相机估计与机器人状态驱动；先检查可见性、时效和持物变化，输出技能建议"),
      dict(id="state_mlp_v1",role="supervisor",kind="neural",observation_contract=OBSERVATION,action_contract=SUPERVISOR_ACTION,available=False,hardware_verified=False,detail="可训练/加载8维状态MLP；需要本任务标签数据和独立评价，未默认安装任何已验证权重"),
      dict(id="demo_motion_v1",role="motion",kind="scripted",observation_contract=OBSERVATION,action_contract=MOTION_ACTION,available=True,hardware_verified=False,detail="带时间的示教关节参考提议；需提供本任务轨迹，执行层负责限幅与过期拒绝"),
      dict(id="local_motion_v1",role="motion",kind="extension",observation_contract=OBSERVATION,action_contract=MOTION_ACTION,available=False,hardware_verified=False,detail="预留连续运动网络接口；高层MLP不能替代此角色")]
