import type {RunEvent,RunTask} from './runActivity';

export type CollaborationTask = RunTask & {
  finished_at?:number;
  result?:{
    plan?:{assignments:{role:string;focus:string}[]};
    plan_path?:string;
    report?:{summary:string};
  };
};

export type CollaborationEvent = RunEvent & {payload?:{
  exchange_id?:string; sender?:string; recipient?:string; request?:string; summary?:string;
  status?:string; evidence_path?:string|null; report_path?:string|null;
}};

export type AgentExchange = {
  id:string; taskId?:string; at:number; sender:string; recipient:string; request:string;
  summary?:string; status:string; evidencePath?:string|null; reportPath?:string|null;
};

const stageActors:Record<string,string> = {
  agent_dispatch:'总控 Agent',task_analysis:'总控 Agent',scene_layout:'场景估算 Agent',
  simulation:'动作专家 Agent',
};

export const agentName = (task:RunTask) => stageActors[task.stage] || task.role;

export function agentExchanges(tasks:CollaborationTask[], events:CollaborationEvent[]) {
  const byId = new Map(tasks.map(task=>[task.id,task]));
  const exchanges = new Map<string,AgentExchange>();
  const active = new Map<string,AgentExchange>();
  for (const event of events) {
    const {type,payload,task_id,created_at}=event;
    const isStart=type==='agent.handoff_started';
    if (!isStart && type!=='agent.handoff_finished') continue;
    if (!payload?.exchange_id || !payload.sender || !payload.recipient || !payload.request) continue;
    const task=task_id ? byId.get(task_id) : undefined;
    const running=!!task && task.status==='running' && created_at>=(task.started_at??Infinity);
    const entry={
      id:payload.exchange_id,taskId:task_id,at:created_at,sender:payload.sender,recipient:payload.recipient,
      request:payload.request,summary:payload.summary,evidencePath:payload.evidence_path,reportPath:payload.report_path,
      status:isStart ? (running?'running':task?.status==='cancelled'?'cancelled':'interrupted') : (payload.status||'interrupted'),
    };
    const previous=exchanges.get(entry.id);
    if (!previous || previous.at<=entry.at) exchanges.set(entry.id,entry);
  }
  for (const entry of exchanges.values()) {
    if (entry.status!=='running' || !entry.taskId) continue;
    const current=active.get(entry.taskId);
    if (!current || current.at<=entry.at) active.set(entry.taskId,entry);
  }

  const dispatch=tasks.find(task=>task.stage==='agent_dispatch'&&['succeeded','completed'].includes(task.status));
  for (const assignment of dispatch?.result?.plan?.assignments ?? []) {
    const specialist=tasks.find(task=>task.stage===assignment.role);
    if (!specialist || !assignment.focus) continue;
    const id='assignment-'+specialist.id;
    const report=specialist.result?.report;
    const status=report ? 'succeeded' : ['queued','pending'].includes(specialist.status)?'assigned':specialist.status;
    exchanges.set(id,{
      id,taskId:specialist.id,at: specialist.finished_at ?? specialist.started_at ?? dispatch?.finished_at ?? 0,
      sender:'总控 Agent',recipient:agentName(specialist),request:assignment.focus,
      status,summary:report?.summary || specialist.error || specialist.result?.reason,
      evidencePath:dispatch?.result?.plan_path,reportPath:report?specialist.result?.report_path:undefined,
    });
  }
  const rows=[...exchanges.values()].sort((a,b)=>Number(b.status==='running')-Number(a.status==='running') || b.at-a.at);
  return {rows,active};
}
