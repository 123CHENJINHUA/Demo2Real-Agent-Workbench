export type Vec = [number,number,number];
type Orientation = [number,number,number,number];
export type ReplayView = 'perspective'|'front'|'side'|'top';
export interface ReplayBody {id:string;label:string;size:Vec|null;boxes?:{center_m:Vec;size_m:Vec}[]}
export interface ReplayCheck {phase:number;kind:string;description:string;passed:boolean;measured:number|null;threshold:number;evaluated?:boolean}
export interface ReplayData {
 time:number[]; pose:Vec[][]; quaternion:Orientation[][]; arms:Vec[][][]; fingers:Vec[][];
 objects:ReplayBody[]; table:{position_m:Vec;size_m:Vec}; phases:{name:string;end_s:number}[];
 gripper_actual:number[][]; gripper_command:number[][]; offsets:Record<string,Vec>; checks:ReplayCheck[];
 early_fall?:{object_id:string;phase:number;time_s:number}; contacts_logged:boolean;
 lift_loss?:{object_id:string;phase:number;time_s:number;peak_rise_m:number}|null;
 contacts:{time_s:number;actors:string[];impulse_ns:number}[];
}

export const objectColors=['#ad641f','#65827d','#9a6f10','#9776ad','#4c8188','#a25f65'];
export const armColors=['#266f80','#5458a8'];

export function frameAt(times:number[],target:number){
 let start=0,end=times.length-1;
 while(start<end){
  const middle=Math.ceil((start+end)/2);
  if(times[middle]<=target)start=middle;
  else end=middle-1;
 }
 return start;
}

export function phaseAt(phases:ReplayData['phases'],time:number){
 const phase=phases.findIndex(item=>time<=item.end_s);
 return phase<0 ? phases.length-1 : phase;
}

const project=(view:ReplayView,[x,y,z]:Vec):[number,number]=>{
 if(view==='front')return [x,-z];
 if(view==='side')return [y,-z];
 if(view==='top')return [x,-y];
 return [.87*(x-y),-.43*(x+y)-z];
};

function rotate([x,y,z]:Vec,[w,a,b,c]:Orientation):Vec{
 const u=b*z-c*y,v=c*x-a*z,t=a*y-b*x;
 return [x+2*(w*u+b*t-c*v),y+2*(w*v+c*u-a*t),z+2*(w*t+a*v-b*u)];
}

const add=(a:Vec,b:Vec):Vec=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
const corners=(center:Vec,size:Vec,q:Orientation=[1,0,0,0]):Vec[]=>
 [0,1,2,3,4,5,6,7].map(i=>add(center,rotate(size.map((length,axis)=>(i&(1<<axis)?1:-1)*length/2) as Vec,q)));
const faces=[[0,1,3,2],[4,5,7,6],[0,1,5,4],[2,3,7,6],[0,2,6,4],[1,3,7,5]];

export function replayBounds(data:ReplayData,view:ReplayView){
 const range=[Infinity,Infinity,-Infinity,-Infinity];
 function include(point:Vec){
  const [x,y]=project(view,point);
  range[0]=Math.min(range[0],x);range[1]=Math.min(range[1],y);
  range[2]=Math.max(range[2],x);range[3]=Math.max(range[3],y);
 }
 corners(data.table.position_m,data.table.size_m).forEach(include);
 for(let i=0;i<data.time.length;i++){
  data.arms[i].forEach(arm=>arm.forEach(include));
  data.fingers[i].forEach(include);
  data.pose[i].forEach((point,j)=>corners(point,data.objects[j].size||[0,0,0],data.quaternion[i][j]).forEach(include));
 }
 return range;
}

export function drawReplay(canvas:HTMLCanvasElement,data:ReplayData,frame:number,view:ReplayView,bounds:number[]){
 const context=canvas.getContext('2d');
 if(!context)return;
 const ctx:CanvasRenderingContext2D=context;
 const w=canvas.width,h=canvas.height;
 const [xmin,ymin,xmax,ymax]=bounds;
 const width=Math.max(.1,xmax-xmin),height=Math.max(.1,ymax-ymin);
 const scale=Math.min((w-82)/width,(h-68)/height);
 function screen(point:Vec):[number,number]{
  const [x,y]=project(view,point);
  return [w/2+(x-(xmin+xmax)/2)*scale,h/2+(y-(ymin+ymax)/2)*scale];
 }
 function shape(points:Vec[],stroke:string,weight:number,fill?:string){
  ctx.beginPath();points.forEach((point,i)=>{const [x,y]=screen(point);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});
  if(fill){ctx.closePath();ctx.fillStyle=fill;ctx.fill();}
  ctx.strokeStyle=stroke;ctx.lineWidth=weight;ctx.stroke();
 }
 function box(center:Vec,size:Vec,rotation:Orientation,color:string){
  const vertices=corners(center,size,rotation);
  for(const face of faces)shape(face.map(i=>vertices[i]),color,1,color+'18');
 }
 function dot(point:Vec,color:string,radius:number){
  ctx.beginPath();ctx.arc(...screen(point),radius,0,2*Math.PI);ctx.fillStyle=color;ctx.fill();
 }

 ctx.fillStyle='#eff4ef';ctx.fillRect(0,0,w,h);
 ctx.lineJoin='round';ctx.lineCap='round';
 box(data.table.position_m,data.table.size_m,[1,0,0,0],'#819792');
 data.objects.forEach((object,i)=>{
  const color=objectColors[i%objectColors.length],point=data.pose[frame][i],q=data.quaternion[frame][i];
  const trail=data.pose.slice(Math.max(0,frame-20),frame+1).map(row=>row[i]);
  ctx.setLineDash([4,5]);shape(trail,color,2);ctx.setLineDash([]);
  if(object.boxes?.length){
   for(const item of object.boxes)box(add(point,rotate(item.center_m,q)),item.size_m,q,color);
  } else if(object.size)box(point,object.size,q,color);
  dot(point,color,4);
 });
 data.arms[frame].forEach((arm,i)=>{
  const color=armColors[i];
  shape(arm,color,5);
  arm.forEach(point=>dot(point,color,4));
  data.fingers[frame].slice(i*2,i*2+2).forEach(point=>{
   shape([arm[arm.length-1],point],color,2.5);dot(point,color,4);
  });
 });
}
