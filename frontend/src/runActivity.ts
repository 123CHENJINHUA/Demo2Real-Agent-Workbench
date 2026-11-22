export interface StageProgress {
  phase?: string;
  message?: string;
  completed?: number;
  total?: number;
  unit?: string;
  current_file?: string;
  image_count?: number;
  supervision?: boolean;
  request?: string;
  summary?: string;
}

export interface RunTask {
  id: string;
  stage: string;
  role: string;
  kind: string;
  status: string;
  deps: string[];
  started_at?: number;
  heartbeat_at?: number;
  progress?: StageProgress;
  error?: string;
  result?: {reason?: string; message?: string; report_path?: string; issues?: StageIssue[]; diagnosis_report_path?:string;
    diagnosis_agent?:string; scene_recheck_result?:{repair_report_path?:string};
    supervision_report_path?:string; supervision_summary?:string};
}

interface StageIssue {severity?: string; code?: string; message?: string; asset_id?: string}

export interface RunEvent {
  id: number;
  task_id?: string;
  type: string;
  created_at: number;
  payload?: StageProgress & {event_type?: string; item_type?: string; epoch?: number; epochs?: number; recorded_image_count?: number};
}

export interface TaskActivity {
  text: string;
  count?: {done: number; total: number; unit: string};
}

const phaseLabels: Record<string,string> = {
  preparing_model_input: '正在整理示教帧与任务证据',
  requesting_model: '正在提交模型请求',
  validating_model_output: '正在校验模型输出并整理报告',
};
const progressUnits: Record<string,string> = {files:'个文件', assets:'项资产', frames:'帧', trials:'次试验', epochs:'轮'};
const completedStates = new Set(['succeeded','completed']);
const problemStates = new Set(['failed','blocked','needs_configuration','waiting_input','interrupted']);
export const isTaskComplete = (task: RunTask) => completedStates.has(task.status);
export const isTaskWaiting = (task: RunTask) => ['queued','pending'].includes(task.status);
export const isTaskProblem = (task: RunTask) => problemStates.has(task.status);

export function hasLiveHeartbeat(task: RunTask, connected: boolean, now: number) {
  const heartbeat = task.heartbeat_at ?? task.started_at;
  return connected && task.status==='running' && typeof heartbeat==='number' && now-heartbeat<12;
}

function countProgress(progress: StageProgress): TaskActivity['count'] {
  const {completed,total,unit} = progress;
  if (typeof completed!=='number' || typeof total!=='number' || !Number.isFinite(completed) ||
    !Number.isFinite(total) || completed<0 || total<=0 || completed>total) return undefined;
  return {done:completed, total, unit:unit ? progressUnits[unit] ?? unit : '项'};
}

function progressActivity(progress: StageProgress): TaskActivity | undefined {
  const count = countProgress(progress);
  const phase = progress.phase ? phaseLabels[progress.phase] : undefined;
  const file = typeof progress.current_file==='string' ? progress.current_file.split('/').at(-1) : undefined;
  const text = phase
    ? phase+(progress.phase==='requesting_model'&&progress.image_count ? ' · '+progress.image_count+' 张示教帧' : '')
    : file ? '处理中 · '+file : progress.message;
  return text || count ? {text:text || '正在处理',count} : undefined;
}

function describeEvent(event: RunEvent): TaskActivity | undefined {
  const data = event.payload ?? {};
  if (event.type==='agent.handoff_started' && data.request) return {text:data.request};
  if (event.type==='agent.handoff_finished' && data.summary) return {text:data.summary};
  if (data.supervision && event.type.startsWith('model.')) return {text:'总控 Agent 正在分析失败证据'};
  if (event.type==='supervisor.decision' && data.message) return {text:'总控 Agent · '+data.message};
  if (event.type==='progress' || event.type==='task.progress') return progressActivity(data);
  if (event.type==='model.requested') return progressActivity({phase:'requesting_model', image_count:data.recorded_image_count ?? data.image_count});
  if (event.type==='model.codex_started') return {text:'Codex 已启动，等待模型响应'};
  if (event.type==='model.waiting_evidence') return {text:'缺少可供分析的示教帧'};
  if (event.type==='model.codex_event') {
    if (data.event_type==='turn.started') return {text:'模型正在分析'};
    if (data.event_type==='turn.failed' || data.event_type==='error') return {text:'模型返回错误'};
    if (data.event_type==='turn.completed' || (data.event_type==='item.completed'&&data.item_type==='agent_message'))
      return {text:'已收到模型回复，等待结果校验'};
  }
  if (event.type==='learning.progress') return progressActivity({message:'正在训练',completed:data.epoch,total:data.epochs,unit:'epochs'});
  if (event.type==='phase' && data.phase) return {text:'仿真阶段 · '+data.phase};
  if (event.type==='expert.phase' && data.message) return {text:data.message.slice(0,180)};
  if (event.type==='scene.heartbeat') return {text:'Isaac 场景子进程在运行'};
  if (event.type==='expert.heartbeat' || event.type==='simulation.heartbeat') return {text:'Isaac 仿真子进程在运行'};
}

export function taskActivities(tasks: RunTask[], events: RunEvent[]) {
  const running = new Map(tasks.filter(task=>task.status==='running').map(task=>[task.id,task]));
  const activities = new Map<string,TaskActivity>();
  const heartbeats = new Map<string,TaskActivity>();
  for (const event of [...events].reverse()) {
    if (!event.task_id || activities.has(event.task_id)) continue;
    const task = running.get(event.task_id);
    if (!task || event.created_at<(task.started_at??0)) continue;
    const activity = describeEvent(event);
    if (!activity) continue;
    if (event.type.endsWith('.heartbeat')) {
      if (!heartbeats.has(task.id)) heartbeats.set(task.id,activity);
    } else activities.set(task.id,activity);
  }
  for (const task of running.values()) {
    if (!activities.has(task.id)) {
      const activity = (task.progress && progressActivity(task.progress)) || heartbeats.get(task.id);
      if (activity) activities.set(task.id,activity);
    }
  }
  return activities;
}

export function waitingOn(task: RunTask, tasks: RunTask[]) {
  return (task.deps ?? [])
    .map(stage=>tasks.find(item=>item.stage===stage))
    .filter((item):item is RunTask=>!!item && !isTaskComplete(item));
}

export function taskMessage(task: RunTask, tasks: RunTask[], activity?: TaskActivity, live=true) {
  if (task.status==='running') return live ? activity?.text ?? '执行已开始，等待阶段反馈' : '等待运行状态更新';
  if (isTaskWaiting(task)) {
    const dependencies = waitingOn(task,tasks);
    const names = dependencies.slice(0,2).map(item=>item.role).join('、');
    return names ? '等待 '+names+(dependencies.length>2?' 等':'') : '等待可用执行资源';
  }
  if (isTaskProblem(task)) {
    const message = task.error || task.result?.reason || task.result?.message;
    if (message==='needs_multimodal_analysis') return '需要配置并运行多模态分析';
    const blocker = task.status==='blocked' ? waitingOn(task,tasks).find(isTaskProblem) : undefined;
    if (blocker) return '等待 '+blocker.role+' 恢复';
    const blocking = task.result?.issues?.filter(issue=>issue.severity==='blocking') ?? [];
    const missingModels = blocking.filter(issue=>issue.code==='mesh_missing');
    if (missingModels.length) {
      const objects = new Set(missingModels.map(issue=>issue.asset_id).filter(Boolean)).size;
      return (objects ? objects+' 个对象' : '任务对象')+'缺少模型文件或明确的几何尺寸。';
    }
    return message || blocking.find(issue=>issue.message)?.message || '查看运行记录了解原因';
  }
  if (isTaskComplete(task)) return '阶段结果已归档';
  return '';
}
