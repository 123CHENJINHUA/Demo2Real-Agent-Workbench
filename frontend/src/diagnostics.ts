export interface DiagnosticOptions {
  seed:number; spread_m:number; yaw_spread_deg:number; pose_noise_m:number; pose_delay_s:number; disturbance:string;
}
export const defaultDiagnosticOptions:DiagnosticOptions={seed:712,spread_m:0.025,yaw_spread_deg:8,pose_noise_m:0,pose_delay_s:0,disturbance:'none'};
export function buildDiagnosticPayload(kind:string,options:DiagnosticOptions){
  if(!['connector_bench','connector_physics','connector_rgbd'].includes(kind))throw new Error('未知仿真诊断模式');
  const ranges:[keyof DiagnosticOptions,number,number,string][]=[['seed',0,2147483647,'随机种子'],['spread_m',0,0.05,'位置变化范围'],['yaw_spread_deg',0,30,'角度变化范围'],['pose_noise_m',0,0.005,'位姿测量噪声'],['pose_delay_s',0,0.5,'位姿延迟']];
  for(const [key,min,max,title] of ranges){const value=options[key];if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max)throw new Error(title+'超出范围：'+min+'–'+max);}
  if(!Number.isInteger(options.seed))throw new Error('随机种子必须是整数');
  if(!['none','push','drop','relocate','occlude','remove'].includes(options.disturbance))throw new Error('未知干扰类型');
  return {kind,seed:options.seed,spread_m:options.spread_m,yaw_spread_deg:options.yaw_spread_deg,pose_noise_m:options.pose_noise_m,pose_delay_s:options.pose_delay_s,disturbance:options.disturbance};
}
