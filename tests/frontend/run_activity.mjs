import assert from 'node:assert/strict';
import {hasLiveHeartbeat, taskActivities, taskMessage} from '../../frontend/src/runActivity.ts';

const task = {id:'run:agent_intent',stage:'agent_intent',role:'示教与动作 Agent',kind:'llm',
  status:'running',deps:[],started_at:20,heartbeat_at:22,progress:{phase:'preparing_model_input'}};
const event = (id, created_at, type, payload) => ({id,created_at,task_id:task.id,type,payload});
assert.equal(taskActivities([task],[event(1,19,'model.codex_event',{event_type:'turn.started'})]).get(task.id).text,
  '正在整理示教帧与任务证据');
const activities = taskActivities([task],[
  event(2,21,'model.codex_event',{event_type:'turn.started'}),
  event(3,22,'model.codex_event',{event_type:'item.completed',item_type:'reasoning',text:'hidden'}),
]);
assert.equal(activities.get(task.id).text,'模型正在分析');
assert.equal(hasLiveHeartbeat(task,true,24),true);
assert.equal(hasLiveHeartbeat(task,false,24),false);
assert.equal(hasLiveHeartbeat(task,true,40),false);
assert.equal(taskMessage(task,[task],activities.get(task.id),false),'等待运行状态更新');

const queued = {...task,id:'run:task_analysis',stage:'task_analysis',status:'queued',deps:[task.stage]};
assert.equal(taskMessage(queued,[task,queued]),'等待 示教与动作 Agent');
const assetCount = taskActivities([task],[
  event(4,23,'progress',{completed:4,total:8,unit:'assets',current_file:'project/assets/fixture.usd'}),
  event(5,24,'scene.heartbeat',{}),
]).get(task.id);
assert.deepEqual(assetCount.count,{done:4,total:8,unit:'项资产'});
assert.equal(assetCount.text,'处理中 · fixture.usd');
assert.equal(taskActivities([task],[event(6,25,'progress',{completed:4,total:2,unit:'assets'})]).get(task.id).count,undefined);

const geometry = {...task,id:'run:functional_geometry',stage:'functional_geometry',role:'功能几何证据审查',
  status:'blocked',result:{issues:[
    {severity:'review',code:'experience_review_missing',message:'non-blocking review'},
    {severity:'blocking',code:'mesh_missing',asset_id:'object_0'},
    {severity:'blocking',code:'mesh_missing',asset_id:'object_1'},
  ]}};
const scene = {...task,id:'run:scene_build',stage:'scene_build',status:'blocked',deps:[geometry.stage]};
assert.equal(taskMessage(geometry,[geometry,scene]),'2 个对象缺少模型文件或明确的几何尺寸。');
assert.equal(taskMessage(scene,[geometry,scene]),'等待 功能几何证据审查 恢复');
console.log('Run activity checks passed');
