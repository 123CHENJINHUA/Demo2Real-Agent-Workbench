export interface TaskSpecEnvelope{spec:Record<string,any>;spec_path?:string;etag?:string;history?:any[];freeze_snapshots?:any[];scene_builds?:any[];frozen_snapshot_path?:string;freeze_scope?:string}
export interface SceneArtifact{id:string;path:string;name?:string;media_type?:string;created_at?:number}
export function parseTaskSpecDraft(text:string):Record<string,any>{
 let value:any;try{value=JSON.parse(text)}catch(error){throw new Error('JSON语法错误：'+(error as Error).message)}
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('任务定义必须是JSON对象');
 return value;
}
export function currentSpecRevision(envelope:TaskSpecEnvelope|null){const n=envelope?.spec?.revision;return typeof n==='number'&&Number.isInteger(n)&&n>=1?n:null}
export function isTaskSpecDirty(text:string,envelope:TaskSpecEnvelope|null){return !!envelope&&text!==JSON.stringify(envelope.spec,null,2)}
export function taskSpecFieldErrors(error:any):string[]{
 const detail=error?.body?.detail??error?.body?.errors;
 if(Array.isArray(detail))return detail.map(value=>typeof value==='string'?value:[Array.isArray(value.loc)?value.loc.join('.'):value.path,value.msg||value.message||value.error].filter(Boolean).join('：')||JSON.stringify(value));
 if(detail&&typeof detail==='object'){if(Array.isArray(detail.errors))return detail.errors.map((value:any)=>typeof value==='string'?value:JSON.stringify(value));return [JSON.stringify(detail)]}
 return [error?.message||String(error)];
}
export function selectScenePreviewArtifacts(artifacts:SceneArtifact[],runId:string|undefined){
 if(!runId)return null;const prefix='runs/'+runId+'/scene_build/';
 const scenes=artifacts.filter(a=>a.id&&a.path.startsWith(prefix)&&a.path.slice(prefix.length).split('/').length===2&&a.path.endsWith('/scene.png'));
 const numeric=(path:string)=>{const name=path.slice(prefix.length).split('/')[0];const match=name.match(/^(?:build_|scene_|preview_|isaac_)?(\d+)$/);return match?BigInt(match[1]):null};
 scenes.sort((a,b)=>{const left=numeric(a.path),right=numeric(b.path);if(left!==null&&right!==null&&left!==right)return left>right?-1:1;return (b.created_at||0)-(a.created_at||0)||b.path.localeCompare(a.path)});
 const scene=scenes[0];if(!scene)return null;const directory=scene.path.slice(0,scene.path.lastIndexOf('/')+1);
 const exact=(filename:string)=>artifacts.filter(a=>a.path===directory+filename).sort((a,b)=>(b.created_at||0)-(a.created_at||0))[0]||null;
 return {directory,scene,cameras:[exact('camera_0_rgb.png'),exact('camera_1_rgb.png')].filter(Boolean) as SceneArtifact[],diagram:exact('frame_axes.svg'),usd:exact('scene.usda'),manifest:exact('scene_manifest.json'),specSnapshot:exact('task_spec.json'),buildReport:exact('build_report.json')};
}

export function taskSpecRequestHeaders(envelope:TaskSpecEnvelope|null):Record<string,string>{return envelope?.etag?{'If-Match':envelope.etag}:{}}
export function taskSpecGenerationBody(profileId:string,envelope:TaskSpecEnvelope|null){const body:{profile_id?:string;expected_revision?:number}={};if(profileId)body.profile_id=profileId;if(envelope){const revision=currentSpecRevision(envelope);if(revision===null)throw new Error('无法确定当前任务定义revision，请重新读取');body.expected_revision=revision}return body}
export function taskSpecSaveBody(text:string,envelope:TaskSpecEnvelope|null){const revision=currentSpecRevision(envelope);if(revision===null)throw new Error('任务定义尚无有效revision');return {spec:parseTaskSpecDraft(text),expected_revision:revision}}
export function taskSpecFreezeBody(envelope:TaskSpecEnvelope|null){const revision=currentSpecRevision(envelope);if(revision===null)throw new Error('任务定义尚无有效revision');return {expected_revision:revision,scope:'offline_contract'}}
export function taskSpecBuildBody(envelope:TaskSpecEnvelope|null){const revision=currentSpecRevision(envelope);if(revision===null)throw new Error('任务定义尚无有效revision');return {expected_revision:revision,preview:true}}

export function sceneSpecBinding(run:any,tasks:any[]=[]){
 const result=tasks.find(task=>task.stage==='scene_build')?.result||{};
 const requestedHash=run?.input?.spec_hash;const requestedRevision=run?.input?.spec_revision;
 const hash=result.task_spec_hash||requestedHash;const revision=result.spec_revision??requestedRevision;
 return {hash,revision,requestedHash,requestedRevision,fromResult:!!result.task_spec_hash,requestMismatch:!!result.task_spec_hash&&((!!requestedHash&&result.task_spec_hash!==requestedHash)||(requestedRevision!==undefined&&result.spec_revision!==undefined&&result.spec_revision!==requestedRevision))};
}
