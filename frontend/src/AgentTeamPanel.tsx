import type {ReactNode} from 'react';
import {ArrowRight, ArrowUpRight, FileText, GitBranch, Network, Workflow} from 'lucide-react';
import {hasLiveHeartbeat, isTaskComplete, isTaskWaiting, taskMessage} from './runActivity';
import type {RunTask, TaskActivity} from './runActivity';

type Assignment = {role: string; focus: string; reason: string};
type TeamTask = RunTask & {
  result?: {
    model?: string; requested_model?: string; model_source?: string;
    plan_path?: string; report_path?: string; plan?: {assignments: Assignment[]};
    report?: {summary: string}; proposal?: {task_summary: string};
  };
};
type Artifact = {id: string; path: string};

export default function AgentTeamPanel({tasks, artifacts, activities, connected, now, compact=false, specialistCount, renderStatus}: {
  tasks: TeamTask[];
  artifacts: Artifact[];
  activities: ReadonlyMap<string,TaskActivity>;
  connected: boolean;
  now: number;
  compact?: boolean;
  specialistCount?: number;
  renderStatus: (status: string) => ReactNode;
}) {
  const agents = tasks.filter(task=>task.kind==='llm'&&task.stage!=='scene_layout');
  if (!agents.length) return null;
  const dispatch = agents.find(task=>task.stage==='agent_dispatch');
  const synthesis = agents.find(task=>task.stage==='task_analysis');
  const members = agents.filter(task=>task!==dispatch && task!==synthesis);
  const assignments = new Map(dispatch?.result?.plan?.assignments?.map(assignment=>[assignment.role,assignment]));
  const memberCount = members.filter(isTaskComplete).length;

  function card(task: TeamTask, title=task.role) {
    const result = task.result;
    const assignment = assignments.get(task.stage);
    const path = result?.plan_path || result?.report_path;
    const artifact = path && artifacts.find(item=>item.path===path);
    const summary = result?.report?.summary || result?.proposal?.task_summary;
    const requestedModel = result?.model_source==='not_reported' && result.requested_model;
    const model = requestedModel || result?.model;
    const active = hasLiveHeartbeat(task,connected,now);
    const current = taskMessage(task,tasks,activities.get(task.id),active);
    return <article className="agent-team-card" key={task.id} data-stage={task.stage} data-active={active} data-status={task.status}>
      <div className="agent-team-card-heading">
        <span className="agent-team-icon" aria-hidden="true">{task===dispatch||task===synthesis?<GitBranch size={16}/>:<Workflow size={16}/>}</span>
        <h3>{title}</h3>
        {renderStatus(task.status)}
      </div>
      {current&&<p className="agent-team-current">{current}</p>}
      {assignment&&<p className="agent-team-assignment">{assignment.focus}</p>}
      {!compact&&(assignment?.reason||summary||model||artifact)&&<details className="agent-team-evidence">
        <summary>分析详情与产物</summary>
        {assignment?.reason&&<p>派工依据 · {assignment.reason}</p>}
        {summary&&<p>{summary}</p>}
        {model&&<small>{requestedModel?'请求模型':'模型'} · {model}</small>}
        {artifact&&<a href={'/api/artifacts/'+artifact.id} target="_blank" rel="noreferrer">
          <FileText size={14}/>{task===dispatch?'派工单':task===synthesis?'任务提案':'专责报告'}<ArrowUpRight size={13}/>
        </a>}
      </details>}
    </article>;
  }

  return <section className={'agent-team'+(compact?' agent-team-compact':'')} aria-label="多 Agent 分析">
    <div className="agent-team-heading">
      <Network size={19}/><h2>{dispatch?'多 Agent 分析':'模型 Agent'}</h2>
      {dispatch&&<span>{members.length ? '专责报告 '+memberCount+' / '+members.length : specialistCount ? '计划 '+specialistCount+' 个专责 Agent' : '自动派工'}</span>}
    </div>
    {dispatch ? <div className="agent-team-flow">
      <div className="agent-team-lane"><span>派工</span>{card(dispatch,'总控 Agent')}</div>
      <ArrowRight className="agent-team-arrow" size={16} aria-hidden="true"/>
      <div className="agent-team-lane agent-team-specialists">
        <span>专责分析</span>
        {members.length>0 ? <div className="agent-team-members">{members.map(task=>card(task))}</div>
          : <p className="agent-team-unassigned">{dispatch.status==='running'||isTaskWaiting(dispatch)?'等待总控分配任务':'尚无已分配角色'}</p>}
      </div>
      {synthesis&&<>
        <ArrowRight className="agent-team-arrow" size={16} aria-hidden="true"/>
        <div className="agent-team-lane"><span>汇总</span>{card(synthesis,'总控 Agent')}</div>
      </>}
    </div> : <div className="agent-team-members">{agents.map(task=>card(task))}</div>}
  </section>;
}
