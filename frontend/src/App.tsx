import PhaseRecoveryPanel from './PhaseRecoveryPanel';
import RecoveryLearningPanel from './RecoveryLearningPanel';
import {useEffect,useState,useCallback,useRef} from 'react';
import type {ReactNode,FormEvent} from 'react';
import {Activity,ArrowLeft,ArrowRight,ArrowUpRight,Box,Check,CheckCircle2,ChevronDown,ChevronRight,Copy,Database,FileArchive,FileText,Folder,FolderOpen,GitBranch,Image,Layers3,LoaderCircle,MessageCircle,Play,Plus,RefreshCw,Search,Settings2,Square,Terminal,TriangleAlert,Waypoints,X} from 'lucide-react';
import {api,ApiError,post,put,collection} from './api';
import {buildDiagnosticPayload,defaultDiagnosticOptions} from './diagnostics';
import type {DiagnosticOptions} from './diagnostics';
import {selectLatestSimulationPanel,getSimulationValidationNotices} from './simulationPreviews';
import ModelProviderSettings from './ModelProviderSettings';
import TaskScenePanel from './TaskScenePanel';
import ScenePreviewPanel from './ScenePreviewPanel';
import ExpertRunPanel from './ExpertRunPanel';
import Knowledge from './Knowledge';
import AgentTeamPanel from './AgentTeamPanel';
import AgentCollaborationPanel from './AgentCollaborationPanel';
import RunProgress from './RunProgress';
import AgentConversation from './AgentConversation';
import TaskUnderstanding from './TaskUnderstanding';
import ActionReplayPanel from './ActionReplayPanel';
import type {AgentQuestion} from './AgentConversation';
import {isTaskProblem,taskActivities,taskMessage} from './runActivity';
import RunGallery, {isActiveRun} from './RunGallery';
import type {RunPreview,RunSummary} from './RunGallery';

type Page='overview'|'knowledge'|'policies'|'settings';
const statusText:Record<string,string>={pending:'等待执行',queued:'已排队',running:'执行中',completed:'已完成',succeeded:'已完成',failed:'失败',cancelled:'已取消',canceled:'已取消',blocked:'等待条件',waiting_input:'等待回复',needs_configuration:'待配置',interrupted:'已中断',not_started:'尚未开始',ready:'就绪',unconfigured:'待配置',disconnected:'未连接',offline:'离线',idle:'空闲',partially_verified:'局部验证',open:'未解决',mitigation_recorded:'已有缓解记录',verified_in_scope:'范围内验证'};
const label=(s?:string)=>statusText[s||'']||s||'未知';
const date=(v:any)=>v?new Date(typeof v==='number'&&v<1e12?v*1000:v).toLocaleString('zh-CN',{hour12:false}):'—';
const short=(s:any,n=62)=>String(s||'').length>n?String(s).slice(0,n)+'…':String(s||'');
function Status({value}:{value?:string}){return <span className={'status status-'+(value||'unknown')}><i/>{label(value)}</span>}
function Empty({title,children,icon=<FolderOpen size={28}/>}:{title:string,children?:ReactNode,icon?:ReactNode}){return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{children}</p></div>}
function ErrorBox({error}:{error?:string|null}){return error?<div className="error" role="alert"><TriangleAlert size={16}/><span>{error}</span></div>:null}
function JsonView({data}:{data:any}){return <pre className="json-view">{JSON.stringify(data,null,2)}</pre>}
const environmentFields:Record<string,string>={isaac_sim_root:'Isaac Sim 根目录',isaac_lab_root:'IsaacLab 根目录',app_python:'应用 Python',data_python:'数据审查 Python',vision_python:'视觉 Python',policy_python:'策略 Python',training_python:'训练 Python',node_executable:'Node 可执行文件',cuda_home:'CUDA 根目录（可选）',ffmpeg_executable:'FFmpeg（可选）',codex_executable:'Codex CLI 可执行文件'};

const navigation = [
 {id:'overview', icon:Layers3, label:'任务工作台'},
 {id:'knowledge', icon:Database, label:'经验与失败记录'},
 {id:'policies', icon:Waypoints, label:'离线策略'},
 {id:'settings', icon:Settings2, label:'环境与模型配置'},
] as const;
const retryable = ['failed','blocked','interrupted','cancelled','needs_configuration'];

export default function App(){
 const detailPolling = useRef({cursor:0,issued:0,applied:0});
 const selectedRef = useRef<string|null>(null);
 const deletedRuns = useRef(new Set<string>());
 const [page,setPage] = useState<Page>(()=>new URLSearchParams(window.location.search).get('page')==='settings'?'settings':'overview');
 const [runs,setRuns] = useState<RunSummary[]>([]);
 const [previews,setPreviews] = useState<Record<string,RunPreview>>({});
 const [health,setHealth] = useState<any>(null);
 const [loading,setLoading] = useState(true);
 const [error,setError] = useState<string|null>(null);
 const [selected,setSelected] = useState<string|null>(()=>{
  if(page==='settings')return null;
  const id=new URLSearchParams(window.location.search).get('run');
  return id && /^[a-f0-9]{16}$/.test(id) ? id : null;
 });
 const [detail,setDetail] = useState<any>(null);
 const [detailConnected,setDetailConnected] = useState(false);
 const [events,setEvents] = useState<any[]>([]);
 const [importOpen,setImportOpen] = useState(false);
 const [editingModels,setEditingModels] = useState<ModelRun|null>(null);
 const [busy,setBusy] = useState(false);
 const [toast,setToast] = useState('');
 selectedRef.current = selected;

 const refresh = useCallback(async()=>{
  try {
   const [h, gallery] = await Promise.all([api('/health'),api<{runs:RunSummary[];previews?:Record<string,RunPreview>}>('/runs?previews=true')]);
   setHealth(h);
   setRuns(gallery.runs.filter(run=>!deletedRuns.current.has(run.id)));
   setPreviews(Object.fromEntries(Object.entries(gallery.previews ?? {}).filter(([id])=>!deletedRuns.current.has(id))));
   setError(null);
  } catch(e) {
   setError((e as Error).message);
   setHealth(null);
  } finally {
   setLoading(false);
  }
 },[]);
 const loadDetail = useCallback(async()=>{
  if(!selected) return;
  const polling=detailPolling.current;
  const ticket=++polling.issued;
  const current=()=>selectedRef.current===selected&&detailPolling.current===polling&&ticket>=polling.applied;
  try {
   const [d,e] = await Promise.all([api('/runs/'+selected),api('/runs/'+selected+'/events?tail=true&after='+polling.cursor)]);
   if(!current()) return;
   polling.applied=ticket;
   setDetail(d);
   setDetailConnected(true);
   const rows=collection(e);
   polling.cursor=Math.max(polling.cursor,e.cursor??0);
   if(rows.length) setEvents(previous=>{
    const merged = new Map(previous.map(v=>[v.id,v]));
    rows.forEach(v=>merged.set(v.id,v));
    return [...merged.values()].sort((left,right)=>left.id-right.id).slice(-500);
   });
  } catch(e) {
   if(current()) {
    polling.applied=ticket;
    setDetailConnected(false);
    if(e instanceof ApiError && e.status===404) {
     setSelected(null);
     setToast('这个运行已移出列表或不存在');
    } else setError((e as Error).message);
   }
  }
 },[selected]);
 useEffect(()=>{void refresh();const id=setInterval(refresh,4000);return()=>clearInterval(id)},[refresh]);
 useEffect(()=>{
  const url=new URL(window.location.href);
  if(page==='settings')url.searchParams.set('page','settings');
  else url.searchParams.delete('page');
  if(url.href!==window.location.href)window.history.replaceState(window.history.state,'',url);
 },[page]);
 useEffect(()=>{
  const url=new URL(window.location.href);
  if(selected)url.searchParams.set('run',selected);
  else url.searchParams.delete('run');
  if(url.href!==window.location.href)window.history.replaceState(window.history.state,'',url);
  setDetail(null);
  setDetailConnected(false);
  setEvents([]);
  detailPolling.current={cursor:0,issued:0,applied:0};
  if(!selected) return;
  void loadDetail();
  const id=setInterval(loadDetail,2500);
  return()=>clearInterval(id);
 },[selected,loadDetail]);
 useEffect(()=>{if(!toast)return;const id=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(id)},[toast]);

 function navigate(target:Page){
  setSelected(null);
  setPage(target);
  window.scrollTo(0,0);
 }
 function openRun(id:string){
  setSelected(id);
  setPage('overview');
  window.scrollTo(0,0);
 }
 async function diagnostic(kind:string,options:DiagnosticOptions){
  setBusy(true);
  try {
   const run=await post('/diagnostics',buildDiagnosticPayload(kind,options));
   openRun(run.id);
   await refresh();
   setToast('已提交离线仿真诊断');
  } catch(e) {
   setError((e as Error).message);
  } finally {
   setBusy(false);
  }
 }
 async function act(path:string){
  setBusy(true);
  try {
   await post(path);
   await refresh();
   await loadDetail();
   setToast('操作已提交');
  } catch(e) {
   setError((e as Error).message);
  } finally {
   setBusy(false);
  }
 }
 async function deleteRun(id:string){
  await api('/runs/'+encodeURIComponent(id),{method:'DELETE'});
  deletedRuns.current.add(id);
  setRuns(items=>items.filter(run=>run.id!==id));
  setPreviews(items=>{
   const remaining={...items};
   delete remaining[id];
   return remaining;
  });
  setToast('运行已从列表删除');
 }
 const active = runs.filter(isActiveRun).length;
 return <div className="app workbench-app">
  <aside className="sidebar">
   <a className="brand" href="#" aria-label="Demo2Real 首页" onClick={e=>{e.preventDefault();navigate('overview')}}>
    <img src="/mark.svg" alt="" width="38" height="38"/><span>Demo2Real</span>
   </a>
   <nav aria-label="工作台导航">{navigation.map(item=>
    <button key={item.id} className={page===item.id?'nav-item active':'nav-item'} title={item.label}
     aria-label={item.label} aria-current={page===item.id?'page':undefined} onClick={()=>navigate(item.id)}>
     <item.icon size={19}/><span>{item.label}</span>{item.id==='overview'&&active>0&&<span className="nav-count">{active}</span>}
    </button>)}</nav>
  </aside>
  <div className="workspace">
   <header className="topbar">
    <span className="workbench-connection"><i className={health?'connected':''} aria-hidden="true"/>
     {loading?'正在连接本地服务':health?'本地服务已连接':'本地服务未连接'}
    </span>
    <button className="icon-button" title="刷新状态" aria-label="刷新状态" onClick={()=>{void refresh();void loadDetail()}}><RefreshCw size={17}/></button>
   </header>
   <main>
    <ErrorBox error={error}/>
    {page==='overview'&&<>
     <div hidden={!!selected}><RunGallery runs={runs} previews={previews} loading={loading} onOpen={openRun} onImport={()=>setImportOpen(true)} onDelete={deleteRun}
      renderStatus={status=><Status value={status}/>} diagnostics={<DiagnosticPanel busy={busy} run={diagnostic}/>}/></div>
     {selected&&<RunDetail key={selected} detail={(detail?.run??detail)?.id===selected?detail:null} connected={detailConnected}
      sourceName={runs.find(run=>run.id===detail?.input?.source_run_id)?.name} events={events} busy={busy}
      cancel={()=>act('/runs/'+selected+'/cancel')} retry={()=>act('/runs/'+selected+'/retry')} close={()=>setSelected(null)}
      onOpenRun={openRun} onEditModels={()=>setEditingModels(detail?.run??detail)}
      onReply={async()=>{await Promise.all([loadDetail(),refresh()])}}/>}
    </>}
    {page==='knowledge'&&<Knowledge runs={runs} onOpenRun={openRun}/>}
    {page==='settings'&&<Settings notify={setToast}/>}
    {page==='policies'&&<OfflinePolicies/>}
   </main>
  </div>
  {importOpen&&<ImportModal close={()=>setImportOpen(false)} done={async id=>{
   setImportOpen(false);
   openRun(id);
   await refresh();
   setToast('任务已创建');
  }}/>}
  {editingModels&&<ImportModal editing={editingModels} close={()=>setEditingModels(null)} done={async()=>{
   setEditingModels(null);
   await loadDetail();
   setToast(editingModels.status==='waiting_input'?'模型选择已保存，可在 Agent 对话中继续':'模型选择已保存，可以重试');
  }}/>}
  {toast&&<div className="toast" role="status"><CheckCircle2 size={17}/>{toast}</div>}
 </div>;
}

const runTabs = [
 {id:'summary', icon:Image, label:'概览'},
 {id:'task-scene', icon:Box, label:'任务定义 / 场景'},
 {id:'action-replay', icon:Activity, label:'专家训练'},
 {id:'runtime', icon:Activity, label:'运行记录'},
 {id:'files', icon:FolderOpen, label:'产物文件'},
] as const;

function RunDetail({detail,sourceName,events,connected,busy,cancel,retry,close,onOpenRun,onEditModels,onReply}:{
 detail:any; sourceName?:string; events:any[]; connected:boolean; busy:boolean; cancel:()=>void; retry:()=>void; close:()=>void; onOpenRun:(id:string)=>void; onEditModels:()=>void;
 onReply:()=>Promise<void>;
}){
 const run = detail?.run || detail;
 const jobs = collection(detail?.tasks || detail?.jobs || detail?.stages || detail?.agents || []);
 const artifacts = collection(detail?.artifacts || []);
 const stages = jobs.some(job=>job.stage==='agent_dispatch')?jobs.filter(job=>job.kind!=='llm'||job.stage==='scene_layout'):jobs;
 const [tab,setTab] = useState<typeof runTabs[number]['id']>('summary');
 const [specOpened,setSpecOpened] = useState(false);
 const [conversation,setConversation] = useState(false);
 const questions:AgentQuestion[] = run?.questions ?? [];
 const pendingQuestions = questions.filter(question=>question.pending);
 const runId = run?.id || run?.run_id;
 const simulation = jobs.find(job=>job.stage==='simulation');
 const hasReplay = !!simulation&&(!!simulation.result?.episode_report_path||events.some(event=>
  event.type==='expert.rollouts'&&event.task_id===simulation.id&&event.created_at>=(simulation.started_at??Infinity)));
 const currentTab = tab==='action-replay'&&!hasReplay ? 'summary' : tab;
 const title = run?.input?.preview && run.name?.startsWith('场景预览 / ')
  ? ['场景预览', sourceName].filter(Boolean).join(' · ') : run?.name || '运行详情';
 const activities = taskActivities(jobs,events);
 const now = Date.now()/1000;
 const showLiveAgents = !!run && isActiveRun(run);
 const collaborationEvents=[...new Map([
  ...collection(run?.collaboration_events),...events.filter(event=>event.type?.startsWith('agent.handoff_')),
 ].map(event=>[event.id,event])).values()].sort((a,b)=>a.id-b.id);
 const collaborationProps={tasks:jobs,events:collaborationEvents,artifacts,activities,connected,now};
 const agentProps = {tasks:jobs, artifacts, activities, connected, now,
  specialistCount:run?.input?.specialist_count, renderStatus:(status:string)=><Status value={status}/>};
 function openTaskScene(){
  setSpecOpened(true);
  setTab('task-scene');
 }
 const hasVisuals = artifacts.some(artifact=>/\/(scene|panels|contact_sheet)\.(png|jpe?g)$/i.test(artifact.path || ''));
 const editable = [...retryable,'waiting_input'];
 const editModels = editable.includes(run?.status) && jobs.some(job=>job.stage==='geometry'&&editable.includes(job.status));

 return <section className="panel run-detail" aria-label="运行详情" data-run-id={runId}>
  <button className="run-back" onClick={close} aria-label="关闭运行详情"><ArrowLeft size={17}/>返回工作台</button>
  <div className="run-heading">
   <div>
    <h1>{title}</h1>
    {run&&<div className="run-heading-meta"><Status value={run.status}/><time>{date(run.created_at)}</time></div>}
   </div>
   {run&&<div className="actions">
    {run.input?.source_run_id&&<button type="button" className="button secondary small" onClick={()=>onOpenRun(run.input.source_run_id)}><GitBranch size={14}/>源任务</button>}
    <button type="button" className={'button small '+(pendingQuestions.length?'primary':'secondary')} onClick={()=>setConversation(true)}>
     <MessageCircle size={15}/>Agent 对话{pendingQuestions.length>0&&<span className="agent-question-count">{pendingQuestions.length}</span>}
    </button>
    {(isActiveRun(run)||run.status==='waiting_input')&&<button disabled={busy} className="button secondary small" onClick={cancel}><Square size={13}/>取消</button>}
    {editModels&&<button type="button" disabled={busy} className="button secondary small" onClick={onEditModels}><Box size={14}/>修改物体模型</button>}
    {retryable.includes(run.status)&&<button disabled={busy} className="button secondary small" onClick={retry}><RefreshCw size={14}/>重试</button>}
   </div>}
  </div>
  {!detail?<div className="loading"><LoaderCircle className="spin"/>正在读取运行记录</div>:<>
   <div className="run-detail-tabs" role="tablist" aria-label="运行详情页面">
    {runTabs.filter(item=>item.id!=='action-replay'||hasReplay).map(item=><button key={item.id} type="button" role="tab" id={'run-tab-'+item.id}
     aria-controls={'run-pane-'+item.id} aria-selected={currentTab===item.id} className={currentTab===item.id?'active':''}
     onClick={()=>item.id==='task-scene'?openTaskScene():setTab(item.id)}><item.icon size={16}/>{item.label}</button>)}
   </div>
   <div className="run-pane run-summary" id="run-pane-summary" role="tabpanel" aria-labelledby="run-tab-summary" hidden={currentTab!=='summary'}>
    <TaskUnderstanding tasks={jobs} events={events} artifacts={artifacts} onReplay={hasReplay?()=>setTab('action-replay'):undefined}/>
    {run.error&&<ErrorBox error={typeof run.error==='string'?run.error:JSON.stringify(run.error)}/>}
    <RunProgress tasks={jobs} artifacts={artifacts} activities={activities} connected={connected} now={now}
     agentsVisible={showLiveAgents} onTaskScene={openTaskScene} onConversation={()=>setConversation(true)} renderStatus={agentProps.renderStatus}/>
    <AgentCollaborationPanel {...collaborationProps} onShowAll={()=>setTab('runtime')}/>
    <SimulationValidationNotice jobs={jobs}/>
    <ScenePreviewPanel runId={runId} outputDirectory={run.output_directory} artifacts={artifacts} tasks={jobs} specHash={run.input?.spec_hash} specRevision={run.input?.spec_revision} activeScene={run.active_scene}/>
    <RunPreviews jobs={jobs} artifacts={artifacts}/>
    <SimulationVisionPreview runId={runId} outputDirectory={run.output_directory} artifacts={artifacts}/>
    {!hasVisuals&&!run.input?.expert_run&&!run.input?.phase_recovery&&!run.input?.recovery_learning&&!run.input?.recovery_student&&<div className="run-no-preview"><Image size={26}/>暂无图像预览</div>}
    <ExpertRunPanel run={run} tasks={jobs} artifacts={artifacts} busy={busy} onOpenRun={onOpenRun}/>
    <PhaseRecoveryPanel run={run} tasks={jobs} artifacts={artifacts}/>
    <RecoveryLearningPanel run={run} tasks={jobs} artifacts={artifacts}/>
   </div>
   <div className="run-pane task-scene-pane" id="run-pane-task-scene" role="tabpanel" aria-labelledby="run-tab-task-scene" hidden={tab!=='task-scene'}>
    {specOpened&&<TaskScenePanel run={run} tasks={jobs} artifacts={artifacts} onOpenRun={onOpenRun}/>}
   </div>
   {hasReplay&&<div className="run-pane" id="run-pane-action-replay" role="tabpanel" aria-labelledby="run-tab-action-replay" hidden={currentTab!=='action-replay'}>
    {currentTab==='action-replay'&&<ActionReplayPanel runId={runId} active={simulation.status==='running'}
      sceneRecheck={simulation.result?.scene_recheck_result} artifacts={artifacts}/>}
   </div>}
   <div className="run-pane run-records" id="run-pane-runtime" role="tabpanel" aria-labelledby="run-tab-runtime" hidden={tab!=='runtime'}>
    <AgentCollaborationPanel {...collaborationProps}/>
    {jobs.some(job=>job.kind==='llm')&&<details className="agent-assignment-details">
     <summary>查看分析分工详情</summary><AgentTeamPanel {...agentProps}/>
    </details>}
    {stages.length>0&&<section className="run-stages">
     <h3 className="subheading"><Layers3 size={16}/>执行阶段 <span>{stages.length}</span></h3>
     <ul>{stages.map((job,i)=><li className="stage-card" key={job.id||i}>
      <div><strong>{job.name||job.role||job.stage||job.id}</strong><Status value={job.status}/></div>
      <p>{isTaskProblem(job)?taskMessage(job,jobs):job.message||job.error||job.result?.message||job.result?.reason}</p>
      {job.progress?.completed!==undefined&&<small>{job.progress.completed} / {job.progress.total??'?'} {job.progress.unit||'项'}</small>}
     </li>)}</ul>
    </section>}
    <section className="run-events">
     <h3 className="subheading"><Terminal size={16}/>运行事件 <span>{events.length}</span></h3>
     <div className="event-stream">{events.length===0?<p className="muted">尚无事件</p>:events.slice(-80).reverse().map((e,i)=>
      <div className="event-line" key={e.id||i}><time>{date(e.created_at||e.timestamp)}</time><b>{e.type||e.event_type||'event'}</b><span>{e.message||e.payload?.message||e.payload?.reason||short(JSON.stringify(e.payload||e.data||{}),220)}</span></div>
     )}</div>
    </section>
   </div>
   <div className="run-pane run-files" id="run-pane-files" role="tabpanel" aria-labelledby="run-tab-files" hidden={tab!=='files'}>
    <div className="run-id">运行 ID <code>{runId}</code>{run.output_directory&&<div className="run-output-directory">产物目录 <code>{run.output_directory}/</code></div>}</div>
    <ArtifactPanel artifacts={artifacts}/>
   </div>
   {conversation&&<AgentConversation runId={runId} questions={questions} artifacts={artifacts} onClose={()=>setConversation(false)} onReply={onReply}/>}
  </>}
 </section>;
}

type ImportBrowserMode = 'demo' | 'models';
type ModelRun = {id:string; status?:string; input:{source_path?:string; model_sources?:string[]}};
type ImportBrowser = {
 mode: ImportBrowserMode;
 path: string;
 parent: string|null;
 entries: {name:string; path:string; is_dir:boolean}[];
};

function ImportModal({close,done,editing}:{close:()=>void,done:(id:string)=>void,editing?:ModelRun}){
 const [path,setPath]=useState(editing?.input.source_path||'');
 const [name,setName]=useState('');
 const [goal,setGoal]=useState('');
 const [models,setModels]=useState<string[]>(editing?.input.model_sources||[]);
 const [modelDirectory,setModelDirectory]=useState('');
 const [episode,setEpisode]=useState(0);
 const [specialistCount,setSpecialistCount]=useState<number>();
 const [browser,setBrowser]=useState<ImportBrowser|null>(null);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [browsing,setBrowsing]=useState<ImportBrowserMode|null>(null);

 async function browse(directory:string,mode:ImportBrowserMode){
  setBrowsing(mode);
  setError('');
  try{
   const query=new URLSearchParams({path:directory.trim(),models:String(mode==='models')});
   const result=await api<Omit<ImportBrowser,'mode'>>('/fs?'+query.toString());
   setBrowser({...result,mode});
   if(mode==='demo')setPath(result.path);
   else setModelDirectory(result.path);
  }catch(e){setError((e as Error).message)}
  finally{setBrowsing(null)}
 }

 function toggleModel(value:string){
  setModels(current=>current.includes(value)?current.filter(item=>item!==value):[...current,value]);
 }

 async function submit(e:FormEvent){
  e.preventDefault();
  setBusy(true);
  setError('');
  try{
   if(editing){
    await put('/runs/'+editing.id+'/object-models',{model_sources:models});
    done(editing.id);
    return;
   }
   const source=path.trim();
   const d=await post('/runs',{
    name:name.trim()||source.replace(/\/$/,'').split('/').pop()||'示教任务',
    source_path:source,goal,episode_index:episode,specialist_count:specialistCount,model_sources:models
   });
   done(d.id||d.run_id||d.run?.id);
  }catch(e){setError((e as Error).message);setBusy(false)}
 }

 function picker(mode:ImportBrowserMode){
  if(browser?.mode!==mode)return null;
  const choosingModels=mode==='models';
  const entries=browser.entries.filter(item=>item.is_dir||choosingModels&&/\.(obj|stl)$/i.test(item.name));
  return <div className="folder-browser" role="group" aria-label={choosingModels?'选择物体模型文件':'选择示教文件夹'}>
   <div className="folder-top">
    <code title={browser.path}>{browser.path}</code>
    {browser.path!=='/'&&<button type="button" disabled={!!browsing||busy} onClick={()=>void browse('/',mode)}>根目录</button>}
    {browser.parent&&<button type="button" disabled={!!browsing||busy} onClick={()=>void browse(browser.parent!,mode)}>上一级</button>}
   </div>
   {entries.map(item=>{
    const selected=models.includes(item.path);
    return <button type="button" key={item.path} title={item.path}
     aria-pressed={item.is_dir?undefined:selected} disabled={!!browsing||busy}
     onClick={()=>item.is_dir?void browse(item.path,mode):toggleModel(item.path)}>
     {item.is_dir?<Folder size={16}/>:<Box size={16}/>}
     <span>{item.name}</span>
     {item.is_dir?<ChevronRight size={14}/>:selected&&<Check size={16}/>}
    </button>;
   })}
   {choosingModels&&!entries.some(item=>!item.is_dir)&&<p className="import-field-help">此目录没有 STL 或 OBJ 文件。</p>}
   <button className="select-folder" type="button" disabled={!!browsing||busy} onClick={()=>setBrowser(null)}>
    <Check size={15}/>{choosingModels?'完成选择':'使用当前文件夹'}
   </button>
  </div>;
 }

 return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)close()}}>
  <div className="modal" role="dialog" aria-modal="true" aria-labelledby="import-title">
   <div className="modal-header">
    <span className="modal-symbol"><FolderOpen size={23}/></span>
    <button className="icon-button" type="button" onClick={close} disabled={busy} aria-label={editing?'关闭模型选择':'关闭导入'}><X size={20}/></button>
   </div>
   <h2 id="import-title">{editing?'修改物体模型':'导入一条示教'}</h2>
   <p className="modal-description">{editing?'保存后，下次重试将使用这里选择的模型。':'选择本机文件夹，系统会复制示教数据到工作台。'}</p>
   <form onSubmit={submit}>
    {!editing&&<>
    <label htmlFor="demo-path">示教文件夹路径<span className="required">*</span></label>
    <div className="input-action">
     <input id="demo-path" autoFocus value={path} onChange={e=>setPath(e.target.value)} placeholder="/home/…/datasets/my_demo" required/>
     <button type="button" className="button secondary" onClick={()=>void browse(path,'demo')} disabled={!!browsing||busy}>
      {browsing==='demo'?<LoaderCircle size={16} className="spin"/>:<Folder size={16}/>}浏览
     </button>
    </div>
    {picker('demo')}
    <label htmlFor="demo-name">任务名称</label>
    <input id="demo-name" value={name} onChange={e=>setName(e.target.value)} placeholder="例如：双臂连接器装配"/>
    </>}
    <div className="import-models">
     <label htmlFor="select-object-models">物体模型（可选）</label>
     <div className="import-model-actions">
      <button id="select-object-models" type="button" className="button secondary" aria-expanded={browser?.mode==='models'}
       onClick={()=>void browse(modelDirectory,'models')} disabled={!!browsing||busy}>
       {browsing==='models'?<LoaderCircle size={16} className="spin"/>:<Box size={16}/>}选择 STL / OBJ
      </button>
      {models.length>0&&<span aria-live="polite">已选 {models.length} 个</span>}
     </div>
     {picker('models')}
     {models.length>0&&<ul className="import-model-list" aria-label="已选模型">{models.map(model=>{
      const filename=model.split('/').pop();
      return <li key={model}><span title={model}>{filename}</span>
       <button type="button" className="icon-button" aria-label={'移除 '+filename} onClick={()=>toggleModel(model)} disabled={busy}><X size={15}/></button>
      </li>;
     })}</ul>}
    </div>
    {!editing&&<details className="import-options">
     <summary><Settings2 size={15}/>示教索引与 Agent<ChevronRight size={14}/></summary>
     <div className="import-extra">
      <div><label htmlFor="episode-index">示教 Episode 索引</label><input id="episode-index" type="number" min="0" step="1" value={episode} onChange={e=>setEpisode(Math.max(0,Number(e.target.value)||0))}/></div>
      <div><label htmlFor="task-goal">目标说明（可选）</label><input id="task-goal" value={goal} onChange={e=>setGoal(e.target.value)} placeholder="供分析Agent参考的任务目标"/></div>
     </div>
     <label htmlFor="specialist-count">专责 Agent 数量</label>
     <select id="specialist-count" value={specialistCount??''} onChange={event=>setSpecialistCount(event.target.value?Number(event.target.value):undefined)} aria-describedby="specialist-count-help">
      <option value="">自动 · 2–4 个</option><option value="2">2 个</option><option value="3">3 个</option><option value="4">4 个</option>
     </select>
     <p id="specialist-count-help" className="import-field-help">总控 Agent 单独计数，按所选数量派工。</p>
    </details>}
    <ErrorBox error={error}/>
    <div className="modal-actions">
     <button type="button" className="button secondary" onClick={close} disabled={busy}>取消</button>
     <button className="button primary" type="submit" disabled={busy||!editing&&!path.trim()}>
      {busy?<LoaderCircle className="spin" size={17}/>:<ArrowRight size={17}/>} {editing?'保存模型选择':'复制并创建任务'}
     </button>
    </div>
   </form>
  </div>
 </div>;
}
function Settings({notify}:{notify:(v:string)=>void}){
 const [env,setEnv]=useState<Record<string,string>>({});const [envConfig,setEnvConfig]=useState<any>({schema_version:1});const [validation,setValidation]=useState<any>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 useEffect(()=>{api('/settings/environments').then(value=>{setEnv(value.environments||value.config?.environments||{});setEnvConfig(value.config||{schema_version:1});setValidation(value.validation||value.checks||null)}).catch(e=>setError(e.message))},[]);
 async function saveEnv(){setBusy(true);setError('');try{const value=await put('/settings/environments',{...envConfig,environments:env});setValidation(value.validation||value.checks||value);notify('环境路径已保存并校验')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <>
  <div className="page-heading"><h1>环境与模型配置</h1></div>
  <ErrorBox error={error}/>
  <div className="settings-compact">
   <details className="panel settings-panel">
    <summary><Terminal size={17}/>外部运行环境<ChevronRight size={16}/></summary>
    <div className="settings-fields">{Object.entries(environmentFields).map(([key,title])=><div className="field" key={key}>
     <label htmlFor={key}>{title}</label>
     <input id={key} value={env[key]||''} onChange={e=>setEnv({...env,[key]:e.target.value})} placeholder="填写绝对路径" spellCheck={false}/>
    </div>)}</div>
    <div className="panel-bottom"><span className="muted">config/environments.yaml</span>
     <button className="button primary" disabled={busy} onClick={saveEnv}>{busy?<LoaderCircle className="spin" size={16}/>:<Check size={16}/>}保存并自检</button>
    </div>
    {validation&&<details className="validation"><summary>查看路径自检结果</summary><JsonView data={validation}/></details>}
   </details>
   <ModelProviderSettings notify={notify}/>
  </div>
 </>
}

function OfflinePolicies(){
 const [policies,setPolicies]=useState<any[]|null>(null);
 const [error,setError]=useState('');
 useEffect(()=>{
  api('/policies').then(result=>setPolicies(collection(result))).catch(e=>setError(e.message));
 },[]);
 return <>
  <div className="page-heading">
   <h1>离线策略</h1>
  </div>
  <ErrorBox error={error}/>
  <ActivePolicyForm policies={policies??[]}/>
  <div className="policy-grid">
   <section className="panel">
    <div className="panel-heading"><h2>策略插件</h2><GitBranch size={20}/></div>
    {policies===null
     ? (error?null:<div className="loading"><LoaderCircle className="spin"/>正在读取策略清单</div>)
     : policies.length===0
      ? <Empty title="暂无策略插件" icon={<GitBranch size={28}/>}/>
      : <div className="policy-list">{policies.map(p=><div key={p.id}>
         <span className="task-icon"><GitBranch size={19}/></span>
         <div><strong>{p.name||p.id}</strong><p>{p.role==='supervisor'?'高层决策':'低层运动'} · {p.kind==='neural'?'神经网络':p.kind==='extension'?'扩展接口':'脚本'}</p>{(p.description||p.detail)&&<small>{p.description||p.detail}</small>}</div>
         <span className="tag">{p.available===false?'待配置':p.version||'可用'}</span>
        </div>)}</div>}
   </section>
   <PolicyPackages/>
  </div>
 </>
}

function RunPreviews({jobs,artifacts}:{jobs:any[],artifacts:any[]}){
 const audit=jobs.find(j=>j.stage==='data_audit')?.result;const views=(audit?.videos||[]).filter((v:any)=>v.contact_sheet);
 if(!views.length)return null;
 const cameraName=(path:string)=>{const name=path.split('/').find(p=>p.includes('observation.images.'))?.replace('observation.images.','');return name==='front'?'前视相机':name==='left_wrist'?'左腕相机':name==='right_wrist'?'右腕相机':name==='side'?'侧视相机':name||'视频采样'};
 return <div className="run-previews"><div className="preview-heading"><h3>示教视频采样</h3><span>点击查看原图</span></div><div className="preview-grid">{views.map((v:any,i:number)=>{const artifact=artifacts.find(a=>a.path===v.contact_sheet);if(!artifact)return null;return <a key={v.path||i} href={'/api/artifacts/'+artifact.id} target="_blank" rel="noreferrer"><img src={'/api/artifacts/'+artifact.id} alt={cameraName(v.path)+'真实示教采样'} loading="lazy"/><div><strong>{cameraName(v.path)}</strong><small>Episode {v.episode_index??audit.episode_index??'—'} · {v.sampled_interval_seconds?.map((x:number)=>x.toFixed(1)).join('–')||'未知区间'} s</small><ArrowUpRight size={13}/></div></a>})}</div></div>
}
function ArtifactPanel({artifacts}:{artifacts:any[]}){
 const [filter,setFilter]=useState('summary');const [query,setQuery]=useState('');
 const preview=(a:any)=>(a.media_type||'').startsWith('image/');const source=(a:any)=>(/\/(files|dataset)\//.test(String(a.path||'')));const summary=(a:any)=>!source(a)&&(/\.(json|jsonl|ya?ml|md|txt|log)$/i.test(a.name||a.path||'')||/(contact_sheet|panels|latest)\.(png|jpe?g)$/i.test(a.name||''));
 const visible=artifacts.filter(a=>(filter==='all'||filter==='summary'&&summary(a)||filter==='images'&&preview(a)||filter==='source'&&source(a))&&(!query||String(a.name+' '+a.path).toLowerCase().includes(query.toLowerCase())));
 return <div className="artifact-panel"><h3 className="subheading"><FileArchive size={16}/>证据与产物 <span>{artifacts.length}</span></h3>{artifacts.length===0?<div className="mini-empty">阶段产物将在这里归档。<br/>失败证据也会被保留。</div>:<><div className="artifact-toolbar"><select aria-label="工件分类" value={filter} onChange={e=>setFilter(e.target.value)}><option value="summary">报告与预览</option><option value="images">图像</option><option value="source">原始数据</option><option value="all">全部工件</option></select><div><Search size={13}/><input aria-label="搜索工件" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索文件名或路径"/></div></div><span className="artifact-count">显示 {visible.length} / {artifacts.length} 项 · 原始数据始终保留</span><div className="artifact-list">{visible.length===0?<p className="mini-empty">没有匹配的工件。</p>:visible.map((a,i)=><a key={a.id||i} href={a.url||'/api/artifacts/'+a.id} target="_blank" rel="noreferrer" title={a.path}><FileText size={17}/><span>{a.name||a.path||a.kind}<small>{a.path}</small></span><ArrowUpRight size={15}/></a>)}</div></>}</div>
}

function ActivePolicyForm({policies}:{policies:any[]}){
 const [active,setActive]=useState<any>(null);const [role,setRole]=useState('supervisor');const [plugin,setPlugin]=useState('rule_supervisor_v1');const [checkpoint,setCheckpoint]=useState('');const [signature,setSignature]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');
 useEffect(()=>{api('/policies/active').then(value=>{setActive(value);setPlugin(value.active.supervisor)}).catch(e=>setError(e.message))},[]);
 function changeRole(v:string){setRole(v);setPlugin(active?.active?.[v]||(v==='supervisor'?'rule_supervisor_v1':'demo_motion_v1'));setError('');setMessage('')}
 async function apply(){setBusy(true);setError('');setMessage('');try{const payload:any={role,plugin_id:plugin};if(plugin==='state_mlp_v1'){payload.checkpoint=checkpoint;payload.dependency_signature=signature}const r=await put('/policies/active',payload);setActive(r);setMessage('离线策略配置已保存')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <section className="panel active-policy"><div className="panel-heading"><div><h2>可替换策略配置</h2><p>高层输出技能决策，低层输出运动提议；按角色单独更换</p></div><span className="tag">仅离线配置</span></div><div className="active-policy-body"><div className="active-policy-state"><span>当前高层 <code>{active?.active?.supervisor||'读取中'}</code></span><span>当前运动 <code>{active?.active?.motion||'读取中'}</code></span></div><div className="policy-switch-fields"><div className="field"><label htmlFor="policy-role">策略层级</label><select id="policy-role" disabled={!active} value={role} onChange={e=>changeRole(e.target.value)}><option value="supervisor">高层技能与恢复决策</option><option value="motion">低层连续运动</option></select></div><div className="field"><label htmlFor="policy-plugin">实现插件</label><select id="policy-plugin" value={plugin} onChange={e=>{setPlugin(e.target.value);setMessage('')}}>{policies.filter(p=>p.role===role&&p.kind!=='extension').map(p=><option key={p.id} value={p.id}>{p.id} · {p.kind==='neural'?'神经网络':'脚本'}</option>)}</select></div><button className="button primary" disabled={busy||!active||!policies.some(p=>p.id===plugin&&p.role===role)} onClick={apply}>{busy?<LoaderCircle className="spin" size={15}/>:<Check size={15}/>}应用离线策略</button></div>{plugin==='state_mlp_v1'&&<div className="neural-policy-fields"><div className="field"><label htmlFor="policy-checkpoint">本项目内已训练权重</label><input id="policy-checkpoint" value={checkpoint} onChange={e=>setCheckpoint(e.target.value)} placeholder="runs/…/state_supervisor.npz"/></div><div className="field"><label htmlFor="policy-signature">冻结依赖签名（SHA-256）</label><input id="policy-signature" value={signature} onChange={e=>setSignature(e.target.value)} placeholder="训练metadata中的64字符版本签名"/></div><p>还需已训练权重与冻结签名。只训练高层技能分类不能代表低层动作或接触任务已通过。</p></div>}<ErrorBox error={error}/>{message&&<div className="policy-confirmation" role="status"><CheckCircle2 size={15}/>{message}</div>}</div></section>
}
function PolicyPackages(){
 const [items,setItems]=useState<any[]|null>(null);
 const [error,setError]=useState('');
 const [selected,setSelected]=useState<any>(null);
 useEffect(()=>{
  api('/deployments').then(result=>setItems(collection(result))).catch(e=>setError(e.message));
 },[]);
 return <section className="panel bundle-panel">
  <div className="panel-heading">
   <div><h2>离线策略包{items!==null&&<span className="count">{items.length}</span>}</h2><p>查看权重标识、依赖签名和评价范围</p></div>
   <FileArchive size={19}/>
  </div>
  <ErrorBox error={error}/>
  {items===null
   ? (error?null:<div className="loading"><LoaderCircle className="spin"/>正在读取离线策略包</div>)
   : items.length===0
    ? <div className="mini-empty incident-empty">暂无已归档的离线策略包。</div>
    : <div className="bundle-list">{items.map(item=><button type="button" key={item.path} onClick={()=>setSelected(item)}>
       <FileArchive size={19}/>
       <span><strong>{item.manifest?.name||item.path.split('/').pop()}</strong><small>{item.manifest?.plugin_id||item.path}</small></span>
       <span className="tag">{item.manifest?.synthetic_test_fixture?'合成夹具':item.manifest?.mode==='simulation_only'?'仿真专用':'查看详情'}</span>
       <ArrowUpRight size={14}/>
      </button>)}</div>}
  {selected&&<div className="modal-backdrop">
   <div className="modal wide" role="dialog" aria-modal="true" aria-labelledby="policy-package-title">
    <div className="modal-header"><h2 id="policy-package-title">策略包清单</h2><button className="icon-button" onClick={()=>setSelected(null)} aria-label="关闭策略包详情"><X/></button></div>
    <JsonView data={selected}/>
   </div>
  </div>}
 </section>
}

function DiagnosticPanel({busy,run}:{busy:boolean,run:(kind:string,options:DiagnosticOptions)=>void}){
 const [options,setOptions]=useState<DiagnosticOptions>({...defaultDiagnosticOptions});
 const numeric=(key:keyof DiagnosticOptions,value:string)=>setOptions(previous=>({...previous,[key]:Number(value)}));
 return <section className="panel diagnostic-panel configurable-diagnostics"><div className="diagnostic-top"><div><span className="tiny-label">MIGRATED REGRESSION · CONNECTOR</span><h2>已迁移任务的离线仿真诊断</h2><p>独立运行原任务的真实 Isaac 回归；不代表新场景自动构建或策略已训练。</p></div><div className="diagnostic-actions"><button className="button secondary small" disabled={busy} onClick={()=>run('connector_bench',options)}><Play size={13}/>机构台架</button><button className="button secondary small" disabled={busy} onClick={()=>run('connector_physics',options)}><Play size={13}/>位姿代理回归</button><button className="button secondary small" disabled={busy} onClick={()=>run('connector_rgbd',options)}><Play size={13}/>RGBD 回归</button></div></div><details className="diagnostic-parameters"><summary><Settings2 size={14}/>位置变化与干扰参数<span>Seed {options.seed} · {options.spread_m.toFixed(3)} m · ±{options.yaw_spread_deg}° · {options.disturbance}</span></summary><div className="diagnostic-fields"><div className="field"><label htmlFor="diagnostic-seed">随机种子</label><input id="diagnostic-seed" type="number" min="0" max="2147483647" step="1" value={options.seed} onChange={e=>numeric('seed',e.target.value)}/></div><div className="field"><label htmlFor="diagnostic-spread">位置变化范围（m）</label><input id="diagnostic-spread" type="number" min="0" max="0.05" step="0.001" value={options.spread_m} onChange={e=>numeric('spread_m',e.target.value)}/></div><div className="field"><label htmlFor="diagnostic-yaw">角度变化范围（± °）</label><input id="diagnostic-yaw" type="number" min="0" max="30" step="1" value={options.yaw_spread_deg} onChange={e=>numeric('yaw_spread_deg',e.target.value)}/></div><div className="field"><label htmlFor="diagnostic-noise">位姿测量噪声（m）</label><input id="diagnostic-noise" type="number" min="0" max="0.005" step="0.0001" value={options.pose_noise_m} onChange={e=>numeric('pose_noise_m',e.target.value)}/></div><div className="field"><label htmlFor="diagnostic-delay">位姿延迟（s）</label><input id="diagnostic-delay" type="number" min="0" max="0.5" step="0.01" value={options.pose_delay_s} onChange={e=>numeric('pose_delay_s',e.target.value)}/></div><div className="field"><label htmlFor="diagnostic-disturbance">物体 / 观测干扰</label><select id="diagnostic-disturbance" value={options.disturbance} onChange={e=>setOptions({...options,disturbance:e.target.value})}><option value="none">无干扰 · none</option><option value="push">推动 · push</option><option value="drop">脱落 · drop</option><option value="relocate">移到别处 · relocate</option><option value="occlude">遮挡 · occlude</option><option value="remove">拿走 · remove</option></select></div></div><div className="diagnostic-scope"><p>机构台架忽略位置、噪声和干扰参数；位姿代理回归使用状态估计代理，RGBD 回归才使用实际渲染图像。参数会随运行保存。</p><p>零噪声基线不表示已具备扰动鲁棒性。有噪声或扩大范围的测试已出现失败，参见经验与失败记录。</p></div></details></section>
}

function SimulationVisionPreview({runId,outputDirectory,artifacts}:{runId:string|undefined,outputDirectory?:string,artifacts:any[]}){
 const selected=selectLatestSimulationPanel(artifacts,runId,outputDirectory);if(!selected)return null;
 const url='/api/artifacts/'+selected.artifact.id;
 return <section className="simulation-vision-panel"><div className="simulation-preview-heading"><h3>仿真 RGBD 感知结果</h3></div><a href={url} target="_blank" rel="noreferrer" title="打开原始仿真分割与FoundationPose结果图"><img src={url} alt="仿真RGBD相机、SAM分割快照与FoundationPose跟踪估计的真实保存结果" loading="lazy"/><div><span>仿真采样 t = {selected.frameTime.toFixed(3)} s</span><span>点击查看原图<ArrowUpRight size={14}/></span></div></a><details className="simulation-preview-source"><summary>图像来源</summary><p>保存的 RGBD 采样面板。SAM 快照保留自己的时间戳，FoundationPose 显示跟踪估计。</p><code>{selected.artifact.path}</code></details></section>
}

function SimulationValidationNotice({jobs}:{jobs:any[]}){
 const notices=getSimulationValidationNotices(jobs);if(!notices.length)return null;
 return <div className="simulation-validation-notice" role="alert"><TriangleAlert size={20}/><div>{notices.map((message,i)=><strong key={i}>{message}</strong>)}<p>此回合含未通过的验收子项，需结合原始报告和经验记录继续修正。工作流完成状态不代替子项验收。</p></div></div>
}
