import {useEffect, useState} from 'react';
import type {FormEvent, ReactNode} from 'react';
import {ArrowUpRight, Check, ChevronLeft, ChevronRight, FileText, LoaderCircle, Plus, Search, X} from 'lucide-react';
import {api, post} from './api';

const categories: Record<string, string> = {
  data_quality: '数据质量', task_semantics: '任务语义', geometry_function: '功能几何',
  frames_calibration: '坐标与标定', physics_contact: '物理与接触', perception_estimation: '感知估计',
  planning_control: '规划与控制', policy_learning: '策略与学习', runtime_environment: '运行环境',
  evaluation_metrics: '评价判据', workflow_integrity: '工作流完整性', hardware_io: '历史硬件记录',
};
const stages: Record<string, string> = {
  data_ingestion: '数据导入', task_analysis: '任务分析', asset_modeling: '资产建模', scene_build: '场景构建',
  perception_validation: '感知核验', simulation_test: '仿真测试', training: '训练',
  deployment: '策略归档', runtime: '运行时', hardware_test: '历史硬件测试',
};
const statuses: Record<string, string> = {
  open: '未解决', reported: '待核实', mitigation_recorded: '已记录缓解措施',
  partially_verified: '局部验证', verified_in_scope: '范围内验证', superseded: '已有后续记录',
};
const diagnoses: Record<string, string> = {
  unknown: '未知', hypothesis: '假设', supported_by_diagnostics: '诊断支持', confirmed: '已证实',
};
const severities: Record<string, string> = {blocking:'需处理', review:'待审查'};
const options = (labels: Record<string, string>) =>
  Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>);
const timestamp = (value?: number | string) => !value ? '—' :
  new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', {hour12: false});
const references = (text: string) => text.split('\n').map(line => line.trim()).filter(Boolean);

type DemoRun = {id: string; name: string};
type Occurrence = {id: string; run_id?: string; created_at?: string | number; asset_id?: string};
type IncidentRow = {
  id: string; title?: string; summary: string; category?: string; stage?: string; status: string;
  created_at?: string | number; source: string; record_origin?: string; finding_code?: string; severity?: string;
  occurrences: Occurrence[];
};
type Evidence = {path: string; expected_sha256?: string; exists: boolean; verified: boolean};
type RecordData = {
  incident_id?: string; case_id?: string; title?: string; finding_title?: string; summary?: string; observed_behavior?: string;
  expected_behavior?: string; trigger_conditions?: string | string[]; facts?: string[];
  stage?: string; stages?: string[]; primary_category?: string; status?: string; run_id?: string;
  revision?: number; timestamp_utc?: string | number; recorded_on?: string; revision_note?: string;
  revision_reason?: string; diagnosis?: {state?: string; explanation?: string; alternatives?: string[]};
  source_mitigation?: {actions?: string[]; attempts?: {change?: string; outcome?: string; limits?: string}[]};
  verification?: {scope?: string; passed_subchecks?: string[]; failed_or_unverified?: string[]};
  validation_scope?: string; preventive_checks?: string[]; reusable_checklist?: string[];
  applicability?: {when?: string[]; not_when?: string[]; parameters_not_to_copy?: string[]};
};
type Case = RecordData & {case_id: string; title: string; evidence_verification?: Evidence[]};
type Detail = {record: RecordData; revisions: RecordData[]; evidence_verification: Evidence[]};
type IncidentPage = {incidents: IncidentRow[]; total: number; report_count: number; page_size: number};
const emptyPage: IncidentPage = {incidents: [], total: 0, report_count: 0, page_size: 15};

function HistoryError({error}: {error: string}) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

function SummaryList({title, values}: {title: string; values?: string[]}) {
  return values?.length ? <section><h3>{title}</h3><ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul></section> : null;
}

function Dialog({title, close, children}: {title: string; close: () => void; children: ReactNode}) {
  return <div className="modal-backdrop" onKeyDown={event => {if(event.key==='Escape')close()}}>
    <div className="modal wide history-modal" role="dialog" aria-modal="true" aria-labelledby="history-dialog-title">
      <div className="modal-header"><h2 id="history-dialog-title">{title}</h2><button autoFocus type="button" onClick={close} className="icon-button" aria-label="关闭记录"><X size={20}/></button></div>
      {children}
    </div>
  </div>;
}

export default function Knowledge({runs, onOpenRun}: {runs: DemoRun[]; onOpenRun: (id: string) => void}) {
  const [tab, setTab] = useState<'cases'|'incidents'>('cases');
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [stage, setStage] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [cases, setCases] = useState<Case[]>([]);
  const [incidents, setIncidents] = useState(emptyPage);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [selectedCase, setSelectedCase] = useState<Case|null>(null);
  const [selectedRow, setSelectedRow] = useState<IncidentRow|null>(null);
  const [recordId, setRecordId] = useState('');
  const [create, setCreate] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const filters = new URLSearchParams({query, category, stage});
    setBusy(true);
    setError('');
    const update = tab === 'cases'
      ? api<{cases: Case[]}>('/experience?'+filters+'&cases_only=true', {signal: controller.signal})
          .then(result => {if (!controller.signal.aborted) setCases(result.cases)})
      : api<IncidentPage>('/incidents?'+filters+'&status='+encodeURIComponent(status)+'&page='+page, {signal: controller.signal})
          .then(result => {if (!controller.signal.aborted) setIncidents(result)});
    void update.catch(error => {if (!controller.signal.aborted) setError(error.message)})
      .finally(() => {if (!controller.signal.aborted) setBusy(false)});
    return () => controller.abort();
  }, [tab, query, category, stage, status, page, refresh]);

  const visibleCases = status ? cases.filter(item => item.status === status) : cases;
  const pages = Math.max(1, Math.ceil(incidents.total/incidents.page_size));
  function submitSearch(text: string) {
    setDraft(text);
    setQuery(text.trim());
    setPage(1);
  }
  function changeTab(value: 'cases'|'incidents') {
    setTab(value);
    setPage(1);
  }
  function openRecord(row: IncidentRow) {
    setSelectedRow(row);
    setRecordId(row.id);
  }
  function closeRecord() {
    setSelectedRow(null);
    setRecordId('');
  }

  return <>
    <header className="page-heading">
      <h1>经验与失败记录</h1>
      <button className="button primary" onClick={()=>{setNotice('');setCreate(true)}}><Plus size={17}/>记录问题</button>
    </header>
    <div className="run-detail-tabs history-tabs">
      <button type="button" className={tab==='cases'?'active':''} aria-pressed={tab==='cases'} onClick={()=>changeTab('cases')}>经验案例</button>
      <button type="button" className={tab==='incidents'?'active':''} aria-pressed={tab==='incidents'} onClick={()=>changeTab('incidents')}>问题记录</button>
    </div>
    <section className="panel history-controls" aria-label="历史记录筛选">
      <form className="history-search" onSubmit={event=>{event.preventDefault();submitSearch(draft)}}>
        <Search size={18}/><input aria-label="搜索历史记录" value={draft} onChange={event=>setDraft(event.target.value)} placeholder={tab==='cases'?'搜索机构、现象或案例编号':'搜索现象、检查项或运行编号'}/>
        <button type="submit" className="button primary">搜索</button>
      </form>
      <div className="history-filters">
        <select aria-label="类别" value={category} onChange={event=>{setCategory(event.target.value);setPage(1)}}><option value="">全部类别</option>{options(categories)}</select>
        <select aria-label="阶段" value={stage} onChange={event=>{setStage(event.target.value);setPage(1)}}><option value="">全部阶段</option>{options(stages)}</select>
        <select aria-label="处理状态" value={status} onChange={event=>{setStatus(event.target.value);setPage(1)}}><option value="">全部状态</option>{options(statuses)}</select>
        {(query||category||stage||status)&&<button type="button" className="text-button" onClick={()=>{submitSearch('');setCategory('');setStage('');setStatus('')}}>清空筛选</button>}
      </div>
      {tab==='cases'&&!query&&<div className="history-suggestions"><span>常见现象</span>{['卡槽','位姿','松手'].map(term=><button className="text-button" key={term} onClick={()=>submitSearch(term)}>{term}</button>)}</div>}
    </section>
    {notice&&<p className="history-notice" role="status"><Check size={16}/>{notice}</p>}
    <HistoryError error={error}/>
    {busy ? <div className="loading"><LoaderCircle className="spin" size={18}/>正在读取历史记录</div> : !error && (
      tab==='cases'
        ? <section aria-label="经验案例列表"><p className="history-count">{visibleCases.length} 条案例</p>
          {visibleCases.length ? <div className="history-list">{visibleCases.map(item=><button className="panel knowledge-card history-row" key={item.case_id} onClick={()=>setSelectedCase(item)}>
            <div className="history-labels"><code>{item.case_id}</code><span>{categories[item.primary_category||'']||item.primary_category}</span><span>{statuses[item.status||'']||item.status}</span></div>
            <strong>{item.title}</strong><p>{item.facts?.[0]||item.summary}</p><ArrowUpRight className="history-row-arrow" size={17}/>
          </button>)}</div> : <p className="panel history-empty">没有符合条件的案例。</p>}
        </section>
        : <section aria-label="问题记录列表"><p className="history-count">{incidents.total} 组记录 · {incidents.report_count} 次记录</p>
          {incidents.incidents.length ? <div className="history-list">{incidents.incidents.map(row=><button className="panel history-row" key={row.id} onClick={()=>openRecord(row)}>
            <div className="history-labels"><code>{row.finding_code||row.id}</code><span>{categories[row.category||'']||row.category}</span><span>{stages[row.stage||'']||row.stage}</span><span>{severities[row.severity||'']}</span><span>{statuses[row.status]||row.status}</span></div>
            <strong>{row.title||row.summary}</strong>
            <p>{row.record_origin==='manual'?'人工记录':row.source==='sqlite'?'运行记录':'归档记录'} · {timestamp(row.created_at)}{row.occurrences.length>1&&<> · {row.occurrences.length} 次发生</>}</p>
            <ArrowUpRight className="history-row-arrow" size={17}/>
          </button>)}</div> : <p className="panel history-empty">没有符合条件的问题。</p>}
          {pages>1&&<div className="history-pagination"><button className="button secondary small" disabled={page<=1} onClick={()=>setPage(page-1)}><ChevronLeft size={16}/>上一页</button><span>第 {page} / {pages} 页</span><button className="button secondary small" disabled={page>=pages} onClick={()=>setPage(page+1)}>下一页<ChevronRight size={16}/></button></div>}
        </section>
    )}
    {selectedCase&&<Dialog title={selectedCase.title} close={()=>setSelectedCase(null)}><RecordSummary record={selectedCase} evidence={selectedCase.evidence_verification||[]} runs={runs} onOpenRun={onOpenRun}/><RawRecord value={selectedCase}/></Dialog>}
    {recordId&&<IncidentDetail key={recordId} id={recordId} row={selectedRow} onChoose={setRecordId} onClose={closeRecord} runs={runs} onOpenRun={onOpenRun} onSaved={()=>setRefresh(value=>value+1)}/>}
    {create&&<NewObservation runs={runs} close={()=>setCreate(false)} onSaved={id=>{
      setCreate(false);
      setTab('incidents');
      submitSearch('');
      setCategory('');setStage('');setStatus('');
      setRefresh(value=>value+1);
      setNotice('已保存问题 '+id);
      setSelectedRow(null);setRecordId(id);
    }}/>}
  </>;
}

function RecordSummary({record, evidence, runs, onOpenRun}: {record: RecordData; evidence: Evidence[]; runs: DemoRun[]; onOpenRun: (id: string) => void}) {
  const stage = record.stage||record.stages?.[0]||'';
  const scope = record.verification?.scope||record.validation_scope;
  const conditions = Array.isArray(record.trigger_conditions) ? record.trigger_conditions.join('、') : record.trigger_conditions;
  return <div className="history-detail">
    <div className="history-labels"><code>{record.incident_id||record.case_id}</code><span>{categories[record.primary_category||'']||record.primary_category}</span><span>{stages[stage]||stage}</span><span>{statuses[record.status||'']||record.status}</span></div>
    {record.run_id&&(runs.some(run=>run.id===record.run_id)
      ? <button type="button" className="text-button history-run-link" onClick={()=>onOpenRun(record.run_id!)}>查看运行 {record.run_id}<ArrowUpRight size={15}/></button>
      : <p className="history-run-link">关联运行 <code>{record.run_id}</code></p>)}
    <SummaryList title="观察事实" values={record.facts?.length?record.facts:[record.observed_behavior||record.summary||''].filter(Boolean)}/>
    {record.expected_behavior&&<section><h3>预期结果</h3><p>{record.expected_behavior}</p></section>}
    {conditions&&<section><h3>触发条件</h3><p>{conditions}</p></section>}
    {record.diagnosis&&<section><h3>诊断 · {diagnoses[record.diagnosis.state||'']||'待核实'}</h3>{record.diagnosis.explanation&&<p>{record.diagnosis.explanation}</p>}</section>}
    <SummaryList title="候选解释" values={record.diagnosis?.alternatives}/>
    <SummaryList title="处理尝试" values={record.source_mitigation?.attempts?.map(attempt=>[attempt.change,attempt.outcome,attempt.limits].filter(Boolean).join(' · '))}/>
    <SummaryList title="缓解措施" values={record.source_mitigation?.actions}/>
    {scope&&<section><h3>验证范围</h3><p>{scope==='module_reported_failure_only_no_hardware_inference'?'本次模块检查及其记录的证据':scope}</p></section>}
    <SummaryList title="通过的子项" values={record.verification?.passed_subchecks}/>
    <SummaryList title="未通过或待验证" values={record.verification?.failed_or_unverified}/>
    <SummaryList title="适用条件" values={record.applicability?.when}/>
    <SummaryList title="复用前的检查项" values={record.preventive_checks||record.reusable_checklist}/>
    <SummaryList title="需重新核定的参数" values={record.applicability?.parameters_not_to_copy}/>
    {!!evidence.length&&<section><h3>证据快照</h3><ul className="history-evidence">{evidence.map(item=><li key={item.path}><FileText size={16}/><code>{item.path}</code><span>{!item.exists?'文件缺失':item.verified?'哈希一致':item.expected_sha256?'哈希不一致':'待核对哈希'}</span></li>)}</ul></section>}
  </div>;
}

function RawRecord({value}: {value: RecordData}) {
  return <details className="history-raw"><summary>查看原始记录</summary><pre className="json-view">{JSON.stringify(value,null,2)}</pre></details>;
}

function IncidentDetail({id, row, onChoose, onClose, runs, onOpenRun, onSaved}: {
  id: string; row: IncidentRow|null; onChoose: (id: string) => void; onClose: () => void;
  runs: DemoRun[]; onOpenRun: (id: string) => void; onSaved: () => void;
}) {
  const [detail, setDetail] = useState<Detail|null>(null);
  const [loadError, setLoadError] = useState('');
  const [append, setAppend] = useState(false);
  const [note, setNote] = useState('');
  const [check, setCheck] = useState('');
  const [evidence, setEvidence] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    api<Detail>('/incidents/'+encodeURIComponent(id), {signal: controller.signal})
      .then(data=>{if(!controller.signal.aborted)setDetail(data)})
      .catch(error=>{if(!controller.signal.aborted)setLoadError(error.message)});
    return ()=>controller.abort();
  },[id]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);setSaveError('');
    try {
      const path='/incidents/'+encodeURIComponent(id);
      await post(path+'/revisions',{expected_revision:detail?.record.revision,note,check,evidence:references(evidence)});
      setAppend(false);setNote('');setCheck('');setEvidence('');
      const updated=await api<Detail>(path);
      setDetail(updated);onSaved();
    } catch(error) {setSaveError((error as Error).message)}
    finally {setSaving(false)}
  }
  return <Dialog title={detail?.record.finding_title||detail?.record.summary||row?.title||row?.summary||'问题记录'} close={onClose}>
    {!!row&&row.occurrences.length>1&&<label className="history-occurrence">发生记录（{row.occurrences.length} 次）
      <select value={id} onChange={event=>onChoose(event.target.value)}>{row.occurrences.map(item=><option value={item.id} key={item.id}>{timestamp(item.created_at)} · {item.run_id||item.id}{item.asset_id?' · '+item.asset_id:''}</option>)}</select>
    </label>}
    <HistoryError error={loadError}/>
    {!detail&&!loadError&&<div className="loading"><LoaderCircle className="spin" size={17}/>正在读取记录</div>}
    {detail&&<>
      <RecordSummary record={detail.record} evidence={detail.evidence_verification} runs={runs} onOpenRun={onOpenRun}/>
      {!!detail.revisions.length&&<section className="history-revisions">
        <div><h3>修订历史</h3><button className="button secondary small" onClick={()=>setAppend(!append)}>{append?'收起跟进':'追加跟进'}</button></div>
        <ol>{detail.revisions.map(item=><li key={item.revision}><strong>r{item.revision} · {item.revision_reason||'初始记录'}</strong><time>{timestamp(item.timestamp_utc)}</time>{item.revision_note&&<p>{item.revision_note}</p>}<RawRecord value={item}/></li>)}</ol>
      </section>}
      {!detail.revisions.length&&<RawRecord value={detail.record}/>}
      <HistoryError error={saveError}/>
      {append&&<form className="history-form" onSubmit={save}>
        <label>本次观察或处理结果<textarea required maxLength={4000} value={note} onChange={event=>setNote(event.target.value)}/></label>
        <label>给后续任务的检查项（可选）<input maxLength={1000} value={check} onChange={event=>setCheck(event.target.value)}/></label>
        <label>新增运行证据路径（可选，每行一个）<textarea value={evidence} onChange={event=>setEvidence(event.target.value)} placeholder="runs/…/report.json"/></label>
        <div className="modal-actions"><button type="submit" className="button primary" disabled={saving||!note.trim()}>保存新修订</button></div>
      </form>}
    </>}
  </Dialog>;
}

function NewObservation({runs, close, onSaved}: {runs: DemoRun[]; close: () => void; onSaved: (id: string) => void}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();setSaving(true);setError('');
    const fields = new FormData(event.currentTarget);
    const data = Object.fromEntries(fields);
    try {
      const result=await post('/incidents',{...data,evidence:references(String(data.evidence||''))});
      onSaved(result.id);
    } catch(error) {setError((error as Error).message)}
    finally {setSaving(false)}
  }
  return <Dialog title="记录问题" close={close}>
    <HistoryError error={error}/>
    <form className="history-form" onSubmit={save}>
      <label>问题标题<input name="summary" required maxLength={180} placeholder="例如：松手后接头脱开"/></label>
      <label>关联运行（可选）<select name="run_id"><option value="">独立观察</option>{runs.map(run=><option key={run.id} value={run.id}>{run.name} · {run.id}</option>)}</select></label>
      <div className="history-form-grid">
        <label>类别<select name="primary_category" defaultValue="workflow_integrity">{options(Object.fromEntries(Object.entries(categories).filter(([key])=>key!=='hardware_io')))}</select></label>
        <label>阶段<select name="stage" defaultValue="runtime">{options(Object.fromEntries(Object.entries(stages).filter(([key])=>key!=='hardware_test')))}</select></label>
        <label>来源<select name="mode" defaultValue="offline"><option value="offline">离线观察</option><option value="simulation">仿真观察</option></select></label>
      </div>
      <label>实际发生了什么<textarea name="observed_behavior" required maxLength={4000} rows={3}/></label>
      <label>预期结果（可选）<input name="expected_behavior" maxLength={4000}/></label>
      <label>触发条件（可选）<input name="trigger_conditions" maxLength={4000}/></label>
      <label>运行证据路径（可选，每行一个）<textarea name="evidence" rows={2} placeholder="runs/…/report.json"/></label>
      <div className="modal-actions"><button type="button" className="button secondary" onClick={close}>取消</button><button type="submit" className="button primary" disabled={saving}>保存观察</button></div>
    </form>
  </Dialog>;
}
