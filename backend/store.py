"""SQLite state owned by the application, one short connection per transaction."""
from pathlib import Path
import json, sqlite3, time, uuid

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
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,path TEXT,name TEXT,media_type TEXT,sha256 TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,category TEXT,status TEXT,summary TEXT,details TEXT,created_at REAL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,attempt INTEGER,status TEXT,pid INTEGER,log_path TEXT,exit_code INTEGER,started_at REAL,finished_at REAL);
                CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,run_id TEXT,task_id TEXT,role TEXT,kind TEXT,status TEXT,heartbeat_at REAL);
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
        return item
    def query(self, sql, args=()):
        with self.connect() as db: return [self.decode(x) for x in db.execute(sql,args)]
    def execute(self, sql, args=()):
        with self.connect() as db: db.execute(sql,args)
    def event(self, run_id, task_id, kind, payload):
        with self.connect() as db:
            cur=db.execute('INSERT INTO events(run_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?)',(run_id,task_id,kind,json.dumps(payload,ensure_ascii=False),time.time()))
            return cur.lastrowid
    def create_run(self, name, data, stages):
        rid=uuid.uuid4().hex[:16]; now=time.time()
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,name,json.dumps(data,ensure_ascii=False),'queued',now,now))
            for stage in stages:
                tid=rid+':'+stage['id']
                db.execute('INSERT INTO tasks(id,run_id,stage,role,kind,deps,status,progress) VALUES(?,?,?,?,?,?,?,?)',(tid,rid,stage['id'],stage['role'],stage.get('kind','tool'),json.dumps(stage.get('deps',[])),'queued','{}'))
                db.execute('INSERT INTO agents VALUES(?,?,?,?,?,?,?)',(tid,rid,tid,stage['role'],stage.get('kind','tool'),'queued',None))
        self.event(rid,None,'run.created',{'name':name})
        return self.get_run(rid)
    def get_run(self,rid):
        rows=self.query('SELECT * FROM runs WHERE id=?',(rid,))
        if not rows:return None
        run=rows[0];run['tasks']=self.query('SELECT * FROM tasks WHERE run_id=? ORDER BY rowid',(rid,))
        run['agents']=self.query('SELECT * FROM agents WHERE run_id=?',(rid,))
        run['artifacts']=self.query('SELECT * FROM artifacts WHERE run_id=?',(rid,))
        run['incidents']=self.query('SELECT * FROM incidents WHERE run_id=?',(rid,))
        run['jobs']=self.query('SELECT * FROM jobs WHERE run_id=? ORDER BY started_at',(rid,))
        return run
    def transition(self,tid,status,**fields):
        allowed={'progress','result','error','started_at','finished_at','heartbeat_at','pid','attempt'}
        if set(fields)-allowed: raise ValueError('unknown task fields')
        columns=['status=?'];values=[status]
        for key,value in fields.items():
            columns.append(key+'=?');values.append(json.dumps(value,ensure_ascii=False) if key in ('progress','result') else value)
        values.append(tid)
        with self.connect() as db:
            db.execute('UPDATE tasks SET '+','.join(columns)+' WHERE id=?',values)
            db.execute('UPDATE agents SET status=?,heartbeat_at=? WHERE task_id=?',(status,time.time(),tid))
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
    def recover(self):
        self.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status='running'",(time.time(),))
        for task in self.query("SELECT * FROM tasks WHERE status='running'"):
            self.transition(task['id'],'interrupted',error='服务重启：任务未完成；请显式重试。',finished_at=time.time(),pid=None)
        self.execute("UPDATE runs SET status='interrupted',updated_at=? WHERE status='running'",(time.time(),))
