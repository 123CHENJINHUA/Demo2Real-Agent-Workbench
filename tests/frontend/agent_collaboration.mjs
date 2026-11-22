import assert from 'node:assert/strict';
import {agentExchanges,agentName} from '../../frontend/src/agentCollaboration.ts';

const sim={id:'run:simulation',stage:'simulation',role:'动作专家学习与位置泛化测试',
  status:'running',deps:[],started_at:10,heartbeat_at:15};
const event=(id,at,type,payload)=>({id,created_at:at,task_id:sim.id,type,payload});
const review={exchange_id:'review',sender:'总控 Agent',recipient:'场景 Agent',request:'核对初始支撑'};
const retest={exchange_id:'retest',sender:'场景 Agent',recipient:'动作专家 Agent',request:'在候选场景复测',
  evidence_path:'scene.json'};
const step1=event(1,11,'agent.handoff_started',review);
const step2=event(2,12,'agent.handoff_started',retest);
const step3=event(3,13,'agent.handoff_finished',{
  ...retest,status:'failed',summary:'右夹指仍会碰到工件',report_path:'trial.json',
});
assert.equal(agentName(sim),'动作专家 Agent');
let state=agentExchanges([sim],[step1,step2]);
assert.equal(state.active.get(sim.id).recipient,'动作专家 Agent');
assert.equal(state.rows[0].request,'在候选场景复测');
state=agentExchanges([sim],[step1,step2,step3]);
assert.equal(state.active.get(sim.id).recipient,'场景 Agent');
assert.equal(state.rows.find(row=>row.id==='retest').summary,'右夹指仍会碰到工件');
assert.equal(state.rows.find(row=>row.id==='retest').reportPath,'trial.json');
assert.equal(agentExchanges([{...sim,started_at:20}],[step2]).active.size,0);
assert.equal(agentExchanges([{...sim,status:'cancelled'}],[step2]).rows[0].status,'cancelled');
assert.equal(agentExchanges([{...sim,status:'failed'}],[step3]).rows[0].status,'failed');

const dispatch={id:'dispatch',stage:'agent_dispatch',role:'总控 Agent · 派工',deps:[],status:'succeeded',finished_at:7,
  result:{plan_path:'dispatch.json',plan:{assignments:[{role:'agent_intent',focus:'核对抓取和移送步骤'}]}}};
const member={id:'intent',stage:'agent_intent',role:'示教与动作 Agent',status:'succeeded',deps:[dispatch.stage],finished_at:9,
  result:{report:{summary:'先取出板件，再横移到目标位置'},report_path:'report.json'}};
const history=agentExchanges([dispatch,member],[]);
assert.equal(history.rows[0].sender,'总控 Agent');
assert.equal(history.rows[0].recipient,'示教与动作 Agent');
assert.equal(history.rows[0].summary,'先取出板件，再横移到目标位置');
assert.equal(history.rows[0].reportPath,'report.json');
assert.equal(history.active.size,0);
assert.equal(agentExchanges([{...dispatch,status:'failed'},member],[]).rows.length,0);
console.log('Agent collaboration checks passed');
