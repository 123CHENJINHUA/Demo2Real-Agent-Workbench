export interface PreviewArtifact {id:string;path:string;name?:string;media_type?:string;created_at?:number}
export function selectLatestSimulationPanel(artifacts:PreviewArtifact[],runId:string|undefined){
 if(!runId)return null;
 const prefix='runs/'+runId+'/simulation/';
 const candidates=artifacts.flatMap(artifact=>{
  if(!artifact.id||!artifact.path?.startsWith(prefix))return [];
  const match=artifact.path.slice(prefix.length).match(/^isaac_(\d+)\/(?:[^/]+\/)*vision\/visualization\/frame_(\d+(?:\.\d+)?)\/panels\.png$/);
  if(!match)return [];
  const frameTime=Number(match[2]);if(!Number.isFinite(frameTime))return [];
  return [{artifact,simulationStamp:match[1],frameTime}];
 });
 candidates.sort((a,b)=>{
  const left=BigInt(a.simulationStamp),right=BigInt(b.simulationStamp);
  if(left!==right)return left>right?-1:1;
  return b.frameTime-a.frameTime||(b.artifact.created_at||0)-(a.artifact.created_at||0);
 });
 return candidates[0]||null;
}

export function getSimulationValidationNotices(tasks:any[]):string[]{
 const notices:string[]=[];
 for(const task of tasks.filter(t=>t.stage==='simulation')){
  const outcome=task.result;const trials=Array.isArray(outcome?.result?.results)?outcome.result.results:[];
  const retentionFailures=trials.filter((trial:any)=>trial.post_release_retained===false);
  if(retentionFailures.length){
   const actionCompleted=retentionFailures.every((trial:any)=>trial.passed===true&&trial.released_from_fingers===true);
   notices.push(actionCompleted?'插入及松手完成；松手后保持验证未通过':'松手后保持验证未通过');
  }
  if(Array.isArray(outcome?.validation_warnings))for(const warning of outcome.validation_warnings){if(typeof warning==='string'&&warning.trim())notices.push(warning)}
 }
 return [...new Set(notices)];
}
