import {Activity, ArrowUpRight, CircleAlert, Timer} from 'lucide-react';
import type {ReactNode} from 'react';
import {hasLiveHeartbeat, isTaskComplete, isTaskProblem, isTaskWaiting, taskMessage, waitingOn} from './runActivity';
import type {RunTask, TaskActivity} from './runActivity';

export default function RunProgress({tasks, artifacts, activities, connected, now, agentsVisible, onTaskScene, onConversation, renderStatus}: {
  tasks: RunTask[];
  artifacts: {id: string; path: string}[];
  activities: ReadonlyMap<string,TaskActivity>;
  connected: boolean;
  now: number;
  agentsVisible: boolean;
  onTaskScene: () => void;
  onConversation: () => void;
  renderStatus: (status: string) => ReactNode;
}) {
  if (!tasks.length) return null;
  const completed = tasks.filter(isTaskComplete).length;
  const running = tasks.filter(task=>task.status==='running');
  const live = running.filter(task=>hasLiveHeartbeat(task,connected,now));
  const waiting = tasks.filter(isTaskWaiting);
  const problems = tasks.filter(task=>isTaskProblem(task) &&
    (task.status!=='blocked' || !waitingOn(task,tasks).some(isTaskProblem)));
  const next = waiting.find(task=>!waitingOn(task,tasks).length) ?? waiting[0];
  const shown = running.length
    ? (agentsVisible?[]:running)
    : problems.length ? problems : next ? [next] : [];
  const stageWidth = (count: number) => ({width:100*count/tasks.length+'%'});

  return <section className="run-progress" aria-label="运行进度">
    <div className="run-progress-heading">
      <h2><Activity size={18}/>运行进度</h2>
      <div className="run-progress-counts" aria-live="polite">
        <span><strong>{completed} / {tasks.length}</strong> 阶段完成</span>
        {running.length>0&&<span className={live.length?'progress-live-label':''}>
          {live.length===running.length?running.length+' 个执行中':'等待状态更新'}
        </span>}
      </div>
    </div>
    <div className="run-progress-track" role="progressbar" aria-label="已完成阶段"
      aria-valuemin={0} aria-valuenow={completed} aria-valuemax={tasks.length}
      aria-valuetext={completed+' / '+tasks.length+' 阶段完成'}>
      <span className="run-progress-completed" style={stageWidth(completed)}/>
      {live.length>0&&<span aria-hidden="true" className="run-progress-moving" style={stageWidth(live.length)}/>}
    </div>
    {shown.length>0&&<ul className="run-progress-tasks">{shown.map(task=>{
      const active = live.includes(task);
      const activity = activities.get(task.id);
      const count = active ? activity?.count : undefined;
      const report = isTaskProblem(task) && artifacts.find(item=>item.path===task.result?.report_path);
      const diagnosis = isTaskProblem(task) && artifacts.find(item=>item.path===task.result?.diagnosis_report_path && item.path!==task.result?.report_path);
      const recheck = artifacts.find(item=>item.path===task.result?.scene_recheck_result?.repair_report_path);
      const supervision = artifacts.find(item=>item.path===task.result?.supervision_report_path);
      const needsGeometry = task.stage==='functional_geometry' && isTaskProblem(task);
      return <li key={task.id} className="run-work-card" data-stage={task.stage} data-active={active}>
        <span className="run-work-icon" aria-hidden="true">{isTaskProblem(task)?<CircleAlert size={15}/>:<Timer size={15}/>}</span>
        <div className="run-work-description">
          <strong>{task.role}</strong>
          <p>{taskMessage(task,tasks,activity,active)}</p>
          {task.result?.supervision_summary&&<p>总控 Agent · {task.result.supervision_summary}</p>}
          {count&&<div className="run-work-count"><progress value={count.done} max={count.total} aria-label={task.role+'进度'}/>
            <span>{count.done} / {count.total} {count.unit}</span></div>}
          {(needsGeometry||report||diagnosis||recheck||supervision||task.status==='waiting_input')&&<div className="run-work-links">
            {task.status==='waiting_input'&&<button type="button" onClick={onConversation}>回复 Agent</button>}
            {needsGeometry&&<button type="button" onClick={onTaskScene}>打开任务定义</button>}
            {report&&<a href={'/api/artifacts/'+report.id} target="_blank" rel="noreferrer">{report.path===task.result?.diagnosis_report_path?'查看 Agent 诊断':'查看报告'}<ArrowUpRight size={12}/></a>}
            {diagnosis&&<a href={'/api/artifacts/'+diagnosis.id} target="_blank" rel="noreferrer">{task.result?.diagnosis_agent||'Agent'}诊断<ArrowUpRight size={12}/></a>}
            {recheck&&<a href={'/api/artifacts/'+recheck.id} target="_blank" rel="noreferrer">查看场景回查<ArrowUpRight size={12}/></a>}
            {supervision&&<a href={'/api/artifacts/'+supervision.id} target="_blank" rel="noreferrer">查看总控处理记录<ArrowUpRight size={12}/></a>}
          </div>}
        </div>
        {renderStatus(task.status)}
      </li>;
    })}</ul>}
  </section>;
}
