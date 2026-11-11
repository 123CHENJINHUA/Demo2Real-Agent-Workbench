import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {buildDiagnosticPayload,defaultDiagnosticOptions} from '../../frontend/src/diagnostics.ts';
const kinds=['connector_bench','connector_physics','connector_rgbd'];
const disturbances=['none','push','drop','relocate','occlude','remove'];
assert.deepEqual(buildDiagnosticPayload(kinds[0],defaultDiagnosticOptions),{kind:'connector_bench',seed:712,spread_m:0.025,yaw_spread_deg:8,pose_noise_m:0,pose_delay_s:0,disturbance:'none'});
let rejected=0;
for(const invalid of [{seed:-1},{seed:2147483648},{seed:1.5},{spread_m:-.001},{spread_m:.051},{yaw_spread_deg:31},{yaw_spread_deg:-1},{pose_noise_m:.0051},{pose_noise_m:-1},{pose_delay_s:.51},{pose_delay_s:-1},{pose_noise_m:NaN},{seed:Infinity},{disturbance:'shell-command'}]){
 assert.throws(()=>buildDiagnosticPayload(kinds[0],{...defaultDiagnosticOptions,...invalid}));rejected++;
}
assert.throws(()=>buildDiagnosticPayload('arbitrary_worker',defaultDiagnosticOptions));rejected++;
const requests=[];
const server=createServer(async(req,res)=>{
 let text='';for await(const chunk of req)text+=chunk;
 requests.push({method:req.method,path:req.url,body:JSON.parse(text)});
 res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({isolated_payload_receiver:true}));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const address=server.address();assert.notEqual(address.port,8787);
try{
 for(const kind of kinds)for(const disturbance of disturbances){
  const body=buildDiagnosticPayload(kind,{...defaultDiagnosticOptions,seed:123456,spread_m:.04,yaw_spread_deg:20,pose_noise_m:.003,pose_delay_s:.2,disturbance});
  const response=await fetch(`http://127.0.0.1:${address.port}/api/diagnostics`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  assert.equal(response.status,200);const last=requests.at(-1);assert.equal(last.method,'POST');assert.equal(last.path,'/api/diagnostics');assert.deepEqual(last.body,body);
 }
}finally{await new Promise(resolve=>server.close(resolve));}
assert.equal(requests.length,18);
const report={status:'passed',default_parameters:defaultDiagnosticOptions,validated_modes:kinds,validated_disturbances:disturbances,isolated_http_requests:requests.length,invalid_payloads_rejected:rejected,receiver:'isolated ephemeral localhost test server',workbench_diagnostics_endpoint_called:false,gpu_jobs_started:0,hardware_commands_sent:0};
const out=new URL('../../runs/acceptance/frontend/',import.meta.url);await mkdir(out,{recursive:true});await writeFile(new URL('diagnostic-payload-report.json',out),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
