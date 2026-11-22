import {sceneSpecBinding} from './taskScene.ts';
export function expertLaunchState(run:any,tasks:any[],sourceSpec:any,busy=false){
 const binding=sceneSpecBinding(run,tasks);const build=tasks.find(t=>t.stage==='scene_build');const validation=tasks.find(t=>t.stage==='scene_validation');
 let reason='';
 if(busy)reason='已有操作正在提交。';
 else if(run?.status!=='succeeded'||build?.status!=='succeeded'||validation?.status!=='succeeded')reason='需先完成场景构建和独立局部检查。';
 else if(run?.input?.profile_id!=='connector_keyed_v1')reason='当前离线专家仅支持已核验的Connector定义。';
 else if(!binding.fromResult||typeof binding.hash!=='string'||!/^[a-f0-9]{64}$/.test(binding.hash)||!Number.isInteger(binding.revision)||binding.revision<1)reason='实际场景缺少有效任务版本或内容hash。';
 else if(binding.requestMismatch)reason='实际场景产物与请求快照不一致。';
 else if(!sourceSpec)reason='正在核验源任务当前版本。';
 else if(sourceSpec.content_hash!==binding.hash||sourceSpec.revision!==binding.revision)reason='场景对应旧任务快照，请重新构建。';
 return {enabled:!reason,reason,binding};
}
export function expertRequestBody(run:any,tasks:any[],sourceSpec:any){
 const state=expertLaunchState(run,tasks,sourceSpec);if(!state.enabled)throw new Error(state.reason);
 return {expected_revision:state.binding.revision,spec_hash:state.binding.hash};
}
const bool=(v:any):boolean|null=>typeof v==='boolean'?v:null;
export function expertOutcome(tasks:any[],report:any=null){
 const execution=tasks.find(t=>t.stage==='expert_execution')?.result||{};const validation=tasks.find(t=>t.stage==='expert_validation')?.result;
 const owns=(value:any,key:string)=>!!value&&Object.prototype.hasOwnProperty.call(value,key);const metric=(key:string,fallback:any=null)=>bool(owns(validation,key)?validation[key]:owns(execution,key)?execution[key]:fallback);const independent=!!validation&&(['action_passed','full_task_passed','retention_passed'].some(key=>owns(validation,key))||Object.keys(validation.stage_results||{}).length>0);const raw=report?.report||report||execution.report||{};
 const stage=(name:string,fallback:any)=>{const value=validation?.stage_results?.[name]??execution.stage_results?.[name];return value?bool(value.passed):bool(fallback)};
 const grasps=raw.grasps;const grasp=Array.isArray(grasps)&&grasps.length>0&&grasps.every(g=>typeof g.passed==='boolean')?grasps.every(g=>g.passed):null;
 const retention=stage('retention',metric('retention_passed',raw.post_release_retained));
 const overall=metric('full_task_passed');const action=metric('action_passed');
 return {grasp:stage('grasp',grasp),insertion:stage('insertion',null),release:stage('release',raw.released_from_fingers),retention,action,overall:retention===false?false:overall,inconsistent:overall===true&&retention===false,independent};
}
export function selectExpertArtifacts(runId:string,artifacts:any[],tasks:any[],outputDirectory?:string){
 const prefix=(outputDirectory||'runs/'+runId)+'/';
 const local=artifacts.filter(a=>a.id&&a.path?.startsWith(prefix));
 const execution=tasks.find(t=>t.stage==='expert_execution')?.result||{};const validation=tasks.find(t=>t.stage==='expert_validation')?.result||{};
 const match=(path:string|undefined,suffix:string,stage:string)=>local.find(a=>path&&a.path===path)||local.filter(a=>a.path.startsWith(prefix+stage+'/')&&a.path.endsWith('/'+suffix)).sort((a,b)=>(b.created_at||0)-(a.created_at||0))[0]||null;
 return {report:match(execution.report_path,'expert_report.json','expert_execution'),validation:match(validation.report_path,'validation_report.json','expert_validation'),trajectory:execution.trajectory_path?local.find(a=>a.path===execution.trajectory_path)||null:null};
}
