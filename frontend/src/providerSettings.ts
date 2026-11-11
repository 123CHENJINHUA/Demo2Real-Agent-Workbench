export type ProviderKind='openai_compatible'|'codex_cli';
export interface ProviderDraft{kind:ProviderKind;model:string;base_url?:string;api_key?:string;timeout?:number}
export function buildProviderPayload(draft:ProviderDraft){
 if(!['openai_compatible','codex_cli'].includes(draft.kind))throw new Error('未知模型服务类型');
 const timeout=Number(draft.timeout??90);if(!Number.isFinite(timeout)||timeout<1||timeout>300)throw new Error('模型请求超时须在1–300秒内');
 if(draft.kind==='codex_cli')return {kind:'codex_cli',model:draft.model.trim(),timeout};
 const payload:{kind:string;model:string;timeout:number;base_url:string;api_key?:string}={kind:'openai_compatible',model:draft.model.trim(),timeout,base_url:(draft.base_url||'').trim()};
 if(draft.api_key?.trim())payload.api_key=draft.api_key.trim();
 return payload;
}
export function codexConnectionView(status:any){
 if(!status)return {state:'checking',label:'正在读取项目内状态',ready:false};
 if(status.available===false||['unavailable','not_available'].includes(status.status))return {state:'not_available',label:'Codex CLI 待配置',ready:false};
 if(status.authenticated===true)return {state:'ready',label:'项目内已登录',ready:true};
 if(status.status==='error')return {state:'error',label:'状态检查失败',ready:false};
 return {state:'not_authenticated',label:'项目内待登录',ready:false};
}
export function officialDeviceLoginUrl(value:unknown){
 if(typeof value!=='string')return null;
 try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&(!url.port||url.port==='443')&&['auth.openai.com','login.openai.com','chatgpt.com'].includes(url.hostname)?url.href:null}catch{return null}
}
export function resultModelLabel(result:any,configuredModel:string){
 if(result?.model&&!['CLI default','default'].includes(result.model))return String(result.model);
 return configuredModel.trim()?configuredModel.trim()+'（请求配置；未返回具体型号）':'CLI 默认（真实返回后核验）';
}
