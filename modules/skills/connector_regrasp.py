"""Execute a preflighted contact-centre recovery skill through simulator ports."""
from pathlib import Path
import json,numpy as np
from modules.planning.regrasp import plan_regrasp,hand_geometry

def execute(session,arm,nominal,controller):
    sim=session.sim;state=sim.arms[arm];output=Path(session.output)/f'regrasp_v2_{len(session.recoveries)}_{arm}.json'
    seed=state['robot'].get_joint_positions(joint_indices=state['indices']);sample=session.observe();root=Path(__file__).resolve().parents[2]
    try:
        paths,report=plan_regrasp(sim.ik,sim.bases[arm],arm,seed,sim.tool(arm),nominal,controller.start_tools[arm],hand_geometry(root),sim.table_height,sample['poses'][1-arm])
    except RuntimeError as exc:
        session.needs_human=True;report={'passed':False,'error':str(exc),'attempts':getattr(exc,'attempts',[]),'motion_started':False};output.write_text(json.dumps(report,indent=2));raise
    output.write_text(json.dumps(report,indent=2));sim.follow_joint_path(arm,paths['approach']);sim.follow_joint_path(arm,paths['descend']);state['opening']=0.;sim.wait(1.3);sim.follow_joint_path(arm,paths['lift']);sim.wait(.3)
    sample=session.observe();gap=float(-np.diff(state['robot'].get_joint_positions(joint_indices=state['fingers']))[0])
    report['physical_grasp']={'visible':bool(sample['visible'][arm]),'jaw_gap_m':gap,'object_height_m':float(sample['poses'][arm,2,3]-sim.table_height)}
    report['physical_grasp']['passed']=bool(sample['visible'][arm] and .008<gap<.035 and sample['poses'][arm,2,3]>sim.table_height+.04);output.write_text(json.dumps(report,indent=2))
    if not report['physical_grasp']['passed']:raise RuntimeError('Preflighted regrasp lacks physical lift/jaw confirmation')
    sim.follow_joint_path(arm,paths['return'])
    return report
