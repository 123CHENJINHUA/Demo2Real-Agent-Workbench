"""SQLite state owned by the application, one short connection per transaction."""
from pathlib import Path
import json, re, sqlite3, time, uuid
from contracts.artifacts import run_dependencies, run_directory
from contracts.analysis_agents import DISPATCH_STAGE, SYNTHESIS_STAGE
from contracts.task_dialog import WAITING_INPUT, UPSTREAM_BLOCKED, reply_text


def _dependent_task_ids(rows,stage):
    seen={stage}
    waiting={row['stage']:(row['id'],set(json.loads(row['deps']))) for row in rows}
    while eligible := [name for name,(_,deps) in waiting.items() if seen & deps]:
        seen.update(eligible)
        yield from (waiting.pop(name)[0] for name in eligible)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,name TEXT,input TEXT,status TEXT,created_at REAL,updated_at REAL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,run_id TEXT,stage TEXT,role TEXT,kind TEXT,deps TEXT,status TEXT,progress TEXT,result TEXT,error TEXT,attempt INTEGER DEFAULT 0,started_at REAL,finished_at REAL,heartbeat_at REAL,pid INTEGER);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,task_id TEXT,type TEXT,payload TEXT,created_at REAL);
                CREATE INDEX IF NOT EXISTS agent_handoff_events ON events(run_id,id)
                    WHERE type IN ('agent.handoff_started','agent.handoff_finished');
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,path TEXT,name TEXT,media_type TEXT,sha256 TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,category TEXT,status TEXT,summary TEXT,details TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,attempt INTEGER,status TEXT,pid INTEGER,log_path TEXT,exit_code INTEGER,started_at REAL,finished_at REAL);
                CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,role TEXT,kind TEXT,status TEXT,heartbeat_at REAL);
                CREATE TABLE IF NOT EXISTS task_questions(
                    id TEXT PRIMARY KEY,run_id TEXT NOT NULL,task_id TEXT NOT NULL,attempt INTEGER NOT NULL,
                    agent TEXT NOT NULL,prompt TEXT NOT NULL,context TEXT,report_path TEXT,
                    answer TEXT,created_at REAL NOT NULL,answered_at REAL,UNIQUE(task_id,attempt));
                CREATE INDEX IF NOT EXISTS task_questions_run ON task_questions(run_id,created_at);
                CREATE TABLE IF NOT EXISTS archived_runs(run_id TEXT PRIMARY KEY,archived_at REAL NOT NULL);
            """)
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db
    @staticmethod
    def decode(row):
        if row is None: return None
        item = dict(row)
        for key in ('input','deps','progress','result','payload','details'):
            if key in item and item[key] is not None:
                item[key] = json.loads(item[key])
        if 'input' in item and 'id' in item:
            item['output_directory'] = run_directory(item).as_posix()
        return item
    def query(self, sql, args=()):
        with self.connect() as db: return [self.decode(x) for x in db.execute(sql,args)]
    def execute(self, sql, args=()):
        with self.connect() as db: db.execute(sql,args)
    def event(self, run_id, task_id, kind, payload):
        with self.connect() as db:
            cur=db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',(run_id,task_id,kind,json.dumps(payload,ensure_ascii=False),time.time()))
            return cur.lastrowid
    @staticmethod
    def _insert_stage(db, rid, stage):
        tid=rid+':'+stage['id']
        kind=stage.get('kind','tool')
        db.execute('INSERT INTO tasks(id,run_id,stage,role,kind,deps,status,progress) VALUES(?,?,?,?,?,?,?,?)',(tid,rid,stage['id'],stage['role'],kind,json.dumps(stage.get('deps',[])),'queued','{}'))
        db.execute('INSERT INTO agents VALUES(?,?,?,?,?,?,?)',(tid,rid,tid,stage['role'],kind,'queued',None))
    def create_run(self, name, data, stages, *, demo_run_id=None):
        rid=uuid.uuid4().hex[:16]; now=time.time()
        if demo_run_id is not None:
            if not re.fullmatch(r'[0-9a-f]{16}', demo_run_id):
                raise ValueError('Invalid demonstration run ID')
            data = dict(data, demo_run_id=demo_run_id)
        dependencies = run_dependencies(data)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if dependencies and any(row['run_id'] in dependencies for row in db.execute('SELECT run_id FROM archived_runs')):
                raise ValueError('引用的运行已删除，请重新选择源版本')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,name,json.dumps(data,ensure_ascii=False),'queued',now,now))
            for stage in stages:
                self._insert_stage(db,rid,stage)
        self.event(rid,None,'run.created',{'name':name})
        return self.get_run(rid)
    def list_runs(self):
        return self.query('SELECT * FROM runs WHERE id NOT IN (SELECT run_id FROM archived_runs) ORDER BY created_at DESC')

    def run_exists(self,rid):
        return bool(self.query('SELECT id FROM runs WHERE id=? AND id NOT IN (SELECT run_id FROM archived_runs)',(rid,)))

    def get_run(self,rid):
        rows=self.query('SELECT * FROM runs WHERE id=? AND id NOT IN (SELECT run_id FROM archived_runs)',(rid,))
        if not rows:return None
        run=rows[0];run['tasks']=self.query('SELECT * FROM tasks WHERE run_id=? ORDER BY rowid',(rid,))
        run['active_scene']=self.active_scenes([rid]).get(rid)
        run['collaboration_events']=self.query("""SELECT id,task_id,type,payload,created_at FROM events
            WHERE run_id=? AND type IN ('agent.handoff_started','agent.handoff_finished')
            ORDER BY id DESC LIMIT 160""",(rid,))[::-1]
        run['agents']=self.query('SELECT * FROM agents WHERE run_id=?',(rid,))
        run['artifacts']=self.query('SELECT * FROM artifacts WHERE run_id=?',(rid,))
        run['incidents']=self.query('SELECT * FROM incidents WHERE run_id=?',(rid,))
        run['jobs']=self.query('SELECT * FROM jobs WHERE run_id=? ORDER BY started_at',(rid,))
        run['questions']=self.questions(rid)
        return run

    def archive_run(self,rid):
        """Remove one run from the workspace while keeping its pinned files and evidence."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if run is None:
                raise KeyError(rid)
            archived=db.execute('SELECT archived_at FROM archived_runs WHERE run_id=?',(rid,)).fetchone()
            if archived:
                return dict(archived)
            if (run['status'] in ('queued','pending','running')
                    or db.execute("SELECT 1 FROM jobs WHERE run_id=? AND status='running'",(rid,)).fetchone()
                    or db.execute("SELECT 1 FROM tasks WHERE run_id=? AND status='running'",(rid,)).fetchone()):
                raise ValueError('运行仍在执行或排队，请先取消或等待结束')
            rows=db.execute('SELECT id,name,input FROM runs WHERE id!=? AND id NOT IN (SELECT run_id FROM archived_runs)',(rid,))
            dependent=next((row for row in rows if rid in run_dependencies(json.loads(row['input']))),None)
            if dependent:
                raise ValueError(f'「{dependent["name"]}」({dependent["id"]}) 仍引用此版本，请先处理该运行')
            now=time.time()
            db.execute('INSERT INTO archived_runs VALUES(?,?)',(rid,now))
            db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                       (rid,None,'run.archived','{}',now))
            return {'archived_at':now}

    def questions(self,rid):
        return self.query("""SELECT q.*,t.stage,
            CASE WHEN q.answer IS NULL AND t.status='waiting_input' AND q.attempt=t.attempt THEN 1 ELSE 0 END AS pending
            FROM task_questions q JOIN tasks t ON t.id=q.task_id WHERE q.run_id=? ORDER BY q.created_at""",(rid,))

    def revise_pending_question(self,qid,original_prompt,question):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute("""SELECT q.run_id,q.task_id,q.prompt,q.context FROM task_questions q
                JOIN tasks t ON t.id=q.task_id WHERE q.id=? AND q.prompt=? AND q.answer IS NULL
                AND t.status='waiting_input' AND q.attempt=t.attempt
                AND q.run_id NOT IN (SELECT run_id FROM archived_runs)""",(qid,original_prompt)).fetchone()
            if current is None:
                return
            db.execute('UPDATE task_questions SET agent=?,prompt=?,context=?,report_path=? WHERE id=?',
                       (question['agent'],question['prompt'],question['context'],question['report_path'],qid))
            db.execute("UPDATE tasks SET result=json_set(result,'$.question',json(?)) WHERE id=?",
                       (json.dumps(question,ensure_ascii=False),current['task_id']))
            payload={'question_id':qid,'previous_prompt':current['prompt'],'previous_context':current['context']}
            db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                       (current['run_id'],current['task_id'],'agent.question_revised',json.dumps(payload,ensure_ascii=False),time.time()))

    def close_pending_question(self,qid,result):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute("""SELECT q.run_id,q.task_id,t.stage FROM task_questions q JOIN tasks t ON t.id=q.task_id
                WHERE q.id=? AND q.answer IS NULL AND t.status='waiting_input' AND q.attempt=t.attempt
                AND q.run_id NOT IN (SELECT run_id FROM archived_runs)""",(qid,)).fetchone()
            if current is None:
                return
            rid,tid=current['run_id'],current['task_id']
            result={key:value for key,value in result.items() if key!='question'}
            db.execute("UPDATE tasks SET status='blocked',error=?,result=? WHERE id=?",
                       (result['reason'],json.dumps(result,ensure_ascii=False),tid))
            queued=db.execute("SELECT id,stage,deps FROM tasks WHERE run_id=? AND status='queued'",(rid,))
            dependents=list(_dependent_task_ids(queued,current['stage']))
            db.executemany("UPDATE tasks SET status='blocked',error=? WHERE id=?",
                           [(UPSTREAM_BLOCKED,dependent) for dependent in dependents])
            db.executemany("UPDATE agents SET status='blocked' WHERE task_id=?",[(task_id,) for task_id in [tid,*dependents]])
            now=time.time()
            db.execute("""UPDATE runs SET status='blocked',updated_at=? WHERE id=? AND status='waiting_input'
                AND NOT EXISTS (SELECT 1 FROM tasks WHERE run_id=? AND status IN ('waiting_input','queued','running'))""",(now,rid,rid))
            db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                       (rid,tid,'agent.question_closed',json.dumps({'question_id':qid,'reason':result['reason']},ensure_ascii=False),now))

    def answer_question(self,rid,qid,value):
        answer = reply_text(value)
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT q.task_id,q.answer,q.attempt,t.stage,t.status,t.attempt AS current_attempt
                              FROM task_questions q JOIN tasks t ON t.id=q.task_id WHERE q.run_id=? AND q.id=?
                              AND q.run_id NOT IN (SELECT run_id FROM archived_runs)''',(rid,qid)).fetchone()
            if not row or row['status']!=WAITING_INPUT or row['answer'] is not None or row['attempt']!=row['current_attempt']:
                raise ValueError('此问题已处理或已结束，请刷新后查看')
            db.execute('UPDATE task_questions SET answer=?,answered_at=? WHERE id=?',(answer,now,qid))
            downstream=db.execute("""SELECT id,stage,deps FROM tasks WHERE run_id=? AND status='blocked' AND error=? ORDER BY rowid""",
                                  (rid,UPSTREAM_BLOCKED)).fetchall()
            requeue=[row['task_id'],*_dependent_task_ids(downstream,row['stage'])]
            db.executemany("""UPDATE tasks SET status='queued',error=NULL,result=NULL,progress='{}',
                          started_at=NULL,finished_at=NULL,heartbeat_at=NULL,pid=NULL WHERE id=?""",[(tid,) for tid in requeue])
            db.executemany("UPDATE agents SET status='queued',heartbeat_at=NULL WHERE task_id=?",[(tid,) for tid in requeue])
            db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=?",(now,rid))
            db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                       (rid,row['task_id'],'agent.answered',json.dumps({'question_id':qid}),now))
    def set_pending_object_models(self,rid,sources):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=db.execute('SELECT input,status FROM runs WHERE id=? AND id NOT IN (SELECT run_id FROM archived_runs)',(rid,)).fetchone()
            geometry=db.execute("SELECT status FROM tasks WHERE run_id=? AND stage='geometry'",(rid,)).fetchone()
            if not run or not geometry or run['status'] not in ('failed','blocked','interrupted','cancelled',WAITING_INPUT) or geometry['status'] not in ('failed','blocked','interrupted','cancelled','needs_configuration',WAITING_INPUT):
                raise ValueError('任务在等待资产审查时才能修改模型选择')
            if db.execute("SELECT 1 FROM tasks WHERE run_id=? AND status='running'",(rid,)).fetchone():
                raise ValueError('请在当前任务停止后修改模型选择')
            inputs=json.loads(run['input'])
            inputs.pop('asset_paths',None)
            inputs['model_sources']=sources
            db.execute('UPDATE runs SET input=?,updated_at=? WHERE id=?',(json.dumps(inputs,ensure_ascii=False),time.time(),rid))
            db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                       (rid,None,'geometry.models_selected',json.dumps({'model_count':len(sources)}),time.time()))
    def active_scenes(self, run_ids):
        if not run_ids:
            return {}
        params = tuple(run_ids)
        slots = ','.join('?' for _ in params)
        rows = self.query(f"""SELECT run_id,payload FROM events WHERE id IN (
            SELECT MAX(id) FROM events WHERE type='scene.published' AND run_id IN ({slots}) GROUP BY run_id)""",params)
        active = {row['run_id']:row['payload'] for row in rows}
        legacy = {}
        for row in self.query(f"""SELECT run_id,stage,result FROM tasks WHERE run_id IN ({slots})
            AND result IS NOT NULL AND stage IN ('scene_build','scene_validation','simulation')""",params):
            if row['run_id'] not in active and isinstance(row['result'],dict):
                legacy.setdefault(row['run_id'],{})[row['stage']] = row['result']
        for rid, results in legacy.items():
            simulation = results.get('simulation',{})
            validation = results.get('scene_validation',{})
            build = results.get('scene_build',{})
            if ref := simulation.get('repaired_scene_manifest'):
                active[rid] = {'stage':'simulation','build':simulation.get('resolved_scene_build') or {
                    'manifest_path':ref,'preview_path':str(Path(ref).with_name('scene.png')),
                    'task_spec_hash':simulation.get('scene_task_spec_hash')},
                    'validation_path':simulation.get('repaired_scene_validation_path'),'repair_report_path':simulation.get('repair_report_path')}
            elif resolved := validation.get('resolved_scene_build'):
                active[rid] = {'stage':'scene_validation','build':resolved,'validation_path':validation.get('report_path'),
                               'repair_report_path':validation.get('repair_report_path'),
                               'visual_comparison_path':validation.get('visual_comparison_path'),
                               'visual_comparison_status':validation.get('visual_comparison_status')}
            elif build.get('requires_visual_review'):
                active[rid] = {'stage':'scene_validation','build':{},'review_pending':True,
                               'visual_comparison_path':validation.get('visual_comparison_path'),
                               'visual_comparison_status':validation.get('visual_comparison_status')}
            elif build.get('preview_path'):
                active[rid] = {'stage':'scene_build','build':build,'validation_path':validation.get('report_path')}
        return active

    def run_previews(self, runs):
        prefixes = {run['id']: run['output_directory'] + '/' for run in runs}
        active = self.active_scenes(prefixes)
        previews, scores = {}, {}
        rows = self.query("""
            SELECT id, run_id, path, created_at FROM artifacts
            WHERE run_id NOT IN (SELECT run_id FROM archived_runs) AND media_type IN ('image/png', 'image/jpeg') AND (
                path LIKE '%/scene.png' OR
                path LIKE '%/simulation/%/vision/visualization/%/panels.png' OR
                path LIKE '%/data_audit/video_%/frame_%.jpg'
            ) ORDER BY created_at DESC, rowid DESC
        """)
        for row in rows:
            rid, path = row['run_id'], row['path']
            prefix = prefixes.get(rid)
            if not prefix or not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
            if path == active.get(rid,{}).get('build',{}).get('preview_path'):
                kind, score = 'scene', (4,)
            elif relative.startswith('scene_build/') and active.get(rid,{}).get('review_pending'):
                continue
            elif match := re.fullmatch(r'scene_build/(?:build_|scene_|preview_|isaac_)?(\d+)/scene\.png', relative):
                kind, score = 'scene', (3, int(match[1]))
            elif re.fullmatch(r'scene_build/[^/]+/scene\.png', relative):
                kind, score = 'scene', (3, 0)
            elif match := re.fullmatch(r'simulation/isaac_(\d+)/(?:[^/]+/)*vision/visualization/frame_(\d+(?:\.\d+)?)/panels\.png', relative):
                kind, score = 'rgbd', (2, int(match[1]), float(match[2]))
            elif match := re.fullmatch(r'data_audit/video_(\d+)/frame_(\d+)\.jpg', relative):
                kind, score = 'demonstration', (1, -int(match[1]), -abs(int(match[2]) - 2))
            else:
                continue
            if rid not in scores or score > scores[rid]:
                scores[rid] = score
                previews[rid] = {'artifact_id': row['id'], 'kind': kind}
        return previews
    def transition(self,tid,status,*,delegated=None,question=None,**fields):
        allowed={'progress','result','error','started_at','finished_at','heartbeat_at','pid','attempt'}
        if set(fields)-allowed: raise ValueError('unknown task fields')
        columns=['status=?'];values=[status]
        for key,value in fields.items():
            columns.append(key+'=?');values.append(json.dumps(value,ensure_ascii=False) if key in ('progress','result') else value)
        values.append(tid)
        with self.connect() as db:
            if delegated is not None:
                # Publish the specialists and their join dependency with the dispatch result.
                parent=db.execute('SELECT run_id,stage,status FROM tasks WHERE id=?',(tid,)).fetchone()
                if parent is None or parent['stage']!=DISPATCH_STAGE or parent['status']!='running' or status!='succeeded':
                    raise ValueError('只有正在执行的派工节点可登记成功派工')
                rid=parent['run_id']
                for stage in delegated:
                    self._insert_stage(db,rid,stage)
                deps=[DISPATCH_STAGE,*(stage['id'] for stage in delegated)]
                changed=db.execute('UPDATE tasks SET deps=? WHERE id=? AND status=?',(json.dumps(deps),rid+':'+SYNTHESIS_STAGE,'queued'))
                if changed.rowcount!=1:raise ValueError('汇总节点缺失或已启动')
                db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',(rid,tid,'agents.dispatched',json.dumps({'roles':deps[1:]}),time.time()))
            db.execute('UPDATE tasks SET '+','.join(columns)+' WHERE id=?',values)
            db.execute('UPDATE agents SET status=?,heartbeat_at=? WHERE task_id=?',(status,time.time(),tid))
            if question is not None:
                row=db.execute('SELECT run_id,attempt FROM tasks WHERE id=?',(tid,)).fetchone()
                qid=uuid.uuid4().hex
                db.execute('''INSERT INTO task_questions(id,run_id,task_id,attempt,agent,prompt,context,report_path,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?)''',
                           (qid,row['run_id'],tid,row['attempt'],question['agent'],question['prompt'],
                            question.get('context'),question.get('report_path'),time.time()))
                db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',
                           (row['run_id'],tid,'agent.question',json.dumps({'question_id':qid,'agent':question['agent']},ensure_ascii=False),time.time()))
        row=self.query('SELECT run_id FROM tasks WHERE id=?',(tid,))[0]
        self.event(row['run_id'],tid,'task.'+status,{k:v for k,v in fields.items() if k!='result'})
    def incident(self,rid,tid,category,summary,details=None):
        iid=uuid.uuid4().hex
        self.execute('INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?)',(iid,rid,tid,category,'open',summary,json.dumps(details or {},ensure_ascii=False),time.time()))
        self.event(rid,tid,'incident.created',{'id':iid,'category':category,'summary':summary})
        return iid
    def add_artifact(self,rid,tid,record):
        existing=self.query('SELECT id FROM artifacts WHERE run_id=? AND path=? AND sha256=?',(rid,record['path'],record['sha256']))
        if existing:return existing[0]['id']
        identifier=uuid.uuid4().hex
        self.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',(identifier,rid,tid,record['path'],record.get('name',record['path'].rsplit('/',1)[-1]),record.get('media_type','application/json'),record['sha256'],time.time()))
        return identifier

    def register_artifact(self,root,rid,tid,relative,name=None):
        import hashlib
        import mimetypes

        path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        with path.open('rb') as stream:
            hasher=hashlib.sha256()
            while chunk:=stream.read(1024*1024):
                hasher.update(chunk)
        digest=hasher.hexdigest()
        return self.add_artifact(rid,tid,{'path':path.relative_to(root).as_posix(),'name':name or path.name,
                                         'media_type':mimetypes.guess_type(path)[0] or 'application/octet-stream','sha256':digest})
    def recover(self):
        self.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status='running'",(time.time(),))
        for task in self.query("SELECT * FROM tasks WHERE status='running'"):
            self.transition(task['id'],'interrupted',error='服务重启：任务未完成；请显式重试。',finished_at=time.time(),pid=None)
        self.execute("UPDATE runs SET status='interrupted',updated_at=? WHERE status='running'",(time.time(),))
