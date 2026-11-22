import {ArrowUpRight, ListOrdered} from 'lucide-react';

type Understanding = {task_summary?:string; phases?:{name:string}[]};
type Program = {strategy?:string; phases?:{name:string; instruction:string}[]};
type AnalysisStage = {id:string; stage:string; status:string; started_at?:number; result?:{
 proposal?:Understanding; understanding?:Understanding; report_path?:string; plan_path?:string;
 action_program?:Program; action_program_path?:string;
}};
type AnalysisEvent = {type:string; task_id?:string; created_at:number; payload?:{program?:Program}};

export default function TaskUnderstanding({tasks,events,artifacts,onReplay}:{
 tasks:AnalysisStage[];
 events:AnalysisEvent[];
 artifacts:{id:string;path:string}[];
 onReplay?:()=>void;
}){
 const dispatch=tasks.find(task=>task.stage==='agent_dispatch');
 const synthesis=tasks.find(task=>task.stage==='task_analysis');
 const simulation=tasks.find(task=>task.stage==='simulation');
 if(!dispatch&&!synthesis)return null;
 const proposal=synthesis?.result?.proposal;
 const choice=proposal?.task_summary ? {understanding:proposal, label:'总控汇总', path:synthesis?.result?.report_path}
  : {understanding:dispatch?.result?.understanding, label:'总控初步理解', path:dispatch?.result?.plan_path};
 const {understanding}=choice;
 const analyzing=dispatch?.status==='running'||synthesis?.status==='running';
 const artifact=artifacts.find(item=>item.path===choice.path);
 const published=[...events].reverse().find(event=>event.type==='expert.plan_ready'&&event.task_id===simulation?.id
   && event.created_at>=(simulation?.started_at??0));
 const program:Program|undefined=simulation?.result?.action_program || published?.payload?.program;
 const planArtifact=artifacts.find(item=>item.path===simulation?.result?.action_program_path);

 return <section className="task-understanding" aria-label="任务总览">
  <div className="task-understanding-heading"><h2><ListOrdered size={18}/>任务总览</h2>
   <div className="task-understanding-actions">
    {understanding?.task_summary&&<span>{choice.label}</span>}
    {onReplay&&<button type="button" className="button secondary small" onClick={onReplay}>查看专家训练</button>}
   </div>
  </div>
  {understanding?.task_summary ? <>
   <h3>任务目标</h3><p>{understanding.task_summary}</p>
   {!!understanding.phases?.length&&<><h3>动作步骤</h3><ol>{understanding.phases.map((item,index)=><li key={index}>{item.name}</li>)}</ol></>}
  </> : <p className="task-understanding-status" role="status">{analyzing?'总控正在分析示教':'总控尚未输出任务目标和步骤'}</p>}
  {artifact&&<a href={'/api/artifacts/'+artifact.id} target="_blank" rel="noreferrer">查看推理记录<ArrowUpRight size={13}/></a>}
  {program?.phases?.length&&<details className="task-action-program">
   <summary>动作专家计划</summary>
   {program.strategy&&<p>{program.strategy}</p>}
   <ol>{program.phases.map((item,index)=><li key={index}><strong>{item.name}</strong> {item.instruction}</li>)}</ol>
   {planArtifact&&<a href={'/api/artifacts/'+planArtifact.id} target="_blank" rel="noreferrer">查看动作计划<ArrowUpRight size={13}/></a>}
  </details>}
 </section>;
}
