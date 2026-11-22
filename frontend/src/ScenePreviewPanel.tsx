import {useEffect,useState} from 'react';
import {ArrowUpRight, Box, CheckCircle2, ChevronRight, FileText, Image, TriangleAlert} from 'lucide-react';
import {activeSceneFor, selectScenePreviewArtifacts, sceneSpecBinding} from './taskScene';
import type {ActiveScene,SceneArtifact} from './taskScene';
import {api} from './api';

const validationLabels: Record<string, string> = {
 queued:'等待执行', running:'检查中', succeeded:'检查已完成', failed:'检查失败',
 blocked:'等待条件', needs_configuration:'待配置', interrupted:'已中断',
};
const href = (artifact: {id: string}) => '/api/artifacts/' + artifact.id;
const comparisonLabels: Record<string,string> = {
 orientation_verified:'实景对照通过',orientation_mismatch:'摆放待修正',orientation_unverified:'摆放待确认',
 visually_consistent:'可见布局基本一致',review_required:'仍有差异待复核',
};

type ViewInfo = {id:string;path:string;approximate_reference_view?:boolean;recorded_camera?:string};
type VisualReview = {
 checks?:{details:{recorded_ref?:string;simulated_ref?:string;cue?:string;recorded_camera?:string;pairing?:string;recorded_episode_seconds?:number}}[];
 simulated_views?:ViewInfo[];
 recorded_frames?:{path:string;camera?:string;episode_relative_seconds?:number}[];
 placement_alignment?:{status:string;spacings?:{objects:string[];estimated_spacing_m:number;simulated_spacing_m:number;passed:boolean}[];
  object_labels?:Record<string,string>;
  positions?:{object_id:string;label?:string}[]};
};

const cameraLabels:Record<string,string>={front:'前视',side:'侧视',left:'左腕',right:'右腕',left_wrist:'左腕',right_wrist:'右腕',center:'中间相机',overview:'全景'};
function cameraLabel(id?:string){
 const name=id?.split('.').pop();
 return name ? cameraLabels[name]||name : '';
}

function ComparisonEvidence({report,artifacts}:{report:SceneArtifact;artifacts:SceneArtifact[]}){
 const [open,setOpen]=useState(false);
 const [loaded,setLoaded]=useState<{id:string;value:VisualReview}>();
 useEffect(()=>{
  if(!open)return;
  const controller=new AbortController();
  api<VisualReview>(`/artifacts/${report.id}`,{signal:controller.signal})
   .then(value=>setLoaded({id:report.id,value})).catch(()=>{});
  return ()=>controller.abort();
 },[open,report.id]);
 const byPath=new Map(artifacts.map(artifact=>[artifact.path,artifact]));
 const data=loaded?.id===report.id?loaded.value:undefined;
 const views=new Map(data?.simulated_views?.map(view=>[view.path,view]));
 const frames=new Map(data?.recorded_frames?.map(frame=>[frame.path,frame]));
 const pairs=new Map<string,{recorded:SceneArtifact;simulated:SceneArtifact;cue?:string;camera?:string;view?:ViewInfo;pairing?:string;time?:number}>();
 for(const {details} of data?.checks??[]){
  const recorded=details.recorded_ref && byPath.get(details.recorded_ref);
  const simulated=details.simulated_ref && byPath.get(details.simulated_ref);
  const key=recorded&&simulated ? recorded.id+':'+simulated.id : '';
  if(recorded&&simulated&&!pairs.has(key)){
   const frame=frames.get(recorded.path);
   pairs.set(key,{recorded,simulated,cue:details.cue,view:views.get(simulated.path),pairing:details.pairing,
    camera:details.recorded_camera||frame?.camera,time:details.recorded_episode_seconds??frame?.episode_relative_seconds});
  }
 }
 const alignment=data?.placement_alignment;
 const objectLabels=new Map(alignment?.positions?.map(position=>[position.object_id,position.label]));
 const matchedView=(kind?:string)=>kind==='landmark_fitted_view'||kind==='calibrated_camera_reference';
 const displayedPairs=[...pairs.values()].sort((a,b)=>Number(matchedView(b.pairing))-Number(matchedView(a.pairing))||
  (a.time??Infinity)-(b.time??Infinity)).slice(0,4);
 return <details className="scene-build-evidence" onToggle={event=>setOpen(event.currentTarget.open)}>
  <summary><Image size={15}/>实景与仿真对照<ChevronRight size={15}/></summary>
  {data&&<div className="scene-placement-summary">
   <p>{alignment?.status==='checked'?'已按工作面落点核对相对位置':alignment?.status==='mismatch'?'相对落点或间距仍有偏差':'当前报告未核验实际间距'}</p>
   {!!alignment?.spacings?.length&&<ul>{alignment.spacings.map(pair=><li key={pair.objects.join(':')}>
    {pair.objects.map(id=>alignment.object_labels?.[id]||objectLabels.get(id)||id).join(' ↔ ')} · 示教比例换算 {(pair.estimated_spacing_m*100).toFixed(1)} cm · 仿真 {(pair.simulated_spacing_m*100).toFixed(1)} cm{pair.passed?'':' · 待修正'}
   </li>)}</ul>}
  </div>}
  {displayedPairs.map(({recorded,simulated,cue,camera,view,pairing,time})=>{
   const captions=[{artifact:recorded,label:['示教照片',cameraLabel(camera),typeof time==='number'?`${time.toFixed(1)} s`:''].filter(Boolean).join(' · ')},
    {artifact:simulated,label:['Isaac 场景初始状态',cameraLabel(view?.id),view?.recorded_camera?'按画面拟合':view?.approximate_reference_view?'估算视角':''].filter(Boolean).join(' · ')}];
   const matched=matchedView(pairing);
   return <figure className="scene-comparison-pair" key={recorded.id+simulated.id}>
   <div className="scene-camera-grid">
    {captions.map(({artifact,label})=>
     <a key={artifact.id} href={href(artifact)} target="_blank" rel="noreferrer">
      <img src={href(artifact)} alt={label} loading="lazy"/><div>{label}</div>
     </a>)}
   </div>
   <figcaption>{!matched&&<span>视角未对齐 · 画面仅供定性对照{cue?'。':''}</span>}{cue}</figcaption>
  </figure>})}
  <div className="scene-evidence-links"><a href={href(report)} target="_blank" rel="noreferrer">查看逐项对照报告<ArrowUpRight size={12}/></a></div>
 </details>;
}

export default function ScenePreviewPanel({runId,outputDirectory,artifacts,tasks=[],specHash,specRevision,activeScene}:{
 runId:string|undefined; outputDirectory?:string; artifacts:any[]; tasks?:any[]; specHash?:string; specRevision?:number;
 activeScene?:ActiveScene|null;
}){
 const validation = tasks.find(task=>task.stage==='scene_validation');
 const active = activeSceneFor(tasks,activeScene);
 const preview = selectScenePreviewArtifacts(artifacts,runId,outputDirectory,active?.build.preview_path,!active?.review_pending);
 const comparison = artifacts.find(a=>a.path===(active?.visual_comparison_path||validation?.result?.visual_comparison_path));
 const [failedImage,setFailedImage] = useState<string>();
 const [rigViews,setRigViews] = useState<{manifestId:string; views:{id:string;rgb_path:string}[]}>();
 const build = tasks.find(task=>task.stage==='scene_build');
 const registration = active?.build.robot_registration;
 const manifestId = preview?.manifest?.id;
 useEffect(()=>{
  if(!manifestId)return;
  const controller = new AbortController();
  api<{cameras:{id:string;rgb_path:string}[]}>(`/artifacts/${manifestId}`,{signal:controller.signal})
   .then(data=>{if(Array.isArray(data.cameras))setRigViews({manifestId,views:data.cameras})})
   .catch(()=>{});
  return ()=>controller.abort();
 },[manifestId]);
 if(!preview) return active?.review_pending ? <section className="scene-preview-panel">
  <div className="scene-preview-heading"><h3><Box size={17}/>Isaac 场景</h3></div>
  <div className="scene-inline-error"><TriangleAlert size={14}/>{comparisonLabels[active.visual_comparison_status||'']||'等待实景摆放核对'}</div>
  {comparison&&<ComparisonEvidence report={comparison} artifacts={artifacts}/>}
 </section> : null;
 const binding = sceneSpecBinding({input:{spec_hash:specHash,spec_revision:specRevision},active_scene:active},tasks);
 const incomplete = build && ['failed','cancelled','interrupted','blocked','needs_configuration'].includes(build.status);
 const published = active?.build.preview_path === preview.scene.path;
 const currentRepair = published && !!active?.repair_report_path;
 const liveValidation = published && active?.stage==='simulation' ? undefined : validation;
 const report = artifacts.find(a=>a.path===(published ? active?.validation_path : undefined)) || artifacts.find(a=>a.path===liveValidation?.result?.report_path) || artifacts.filter(a=>a.path?.startsWith((outputDirectory||'runs/'+runId)+'/scene_validation/')&&a.path.endsWith('/validation_report.json'))
  .sort((a,b)=>(b.created_at||0)-(a.created_at||0))[0];
 const evidence = [
  [preview.usd,'打开 USD'], [preview.specSnapshot,'任务定义快照'], [preview.manifest,'相机与资产清单'],
  [preview.buildReport,'构建报告'], [report,'局部检查报告'],
  [artifacts.find(a=>a.path===build?.result?.report_path),'修正记录'],
  [artifacts.find(a=>a.path===(published ? active?.repair_report_path : undefined)),'场景纠错记录'],
 ];
 const refinements = (build?.result?.refinement_rounds ?? []).flatMap((round:any)=>{
  const artifact = artifacts.find(a=>a.path===round.render);
  return artifact ? [{round:round.round,artifact,status:round.status}] : [];
 });
 const cameras = rigViews?.manifestId===manifestId && rigViews ? rigViews.views.flatMap(view=>{
  const artifact=artifacts.find(item=>item.path===view.rgb_path&&item.path.startsWith(preview.directory));
  return artifact && view.id!=='overview' ? [{artifact,label:cameraLabel(view.id)||artifact.name}] : [];
 }) : preview.cameras.map(artifact=>({artifact,label:refinements.length?(/camera_0_rgb\.png$/.test(artifact.path)?'仿真前视 · 估算视角':'仿真侧视 · 估算视角'):artifact.name||artifact.path.split('/').pop()}));

 return <section className="scene-preview-panel">
  <div className="scene-preview-heading"><h3><Box size={17}/>Isaac 场景</h3>
   {currentRepair&&<span className="tag"><CheckCircle2 size={13}/>修正版已复验</span>}
   {active?.visual_comparison_status==='orientation_verified'&&<span className="tag"><CheckCircle2 size={13}/>可见摆放已核对</span>}
  </div>
  {registration&&<div className="scene-inline-error"><Box size={14}/>Nero 双臂已按 {registration.bundle_id} 估计对齐，用于离线预览。</div>}
  {build?.result?.scene_approximation&&<div className="scene-inline-error"><TriangleAlert size={14}/>近似场景。对象几何和初始布局来自多视角估算，适用于对照示教与预览工作空间。</div>}
  {binding.requestMismatch&&<div className="scene-inline-error" role="alert"><TriangleAlert size={14}/>构建记录与请求的任务快照不一致，请核对产物绑定。</div>}
  {incomplete&&<div className="scene-inline-error" role="alert"><TriangleAlert size={14}/>构建尚未完成。当前图像来自中断前的场景渲染。</div>}
  {failedImage===preview.scene.id
   ? <div className="scene-inline-error" role="alert"><TriangleAlert size={14}/>场景图像暂时无法读取。</div>
   : <a className="scene-overview-image" href={href(preview.scene)} target="_blank" rel="noreferrer">
    <img src={href(preview.scene)} alt="本次场景构建的 Isaac 全景渲染" loading="lazy" onError={()=>setFailedImage(preview.scene.id)}/>
    <span>场景全景<ArrowUpRight size={14}/></span>
   </a>}
  {cameras.length>0&&<div className="scene-camera-grid">{cameras.map(({artifact:camera,label})=>
   <a key={camera.id} href={href(camera)} target="_blank" rel="noreferrer">
    <img src={href(camera)} alt={'场景 RGB 相机渲染 · ' + (camera.name||camera.path.split('/').pop())} loading="lazy"/>
    <div><span>{label}</span><ArrowUpRight size={13}/></div>
   </a>)}</div>}
  {comparison&&<ComparisonEvidence report={comparison} artifacts={artifacts}/>}
  {refinements.length>0&&<details className="scene-build-evidence">
   <summary><Image size={15}/>查看 {refinements.length} 轮修正渲染<ChevronRight size={15}/></summary>
   <div className="scene-camera-grid">{refinements.map(({round,artifact,status}:any)=>
    <a key={round} href={href(artifact)} target="_blank" rel="noreferrer">
     <img src={href(artifact)} alt={'第 '+round+' 轮场景渲染'} loading="lazy"/>
     <div><span>第 {round} 轮 · {status==='succeeded'?'局部检查通过':'局部检查待复核'}</span><ArrowUpRight size={13}/></div>
    </a>)}</div>
  </details>}
  {liveValidation&&<div className={'scene-validation-summary '+(liveValidation.status==='failed'?'failed':'')}>
   <span>{liveValidation.status==='succeeded'?<CheckCircle2 size={15}/>:<TriangleAlert size={15}/>}场景局部检查 · {validationLabels[liveValidation.status]||liveValidation.status}</span>
   <p>本项检查覆盖场景几何和局部物理行为。</p>
   {liveValidation.result?.visual_comparison_status&&<p>实景对照 · {comparisonLabels[liveValidation.result.visual_comparison_status]}</p>}
   {liveValidation.error&&<p>{liveValidation.error}</p>}
   <details><summary>查看本次实际检查结果</summary><pre className="json-view">{JSON.stringify(liveValidation.result||{},null,2)}</pre></details>
  </div>}
  <details className="scene-build-evidence">
   <summary><FileText size={15}/>构建文件与快照<ChevronRight size={15}/></summary>
   {(binding.hash||binding.revision!==undefined)&&<div className="scene-spec-binding">任务快照 revision {binding.revision??'—'} · <code>{binding.hash||'未提供 hash'}</code></div>}
   <div className="scene-evidence-links">{evidence.filter(([artifact])=>artifact).map(([artifact,title])=>
    <a key={artifact.id} href={href(artifact)} target="_blank" rel="noreferrer"><FileText size={14}/>{title}<ArrowUpRight size={12}/></a>)}</div>
   {preview.diagram&&<details className="scene-frame-diagram">
    <summary><Image size={14}/>坐标参考示意</summary>
    <a href={href(preview.diagram)} target="_blank" rel="noreferrer"><img src={href(preview.diagram)} alt="资产与功能坐标示意图" loading="lazy"/></a>
   </details>}
   <p className="scene-preview-source">{preview.directory}</p>
  </details>
 </section>;
}
