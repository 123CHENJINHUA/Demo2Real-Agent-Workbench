import {Activity, ArrowRight, ArrowUpRight, Network} from 'lucide-react';
import {agentExchanges,agentName} from './agentCollaboration';
import type {CollaborationEvent,CollaborationTask} from './agentCollaboration';
import {hasLiveHeartbeat,taskMessage} from './runActivity';
import type {TaskActivity} from './runActivity';

const statusText:Record<string,string> = {
  assigned:'已派工',running:'处理中',succeeded:'已返回',completed:'已返回',failed:'未通过',
  blocked:'待解决',needs_configuration:'待配置',needs_multimodal_analysis:'待配置',
  waiting_input:'等待回复',cancelled:'已取消',interrupted:'已中断',
};
const timeFormat=new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});

export default function AgentCollaborationPanel({tasks,events,artifacts,activities,connected,now,onShowAll}:{
  tasks:CollaborationTask[];
  events:CollaborationEvent[];
  artifacts:{id:string;path:string}[];
  activities:ReadonlyMap<string,TaskActivity>;
  connected:boolean;
  now:number;
  onShowAll?:()=>void;
}){
  const {rows,active}=agentExchanges(tasks,events);
  const running=tasks.filter(task=>task.status==='running');
  const live=running.filter(task=>hasLiveHeartbeat(task,connected,now)).length;
  const display=onShowAll?rows.slice(0,3):rows;
  if (!tasks.length) return null;
  return <section className={'agent-collaboration'+(onShowAll?' agent-collaboration-compact':'')} aria-label="Agent 协作">
    <header className="agent-collaboration-heading">
      <h2><Network size={19}/>Agent 协作</h2>
      <span aria-live="polite">{live ? `${live} 个执行中` : running.length ? '等待状态更新' : '暂无 Agent 执行中'}</span>
    </header>
    {running.length>0&&<ul className="agent-collaboration-active" aria-label="当前执行">
      {running.map(task=>{
        const exchange=active.get(task.id);
        const online=hasLiveHeartbeat(task,connected,now);
        const progress=activities.get(task.id);
        const count=online?progress?.count:undefined;
        return <li key={task.id} data-live={online}>
          <span className="agent-collaboration-actor" aria-hidden="true"><Activity size={16}/></span>
          <div><strong>{exchange?.recipient||agentName(task)}</strong>
           <p>{taskMessage(task,tasks,progress,online)}</p>
           {count&&<span className="agent-collaboration-count">
            <progress value={count.done} max={count.total} aria-label={agentName(task)+'进度'}/>
            {count.done} / {count.total} {count.unit}
           </span>}</div>
        </li>;
      })}
    </ul>}
    {rows.length>0?<ol className="agent-collaboration-rows" aria-label="交接记录">
      {display.map(row=>{
        const report=artifacts.find(file=>file.path===row.reportPath);
        const evidence=artifacts.find(file=>file.path===row.evidencePath && file.id!==report?.id);
        const online=running.some(task=>task.id===row.taskId&&hasLiveHeartbeat(task,connected,now));
        const current=row.taskId?active.get(row.taskId):undefined;
        const awaiting=row.status==='running'&&current&&current.id!==row.id;
        return <li key={row.id} data-live={row.status==='running'&&online&&!awaiting}>
          <div className="agent-collaboration-edge">
            <strong>{row.sender}</strong><ArrowRight size={15} aria-label="交给"/><strong>{row.recipient}</strong>
            <span data-status={row.status}>{row.status==='running'&&!online?'等待状态更新':
              awaiting?'等待交接结果':statusText[row.status]||'待核对'}</span>
            {!!row.at&&<time dateTime={new Date(row.at*1000).toISOString()}>{timeFormat.format(row.at*1000)}</time>}
          </div>
          <p>{row.request}</p>
          {row.summary&&<p className="agent-collaboration-result">返回 · {row.summary}</p>}
          {(report||evidence)&&<div className="agent-collaboration-links">
            {evidence&&<a href={'/api/artifacts/'+evidence.id} target="_blank" rel="noreferrer">
              查看交接依据<ArrowUpRight size={13}/></a>}
            {report&&<a href={'/api/artifacts/'+report.id} target="_blank" rel="noreferrer">
              查看结果<ArrowUpRight size={13}/></a>}
          </div>}
        </li>;
      })}
    </ol>:!running.length&&<p className="agent-collaboration-empty">暂无协作记录</p>}
    {onShowAll&&(rows.length>3||display.some(row=>row.request.length>100||(row.summary?.length??0)>100))&&<button className="button secondary small" type="button" onClick={onShowAll}>
      查看全部协作<ArrowRight size={14}/>
    </button>}
  </section>;
}
