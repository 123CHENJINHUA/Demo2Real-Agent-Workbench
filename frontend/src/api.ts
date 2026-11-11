let token = '';
export class ApiError extends Error { status:number; body:any; constructor(message:string,status:number,body?:any){super(message);this.status=status;this.body=body} }
export async function api<T=any>(path:string, options:RequestInit={}):Promise<T>{
  const method=options.method||'GET';
  if(method!=='GET'&&!token){const session=await api<any>('/session');token=session.token||session.session_token||session.csrf_token||'';}
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(token?{'X-Session-Token':token,'X-Workbench-Token':token}:{}),...options.headers}});
  const text=await response.text();let data:any;try{data=text?JSON.parse(text):{}}catch{throw new ApiError('服务返回了无法识别的响应',response.status)}
  if(!response.ok){const detail=data.detail||data.error||data.message;throw new ApiError(typeof detail==='string'?detail:JSON.stringify(detail||response.statusText),response.status,data)}
  if(path==='/session')token=data.token||data.session_token||data.csrf_token||'';
  return data as T;
}
export const post=(path:string,body:any={})=>api(path,{method:'POST',body:JSON.stringify(body)});
export const put=(path:string,body:any)=>api(path,{method:'PUT',body:JSON.stringify(body)});
export function collection(value:any):any[]{return Array.isArray(value)?value:value?.items||value?.policies||value?.runs||value?.cases||value?.results||value?.events||value?.agents||[];}
