import {useEffect,useMemo,useRef,useState} from 'react';
import {Activity,Pause,Play,RotateCcw} from 'lucide-react';
import {api} from './api';
import {armColors,drawReplay,frameAt,objectColors,phaseAt,replayBounds} from './actionReplay';
import type {ReplayData,ReplayView} from './actionReplay';

interface Episode {index:number;trial:string;layout:string;passed:boolean;score:number;checks:number;
 termination?:{kind:'early_fall'|'phase_check_failed'|'unexpected_robot_contact'|'initial_support_loss';
 phase:number;time_s:number;actors?:string[];object_id?:string;support_id?:string}|null;reused_from?:number|null}
interface LearningFeedback {kind:string;phase:number;time_s:number;check?:{description:string};actors?:string[]}
interface Candidate {label:string;passed:number;total:number;mean_checks:number;mean_prefix?:number;focus_phase?:number|null;corrections?:number[][];
 screened?:number;planned_layouts?:number;start_offset_m?:number[][]|null;offset_window_s?:number[]|null;
 parent?:string|null;input_feedback?:LearningFeedback|null;outcome_feedback?:LearningFeedback|null;
 repair?:{phase:number;waypoint:number;arm:string;translation_m:number[];rotation_xyz_deg:number[];opening_delta_m:number}|null}
interface ReplayIndex {attempt:string;episodes:Episode[];learning:{mode?:string;candidates:Candidate[];selected:string|null;frozen:boolean}}
interface SceneRecheck {
 status:string;
 repair_report_path?:string;
 source_episode_report_path?:string;
 approach_retry_status?:string;
 approach_retry_scene_manifest?:string;
 proposed_scene_manifest?:string;
 retest_episode_report_path?:string;
}

function layoutLabel(value:string){
 if(value==='demonstrated_layout')return '示教布局';
 const [kind,index]=value.split('_');
 if(kind==='train'||kind==='holdout')return `${kind==='train'?'训练布局':'留出布局'} ${Number(index)+1}`;
 return value;
}

function feedbackLabel(feedback:LearningFeedback){
 if(feedback.check)return feedback.check.description;
 if(feedback.kind==='unexpected_robot_contact')return `${feedback.time_s.toFixed(2)} s 非预期接触${feedback.actors?.length?' · '+feedback.actors.map(item=>item.split('/').at(-1)).join(' × '):''}`;
 if(feedback.kind==='initial_support_loss')return `${feedback.time_s.toFixed(2)} s 工件在接触前失稳`;
 if(feedback.kind==='early_fall')return `${feedback.time_s.toFixed(2)} s 工件提前跌落`;
 if(feedback.kind==='lift_loss')return `${feedback.time_s.toFixed(2)} s 工件抬起后掉到台面以下`;
 return '逆运动学未达到所需目标位姿';
}

function ReplayPlayer({data,round}:{data:ReplayData;round:Episode}){
 const canvas=useRef<HTMLCanvasElement>(null);
 const [view,setView]=useState<ReplayView>('perspective');
 const [time,setTime]=useState(0);
 const [playing,setPlaying]=useState(false);
 const [speed,setSpeed]=useState(1);
 const duration=data.time[data.time.length-1];
 const frame=frameAt(data.time,time), phase=Math.min(phaseAt(data.phases,time),round.termination?.phase??data.phases.length-1);
 const actualTime=data.time[frame];
 const bounds=useMemo(()=>replayBounds(data,view),[data,view]);
 useEffect(()=>{if(canvas.current)drawReplay(canvas.current,data,frame,view,bounds)},[data,frame,view,bounds]);
 useEffect(()=>{
  if(!playing)return;
  const start=performance.now();
  const beginning=time;
  let animation:number;
  function tick(now:number){
   const next=Math.min(duration,beginning+(now-start)*speed/1000);
   setTime(next);
   if(next>=duration)setPlaying(false);
   else animation=requestAnimationFrame(tick);
  }
  animation=requestAnimationFrame(tick);
  return ()=>cancelAnimationFrame(animation);
  // A running playback keeps the time captured when play was pressed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 },[playing,speed,duration]);
 const seek=(value:number)=>{setPlaying(false);setTime(Math.min(value,duration))};
 const contacts=data.contacts.filter(contact=>Math.abs(contact.time_s-actualTime)<=.22).slice(0,8);
 const fail=data.early_fall;
 const lost=data.lift_loss;
 const objectLabel=(id:string)=>data.objects.find(object=>object.id===id)?.label||id;
 return <div className="action-replay-player">
  <div className="action-replay-player-heading"><strong>{round.trial} · {layoutLabel(round.layout)}</strong>
   <label>视角 <select value={view} onChange={event=>setView(event.target.value as ReplayView)}>
    <option value="perspective">斜视</option><option value="front">正视</option><option value="side">侧视</option><option value="top">俯视</option>
   </select></label></div>
  <canvas ref={canvas} width={1000} height={530} role="img" aria-label="仿真关节与物体位姿回放"/>
  <div className="action-replay-legend" aria-label="回放图例">
   {['左臂','右臂'].map((label,i)=><span key={label}><i style={{backgroundColor:armColors[i]}}/>{label}</span>)}
   {data.objects.map((object,i)=><span key={object.id}><i style={{backgroundColor:objectColors[i%objectColors.length]}}/>{object.label}</span>)}
  </div>
  <div className="action-replay-controls">
   <button type="button" className="button secondary small" onClick={()=>{if(time>=duration)setTime(0);setPlaying(!playing)}}>
    {playing?<Pause size={16}/>:<Play size={16}/>} {playing?'暂停':'播放'}
   </button>
   <button type="button" className="icon-button" aria-label="回到起点" onClick={()=>seek(0)}><RotateCcw size={16}/></button>
   <input type="range" min={0} max={duration} step={.001} value={time} onChange={event=>seek(Number(event.target.value))} aria-label="回放时间"/>
   <output aria-label="当前回放秒数">{actualTime.toFixed(2)} / {duration.toFixed(2)} s</output>
   <label>速度 <select value={speed} onChange={event=>setSpeed(Number(event.target.value))}>
    <option value={.25}>0.25×</option><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option>
   </select></label>
  </div>
  <div className="action-replay-steps" aria-label="跳到动作阶段">{data.phases.map((item,i)=><button key={i} type="button" className="button secondary small"
   disabled={!!round.termination&&i>round.termination.phase}
   aria-pressed={phase===i} onClick={()=>seek(i===0?0:(data.phases[i-1].end_s+.001))}>{item.name}</button>)}</div>
  {round.termination?.kind==='phase_check_failed'&&<button className="action-replay-fall" type="button" onClick={()=>seek(duration)}>
   {round.termination.time_s.toFixed(2)} s · {data.phases[round.termination.phase]?.name}筛选未通过，后续阶段未执行</button>}
  {round.termination?.kind==='unexpected_robot_contact'&&<button className="action-replay-fall" type="button" onClick={()=>seek(duration)}>
   {round.termination.time_s.toFixed(3)} s · 非预期接触 · {round.termination.actors?.map(actor=>objectLabel(actor.split('/').pop()||actor)).join(' × ')}
  </button>}
  {round.termination?.kind==='initial_support_loss'&&<button className="action-replay-fall" type="button" onClick={()=>seek(round.termination?.time_s??duration)}>
   {round.termination.time_s.toFixed(3)} s · {objectLabel(round.termination.object_id??'工件')}在接触前失稳 · 跳到该帧
  </button>}
  {round.reused_from!=null&&<p className="action-replay-hint">布局与控制命令相同，对照复用第 {round.reused_from+1} 回合。</p>}
  {fail&&<button className="action-replay-fall" type="button" onClick={()=>seek(fail.time_s+.001)}>
   {fail.time_s.toFixed(2)} s · {objectLabel(fail.object_id)}在抬升前低于台面 · 跳到该帧</button>}
  {lost&&<button className="action-replay-fall" type="button" onClick={()=>seek(lost.time_s)}>
   {lost.time_s.toFixed(2)} s · {objectLabel(lost.object_id)}抬起 {(lost.peak_rise_m*1000).toFixed(1)} mm 后掉到台面以下 · 跳到该帧</button>}
  <div className="action-replay-observations">
   <div><strong>夹爪开度</strong>
    {['左臂','右臂'].map((label,i)=><p key={label}>{label} · 实际 {data.gripper_actual[frame][i].toFixed(1)} mm · 命令 {data.gripper_command[frame][i].toFixed(1)} mm</p>)}
   </div>
   <div><strong>附近的接触事件</strong>
    {!data.contacts_logged?<p>该回合没有记录接触对。</p>:contacts.length?contacts.map((event,i)=><p key={i} title={event.actors.join(' / ')}>
     {event.time_s.toFixed(2)} s · {event.actors.map(actor=>objectLabel(actor.split('/').slice(-1)[0])).join(' × ')} · {event.impulse_ns} N·s</p>):<p>当前时间附近没有新接触记录。</p>}
   </div>
  </div>
  {!!Object.keys(data.offsets).length&&<details className="action-replay-checks"><summary>本回合的布局变化</summary><ul>
   {Object.entries(data.offsets).map(([id,offset])=><li key={id}>{objectLabel(id)} · XYZ {offset.map(value=>(value*100).toFixed(1)).join(', ')} cm</li>)}
  </ul></details>}
  <details className="action-replay-checks"><summary>验收明细 · {round.score}/{round.checks} 项</summary>
   <ul>{data.checks.map((check,i)=><li key={i}><b>{check.evaluated===false?'未执行':check.kind==='unsupported'||check.measured===null?'待验证':check.passed?'通过':'未通过'}</b>
    {' '}{check.description}{check.measured!==null&&<> · {check.kind==='region'?'超出区域':'实测'} {check.kind==='orientation'?`${check.measured.toFixed(1)}°`:`${(check.measured*1000).toFixed(1)} mm`}</>}</li>)}</ul>
  </details>
 </div>;
}

export default function ActionReplayPanel({runId,active,sceneRecheck,artifacts=[]}:{
 runId:string;active:boolean;sceneRecheck?:SceneRecheck;artifacts?:{id:string;path:string}[];
}){
 const [summary,setSummary]=useState<ReplayIndex>();
 const [choice,setChoice]=useState<{attempt:string;index:number}>();
 const [loaded,setLoaded]=useState<{key:string;data:ReplayData}>();
 const [error,setError]=useState('');
 const [traceError,setTraceError]=useState('');
 const base='/runs/'+encodeURIComponent(runId)+'/action-replay';
 useEffect(()=>{
  const controller=new AbortController();
  async function refresh(){
   try {const index=await api<ReplayIndex>(base,{signal:controller.signal});if(!controller.signal.aborted){setSummary(index);setError('')}}
   catch(err){if(!controller.signal.aborted)setError((err as Error).message)}
  }
  void refresh();
  const timer=active?window.setInterval(()=>void refresh(),2500):undefined;
  return ()=>{controller.abort();window.clearInterval(timer)};
 },[active,base]);
 const rounds=summary?.episodes||[];
 const round=(choice?.attempt===summary?.attempt&&rounds.find(item=>item.index===choice?.index)) ||
  rounds.find(item=>item.trial==='独立验证'&&item.layout==='demonstrated_layout') || rounds[rounds.length-1];
 const attempt=summary?.attempt;
 const selectedIndex=round?.index;
 const replayKey=round&&attempt?`${attempt}:${round.index}`:undefined;
 useEffect(()=>{
  if(selectedIndex===undefined||!attempt||!replayKey)return;
  const controller=new AbortController();
  setChoice({attempt,index:selectedIndex});
  setTraceError('');
  void api<ReplayData>(`${base}/${selectedIndex}?attempt=${encodeURIComponent(attempt)}`,{signal:controller.signal})
   .then(data=>{if(!controller.signal.aborted)setLoaded({data,key:replayKey})})
   .catch(err=>{if(!controller.signal.aborted)setTraceError((err as Error).message)});
  return ()=>controller.abort();
 },[base,attempt,selectedIndex,replayKey]);
 const data=loaded?.key===replayKey?loaded?.data:undefined;
 const learning=summary?.learning;
 const reused=rounds.filter(item=>item.reused_from!=null).length;
 const stopped=rounds.filter(item=>item.termination).length;
 const candidateLabel=round?.trial==='独立验证'?learning?.selected:round?.trial;
 const chosen=learning?.candidates.find(item=>item.label===candidateLabel);
 const recheckReport=artifacts.find(item=>item.path===sceneRecheck?.repair_report_path);
 const sourceEpisodes=artifacts.find(item=>item.path===sceneRecheck?.source_episode_report_path);
 const retestEpisodes=artifacts.find(item=>item.path===sceneRecheck?.retest_episode_report_path);
 const recheckStatus=sceneRecheck?.status==='approach_rechecked'?'场景回查后，原异常阶段已复测通过':
  sceneRecheck?.status==='candidate_not_verified_in_motion'?'场景回查后，修正版仍缺少通过复测的证据':
  '已回查场景，现有证据不足以支持更改';
 return <section className="action-replay" aria-label="专家训练回放">
  <header><h2><Activity size={19}/>专家训练回放</h2><span>{rounds.length-reused} 个仿真回合
   {stopped>0&&` · ${stopped} 个提前结束`}{reused>0&&` · ${reused} 个复用对照`}</span></header>
  {sceneRecheck&&<div className="run-work-links">
   <span>{recheckStatus}</span>
   {sceneRecheck.approach_retry_status&&<span>{sceneRecheck.approach_retry_scene_manifest &&
      sceneRecheck.approach_retry_scene_manifest===sceneRecheck.proposed_scene_manifest
      ?'已在修正版场景复测接近路径':'已在原场景试验接近路径'}</span>}
   {recheckReport&&<a href={'/api/artifacts/'+recheckReport.id} target="_blank" rel="noreferrer">场景回查报告</a>}
   {sourceEpisodes&&<a href={'/api/artifacts/'+sourceEpisodes.id} target="_blank" rel="noreferrer">回查前的回合记录</a>}
   {retestEpisodes&&sceneRecheck.approach_retry_status&&<a href={'/api/artifacts/'+retestEpisodes.id} target="_blank" rel="noreferrer">修正版场景首轮复测</a>}
  </div>}
  {error?<p role="alert">{error}</p>:!rounds.length?<p role="status">{!summary?'正在读取回合记录':active?'等待第一个仿真回合写入':'这次尝试尚无可回放的回合'}</p>:<>
   <p className="action-replay-hint">按仿真记录重放物体位姿和机械臂关节，场景以几何代理显示。</p>
   <div className="action-replay-workspace">
    <nav aria-label="已记录的训练与验证回合">{rounds.map(item=><button type="button" key={item.index}
     aria-pressed={round?.index===item.index} onClick={()=>setChoice({attempt:attempt!,index:item.index})}>
     <strong>{item.index+1}. {item.trial}</strong><span>{layoutLabel(item.layout)} · {item.termination?'提前结束':item.reused_from!=null?'复用轨迹':`${item.score}/${item.checks} 项`}</span>
    </button>)}</nav>
    {traceError?<p role="alert">{traceError}</p>:round&&data?<ReplayPlayer key={replayKey} data={data} round={round}/>:<p role="status">正在读取此回合轨迹</p>}
   </div>
   {!!learning?.candidates.length&&<section className="action-replay-learning" aria-label="方案对比">
    <h3>{learning.mode==='scene_waypoint_search'?'仿真迭代':'方案对比'}</h3>
    <table><thead><tr><th>候选</th><th>{learning.candidates.some(item=>item.screened!==undefined)?'布局筛选':'通过回合'}</th><th>平均连续通过阶段</th><th>平均通过项</th><th>调整阶段</th></tr></thead><tbody>
     {learning.candidates.map(candidate=><tr key={candidate.label}>
      <th>{candidate.label}{candidate.label===learning.selected&&<small>{learning.frozen?' · 已冻结':' · 当前最佳'}</small>}</th>
      <td>{candidate.screened??candidate.passed}/{candidate.total}{candidate.planned_layouts!==undefined&&candidate.total<candidate.planned_layouts&&
       <small> · 跳过 {candidate.planned_layouts-candidate.total} 个</small>}</td><td>{candidate.mean_prefix?.toFixed(2)??'—'}</td><td>{candidate.mean_checks.toFixed(2)}</td>
      <td>{candidate.focus_phase==null?'—':data?.phases[candidate.focus_phase]?.name||`阶段 ${candidate.focus_phase+1}`}</td>
     </tr>)}
    </tbody></table>
    {chosen&&learning.mode==='scene_waypoint_search'&&<details open>
     <summary>{chosen.label} · {chosen.parent?'基于 '+chosen.parent:'初始场景目标点'}</summary>
     {chosen.input_feedback&&<p>上一轮证据 · {feedbackLabel(chosen.input_feedback)}</p>}
     {chosen.repair&&<p>{chosen.repair.arm==='left'?'左臂':'右臂'}目标点 {chosen.repair.waypoint+1}
      {chosen.repair.translation_m.some(value=>value!==0)&&' · 平移 '+chosen.repair.translation_m.map(value=>(value*1000).toFixed(1)).join(', ')+' mm'}
      {chosen.repair.rotation_xyz_deg.some(value=>value!==0)&&' · 旋转 '+chosen.repair.rotation_xyz_deg.map(value=>value.toFixed(1)).join(', ')+'°'}
      {chosen.repair.opening_delta_m!==0&&' · 开度调整 '+(chosen.repair.opening_delta_m*1000).toFixed(1)+' mm'}</p>}
     {chosen.outcome_feedback&&<p>本轮结果 · {feedbackLabel(chosen.outcome_feedback)}</p>}
    </details>}
    {chosen?.corrections&&<details><summary>查看{chosen.label}的位姿调整</summary><ol>
     {chosen.start_offset_m?.map((delta,i)=>delta.some(value=>value!==0)&&
       <li key={'starting-'+i}>{i===0?'左臂':'右臂'}{chosen.offset_window_s
        ?`接近避让（${chosen.offset_window_s[0].toFixed(2)}–${chosen.offset_window_s[3].toFixed(2)} s）`:'起始平移'}
        {' · XYZ '}{delta.map(value=>(value*1000).toFixed(1)).join(', ')} mm</li>)}
     {chosen.corrections.map((values,i)=><li key={i}>{data?.phases[i]?.name||`阶段 ${i+1}`} · 平移 {values.slice(0,3).map(value=>(value*1000).toFixed(1)).join(', ')} mm · 旋转 {values.slice(3).map(value=>value.toFixed(1)).join(', ')}°</li>)}
    </ol></details>}
   </section>}
  </>}
 </section>;
}
