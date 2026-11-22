import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {build} from '../../frontend/node_modules/esbuild/lib/main.js';
import React from '../../frontend/node_modules/react/index.js';
import {renderToStaticMarkup} from '../../frontend/node_modules/react-dom/server.node.js';
import {parseTaskSpecDraft,currentSpecRevision,isTaskSpecDirty,taskSpecFieldErrors,taskSpecRequestHeaders,taskSpecGenerationBody,taskSpecSaveBody,taskSpecFreezeBody,taskSpecBuildBody,selectScenePreviewArtifacts,sceneSpecBinding} from '../../frontend/src/taskScene.ts';
const groups=[];
const verify=(name,fn)=>{fn();groups.push(name)};
const spec={schema_version:'task-spec/v1',task_id:'fixture',revision:2,state:'draft',profile_id:'fixture-profile',content_hash:'a'.repeat(64),created_at:'fixture timestamp',objects:[{id:'fixture-object',asset_path:'assets/test-only.obj',geometry_type:'mesh',pose:{position_m:null,quaternion_wxyz:null},scale:null,mass_kg:null}],robot:{arms:[]},scene:{table:{size_m:[1,1,.1],position_m:[0,0,.4]}},frames:[],phases:[{name:'fixture phase'}],evaluation:{action:{state:'not_verified'},retention:{state:'independent_not_verified'}},unresolved:['test fixture unknown functional frame'],source:{proposal_path:'runs/fixture/task_analysis/proposal.json',manifest_path:'runs/fixture/demo_import/manifest.json',evidence:[]},validation:{physical:'not_verified',hardware:'not_verified'},contact_execution_authorized:false,hardware_verified:false};
const envelope={spec,etag:'"r2-'+spec.content_hash+'"',history:[{revision:2,state:'draft',content_hash:spec.content_hash}],freeze_snapshots:[],scene_builds:[]};
verify('JSON draft parser rejects non-objects',()=>{assert.deepEqual(parseTaskSpecDraft('{"objects":[]}'),{objects:[]});for(const text of ['[1]','null','1','invalid'])assert.throws(()=>parseTaskSpecDraft(text))});
verify('positive revision required',()=>{assert.equal(currentSpecRevision(envelope),2);assert.equal(currentSpecRevision(null),null);assert.equal(currentSpecRevision({spec:{revision:0}}),null)});
verify('local dirty detection',()=>{assert.equal(isTaskSpecDirty(JSON.stringify(spec,null,2),envelope),false);assert.equal(isTaskSpecDirty('{"edit":true}',envelope),true)});
verify('structured backend field errors retained',()=>{assert.deepEqual(taskSpecFieldErrors({body:{detail:[{loc:['objects',0,'pose'],msg:'invalid pose'}]}}),['objects.0.pose：invalid pose']);assert.deepEqual(taskSpecFieldErrors(new Error('conflict')),['conflict'])});
verify('revision and quoted etag preserved',()=>{assert.deepEqual(taskSpecRequestHeaders(envelope),{'If-Match':envelope.etag});assert.deepEqual(taskSpecRequestHeaders(null),{});assert.deepEqual(taskSpecSaveBody(JSON.stringify(spec),envelope),{spec,expected_revision:2})});
verify('profile rebuild uses explicit precondition',()=>{assert.deepEqual(taskSpecGenerationBody('registered',envelope),{profile_id:'registered',expected_revision:2});assert.deepEqual(taskSpecGenerationBody('',null),{})});
verify('freeze scope is only offline contract',()=>assert.deepEqual(taskSpecFreezeBody(envelope),{expected_revision:2,scope:'offline_contract'}));
verify('build is preview only',()=>{assert.deepEqual(taskSpecBuildBody(envelope),{expected_revision:2,preview:true});assert.ok(!('execute' in taskSpecBuildBody(envelope)));assert.throws(()=>taskSpecBuildBody(null))});
const scene=(id,build,filename='scene.png',run='fixture')=>({id,name:filename,path:`runs/${run}/scene_build/${build}/${filename}`,created_at:1});
const artifacts=[scene('old','build_1794326783548444121'),scene('latest','build_1794326783548444122'),scene('old-camera','build_1794326783548444121','camera_0_rgb.png'),scene('new-camera','build_1794326783548444122','camera_0_rgb.png'),scene('new-usd','build_1794326783548444122','scene.usda'),scene('other','build_9999999999999999999','scene.png','another'),{id:'source',path:'runs/fixture/demo_import/dataset/scene_build/build_9999999999999999999/scene.png'},{id:'contact',path:'runs/fixture/data_audit/video_00/contact_sheet.jpg'}];
verify('real scene selection excludes source and other runs',()=>{const selected=selectScenePreviewArtifacts(artifacts,'fixture');assert.equal(selected.scene.id,'latest');assert.deepEqual(selected.cameras.map(a=>a.id),['new-camera']);assert.equal(selected.usd.id,'new-usd')});
verify('grouped scene uses the run output directory',()=>{
 const directory='runs/demo/derived/fixture';
 const grouped=artifacts.filter(a=>a.path.startsWith('runs/fixture/scene_build/')).map(a=>({...a,path:a.path.replace('runs/fixture',directory)}));
 assert.equal(selectScenePreviewArtifacts([...grouped,scene('loose','build_9999999999999999999')],'fixture',directory).scene.id,'latest');
});
verify('numeric latest build sorting and no invented preview',()=>{assert.equal(selectScenePreviewArtifacts([scene('a','build_9'),scene('b','build_10')],'fixture').scene.id,'b');assert.equal(selectScenePreviewArtifacts(artifacts.slice(-2),'fixture'),null);assert.equal(selectScenePreviewArtifacts([], 'fixture'),null)});
verify('published simulation repair selects its own render, snapshots and camera paths',()=>{
 const folder='runs/fixture/simulation/scene_repair_10/attempt_1/build_11/';
 const revised=[{id:'verified',path:folder+'scene.png'},{id:'view',path:folder+'camera_0_rgb.png'},
  {id:'verifiedUSD',path:folder+'scene.usda'}];
 const latest=selectScenePreviewArtifacts([...artifacts,...revised],'fixture',undefined,folder+'scene.png');
 assert.equal(latest.scene.id,'verified');assert.equal(latest.usd.id,'verifiedUSD');
 assert.deepEqual(latest.cameras.map(item=>item.id),['view']);
 assert.equal(sceneSpecBinding({active_scene:{stage:'simulation',build:{task_spec_hash:'verified-hash',preview_path:folder+'scene.png'}}}).hash,'verified-hash');
});
const out=new URL('../../frontend/.component-tests/task-scene/',import.meta.url);await mkdir(out,{recursive:true});
await build({entryPoints:['TaskScenePanel','ScenePreviewPanel','TaskUnderstanding'].map(name=>fileURLToPath(new URL(`../../frontend/src/${name}.tsx`,import.meta.url))),bundle:true,platform:'node',format:'esm',jsx:'automatic',outdir:fileURLToPath(out),outExtension:{'.js':'.mjs'},external:['react','react-dom','lucide-react'],logLevel:'silent'});
const {default:Panel}=await import(new URL('TaskScenePanel.mjs',out));const {default:Scene}=await import(new URL('ScenePreviewPanel.mjs',out));
const {default:Understanding}=await import(new URL('TaskUnderstanding.mjs',out));
verify('supervisor understanding is visible before synthesis and updates after synthesis',()=>{
 const preliminary={stage:'agent_dispatch',result:{understanding:{task_summary:'把箱子移到平台',phases:[{name:'抬箱'},{name:'移动'}]}}};
 const final={stage:'task_analysis',result:{proposal:{task_summary:'把箱子移到右侧平台',phases:[{name:'抬箱'},{name:'移动'},{name:'放稳'}]}}};
 const render=tasks=>renderToStaticMarkup(React.createElement(Understanding,{tasks,events:[],artifacts:[]}));
 assert.match(render([preliminary]),/任务总览.*总控初步理解.*任务目标.*把箱子移到平台.*动作步骤.*抬箱.*移动/);
 assert.match(render([preliminary,final]),/任务总览.*总控汇总.*把箱子移到右侧平台.*抬箱.*移动.*放稳/);
});
verify('demonstration overview remains visible while supervision is pending or running',()=>{
 const render=tasks=>renderToStaticMarkup(React.createElement(Understanding,{tasks,events:[],artifacts:[]}));
 assert.match(render([{stage:'agent_dispatch',status:'queued'}]),/任务总览.*总控尚未输出任务目标和步骤/);
 assert.match(render([{stage:'agent_dispatch',status:'running'}]),/任务总览.*总控正在分析示教/);
 assert.equal(render([{stage:'simulation',status:'running'}]),'');
});
verify('expert plan renders from the current attempt while physics is running',()=>{
 const tasks=[{stage:'task_analysis',result:{proposal:{task_summary:'推动滑块'}}},{stage:'simulation',id:'fixture:simulation',started_at:10}];
 const publish=(time,name)=>({task_id:'fixture:simulation',type:'expert.plan_ready',created_at:time,payload:{program:{phases:[{name,instruction:'根据示教推动滑块'}]}}});
 const html=renderToStaticMarkup(React.createElement(Understanding,{tasks,events:[publish(11,'向左滑动'),publish(9,'旧动作')],artifacts:[]}));
 assert.match(html,/动作专家计划.*向左滑动.*根据示教推动滑块/);
 assert.doesNotMatch(html,/旧动作/);
});
verify('unreviewed orientation does not show a candidate as the current scene',()=>{
 const runTasks=[{stage:'scene_build',status:'succeeded',result:{preview_path:artifacts[1].path,requires_visual_review:true}},
  {stage:'scene_validation',status:'waiting_input',result:{visual_comparison_status:'orientation_unverified'}}];
 const html=renderToStaticMarkup(React.createElement(Scene,{runId:'fixture',artifacts,tasks:runTasks}));
 assert.match(html,/摆放待确认/);assert.doesNotMatch(html,/scene-overview-image/);
});
verify('existing scene shows the calibration discovered on replay',()=>{
 const rig={bundle_id:'teach_02',status:'needs_scene_registration',message:'还需将标定板坐标注册到当前 Isaac 场景'};
 const tasks=[{stage:'simulation',status:'blocked',result:{rig_binding:rig}}];
 const html=renderToStaticMarkup(React.createElement(Panel,{run:{id:'fixture',input:{}},artifacts:[],tasks,onOpenRun:()=>{},initialEnvelope:envelope}));
 assert.match(html,/Nero · teach_02/);
 assert.match(html,/还需将标定板坐标注册到当前 Isaac 场景/);
});
verify('draft component shows unknowns and unverified scope',()=>{const html=renderToStaticMarkup(React.createElement(Panel,{run:{id:'fixture',input:{}},artifacts:[],tasks:[],onOpenRun:()=>{},initialEnvelope:envelope,initialProfiles:[{id:'fixture-profile',title:'TEST FIXTURE ONLY',description:'Not actual environment'}]}));assert.match(html,/未知 \/ 待补充/);assert.match(html,/not_verified/);assert.match(html,/test fixture unknown functional frame/);assert.match(html,/保存新 revision/);assert.match(html,/冻结离线契约/);assert.match(html,/构建场景预览/);assert.ok(!html.includes('scene-overview-image'))});
verify('scene component only uses actual artifact references',()=>{const html=renderToStaticMarkup(React.createElement(Scene,{runId:'fixture',artifacts,tasks:[]}));assert.match(html,/\/api\/artifacts\/latest/);assert.match(html,/\/api\/artifacts\/new-camera/);assert.ok(!html.includes('/api/artifacts/old-camera'));assert.match(html,/本次场景构建的 Isaac 全景渲染/)});
verify('no scene image when only demo contact sheet exists',()=>assert.equal(renderToStaticMarkup(React.createElement(Scene,{runId:'fixture',artifacts:artifacts.slice(-2),tasks:[]})),''));
verify('actual scene output overrides request binding',()=>{const binding=sceneSpecBinding({input:{spec_hash:'requested',spec_revision:1}},[{stage:'scene_build',result:{task_spec_hash:'actual',spec_revision:2}}]);assert.equal(binding.hash,'actual');assert.equal(binding.revision,2);assert.equal(binding.requestMismatch,true)});
verify('approximate scene retains the original source revision binding',()=>{const binding=sceneSpecBinding({input:{spec_hash:'source',spec_revision:1}},[{stage:'scene_build',result:{scene_approximation:true,task_spec_hash:'derived',source_task_spec_hash:'source',spec_revision:1}}]);assert.equal(binding.hash,'derived');assert.equal(binding.sourceHash,'source');assert.equal(binding.requestMismatch,false)});
verify('failed build render remains visibly incomplete',()=>{const html=renderToStaticMarkup(React.createElement(Scene,{runId:'fixture',artifacts,tasks:[{stage:'scene_build',status:'failed',result:{task_spec_hash:'actual',spec_revision:3}}],specHash:'requested',specRevision:2}));assert.match(html,/构建尚未完成/);assert.match(html,/构建记录与请求的任务快照不一致/);assert.match(html,/actual/)});
verify('source contract detects actual-result staleness and retains mismatch warning',()=>{const html=renderToStaticMarkup(React.createElement(Panel,{run:{id:'fixture',input:{source_run_id:'source',spec_hash:spec.content_hash,spec_revision:2}},artifacts,tasks:[{stage:'scene_build',status:'succeeded',result:{task_spec_hash:'older-output',spec_revision:1}}],onOpenRun:()=>{},initialEnvelope:envelope}));assert.match(html,/task-source-context stale/);assert.match(html,/当前预览对应旧快照/);assert.match(html,/构建记录与请求的任务快照不一致/);assert.match(html,/older-output/)});
verify('current actual binding avoids stale despite outdated request',()=>{const html=renderToStaticMarkup(React.createElement(Panel,{run:{id:'fixture',input:{source_run_id:'source',spec_hash:'old-request',spec_revision:1}},artifacts,tasks:[{stage:'scene_build',status:'succeeded',result:{task_spec_hash:spec.content_hash,spec_revision:2}}],onOpenRun:()=>{},initialEnvelope:envelope}));assert.ok(!html.includes('task-source-context stale'));assert.match(html,/构建记录与请求的任务快照不一致/)});
verify('programmatic geometry fixture keeps synthetic evidence boundary',()=>{const fixture=structuredClone(envelope);fixture.spec.source.synthetic_test_fixture=true;fixture.spec.objects[0].geometry_type='box';fixture.spec.objects[0].asset_path=null;const html=renderToStaticMarkup(React.createElement(Panel,{run:{id:'fixture',input:{}},artifacts:[],tasks:[],onOpenRun:()=>{},initialEnvelope:fixture}));assert.match(html,/程序生成场景夹具/);assert.match(html,/没有第二段录制示教/);assert.match(html,/程序几何 · 无外部网格/)});
const report={status:'passed',check_groups:groups.length,groups,evidence_scope:'frontend contract and SSR fixture verification only',fixtures_are_not_real_scene_results:true,model_requests:0,gpu_jobs_started:0,backend_restarted:false,hardware_commands_sent:0};const reportDir=new URL('../../runs/acceptance/frontend/',import.meta.url);await mkdir(reportDir,{recursive:true});await writeFile(new URL('task-scene-components-report.json',reportDir),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
