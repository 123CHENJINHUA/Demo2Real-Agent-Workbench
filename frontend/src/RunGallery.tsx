import {useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {ArrowRight, Box, ChevronRight, FolderOpen, Plus, Search, Terminal, Trash2} from 'lucide-react';

export interface RunSummary {
  id: string;
  name: string;
  status: string;
  created_at: number;
  input?: {source_path?: string; diagnostic_only?: boolean; source_run_id?: string; preview?: boolean; source_import_manifest_path?: string};
}

export interface RunPreview {
  artifact_id: string;
  kind: 'scene' | 'demonstration' | 'rgbd';
}

const previewLabels = {scene: 'Isaac 场景', demonstration: '示教画面', rgbd: '仿真 RGBD'};
const previewLabel = (run:RunSummary, preview:RunPreview) =>
  preview.kind==='scene' && run.input?.source_import_manifest_path ? '近似仿真场景' : previewLabels[preview.kind];
const runDate = new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: '2-digit', day: '2-digit'});
export const isActiveRun = (run: RunSummary) => ['running', 'queued', 'pending'].includes(run.status);

function isDemonstration(run: RunSummary) {
  return !!run.input?.source_path && !run.input.diagnostic_only && !run.input.source_run_id;
}

function PreviewImage({preview, hero = false}: {preview?: RunPreview; hero?: boolean}) {
  const [failed, setFailed] = useState(false);
  if (!preview || failed) {
    return <span className="preview-placeholder" role="img" aria-label="暂无预览"><Box size={hero ? 54 : 32} strokeWidth={1.25}/></span>;
  }
  return <img src={'/api/artifacts/' + preview.artifact_id} alt={previewLabels[preview.kind]}
    loading={hero ? 'eager' : 'lazy'} onError={() => setFailed(true)}/>;
}

function DeleteRunDialog({run,onClose,onDelete}: {
  run:RunSummary;
  onClose:()=>void;
  onDelete:(id:string)=>Promise<void>;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{if(!dialog.current?.open)dialog.current?.showModal()},[]);

  async function confirmDelete(){
    setBusy(true);
    setError('');
    try {
      await onDelete(run.id);
      dialog.current?.close();
    } catch(e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className="agent-conversation run-delete-dialog" aria-labelledby="delete-run-title"
    onClose={onClose} onCancel={event=>{if(busy)event.preventDefault()}}>
    <header><h2 id="delete-run-title">删除这次运行？</h2></header>
    <div className="run-delete-description">
      <strong>{run.name}</strong>
      <div><time dateTime={new Date(run.created_at*1000).toISOString()}>{new Date(run.created_at*1000).toLocaleString('zh-CN',{hour12:false})}</time><code>{run.id}</code></div>
      <p>这次运行将从列表移出，示教、产物和经验记录会继续保留。</p>
      {error&&<p className="error" role="alert">{error}</p>}
    </div>
    <div className="run-delete-actions">
      <button className="button secondary" type="button" autoFocus disabled={busy} onClick={()=>dialog.current?.close()}>取消</button>
      <button className="button run-delete-confirm" type="button" disabled={busy} onClick={confirmDelete}>
        <Trash2 size={15}/>{busy?'正在删除':'删除运行'}
      </button>
    </div>
  </dialog>;
}

export default function RunGallery({runs, previews, loading, onOpen, onImport, onDelete, renderStatus, diagnostics}: {
  runs: RunSummary[];
  previews: Record<string, RunPreview>;
  loading: boolean;
  onOpen: (id: string) => void;
  onImport: () => void;
  onDelete: (id: string) => Promise<void>;
  renderStatus: (status: string) => ReactNode;
  diagnostics: ReactNode;
}) {
  const [allRuns, setAllRuns] = useState(false);
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(9);
  const [deleteTarget,setDeleteTarget] = useState<RunSummary>();
  const completed = (run: RunSummary) => ['completed', 'succeeded'].includes(run.status);
  const runById = new Map(runs.map(run => [run.id, run]));
  const builtScene = (run: RunSummary) => completed(run) && run.input?.preview && previews[run.id]?.kind === 'scene';
  const featured = runs.find(run => builtScene(run) && previews[run.input?.source_run_id || '']?.kind === 'demonstration')
    || runs.find(builtScene)
    || runs.find(run => isDemonstration(run) && previews[run.id]?.kind === 'demonstration')
    || runs.find(run => completed(run) && previews[run.id]);
  const focusRuns = runs.filter(run => run.id === featured?.id || isActiveRun(run) || isDemonstration(run) || run.input?.preview)
    .sort((left,right)=>Number(isActiveRun(right))-Number(isActiveRun(left)));
  const displayName = (run: RunSummary) => {
    const source = runById.get(run.input?.source_run_id || '');
    return run.input?.preview && source && run.name.startsWith('场景预览 / ')
      ? source.name : run.name;
  };
  const query = search.trim().toLowerCase();
  const visible = (allRuns || query || !focusRuns.length ? runs : focusRuns)
    .filter(run => !query || run.name.toLowerCase().includes(query) || displayName(run).toLowerCase().includes(query) || run.id.includes(query));

  return <div className="workbench-home">
    <section className="workbench-hero" aria-labelledby="workbench-title">
      <div className="workbench-intro">
        <h1 id="workbench-title">示教工作台</h1>
        <p>导入机器人示教，查看场景与离线运行结果。</p>
        <button className="button primary workbench-import" onClick={onImport}><Plus size={19}/>导入示教</button>
      </div>
      {featured ? <button type="button" className="featured-preview" onClick={() => onOpen(featured.id)}>
        <PreviewImage key={previews[featured.id].artifact_id} preview={previews[featured.id]} hero/>
        <span className="featured-caption">
          <span><small>{previewLabel(featured,previews[featured.id])}</small><strong>{displayName(featured)}</strong></span>
          <ArrowRight size={20}/>
        </span>
      </button> : <div className="featured-preview featured-empty"><PreviewImage hero/></div>}
    </section>
    <section className="workbench-collection" aria-labelledby="gallery-title">
      <div className="collection-heading">
        <h2 id="gallery-title">示教与运行</h2>
        <label className="run-search"><Search size={17}/><input type="search" aria-label="搜索任务名称或运行 ID"
          placeholder="搜索名称或 ID" value={search} onChange={event => {setSearch(event.target.value); setLimit(9);}}/></label>
      </div>
      {runs.length > 0 && <div className="collection-filters" role="group" aria-label="运行范围">
        <button type="button" aria-pressed={!allRuns && !query} onClick={() => {setAllRuns(false); setSearch(''); setLimit(9);}}>示教与场景</button>
        <button type="button" aria-pressed={allRuns || !!query} onClick={() => {setAllRuns(true); setLimit(9);}}>全部运行</button>
      </div>}
      {visible.length ? <div className="run-gallery">
        {visible.slice(0, limit).map(run => {
          const preview = previews[run.id];
          const active = isActiveRun(run);
          return <div className="run-card-slot" key={run.id}>
           <button className="task-row run-card" type="button" data-run-id={run.id} onClick={() => onOpen(run.id)}>
            <span className="run-card-image">
              <PreviewImage key={preview?.artifact_id ?? 'empty'} preview={preview}/>
              {preview && <span className="run-card-kind">{previewLabel(run,preview)}</span>}
            </span>
            <span className="run-card-body">
              <strong title={run.name}>{displayName(run)}</strong>
              <span className="run-card-meta">
                <time dateTime={new Date(run.created_at * 1000).toISOString()}>{runDate.format(run.created_at * 1000)}</time>
                {renderStatus(run.status)}
                <ChevronRight size={15} aria-hidden="true"/>
              </span>
            </span>
           </button>
           <button className="run-card-delete icon-button" type="button" disabled={active}
             aria-label={'删除运行 '+displayName(run)+' '+run.id}
             title={active?'执行或排队中，请先取消或等待结束':'删除运行'}
             onClick={()=>setDeleteTarget(run)}><Trash2 size={16}/></button>
          </div>;
        })}
      </div> : <div className="gallery-empty"><FolderOpen size={25}/>{loading ? '正在读取运行记录' : query ? '没有匹配的运行' : '导入示教后，可在这里查看运行记录。'}</div>}
      {visible.length > limit && <button className="button secondary gallery-more" onClick={() => setLimit(limit + 9)}>显示更多 <span>{visible.length - limit}</span></button>}
    </section>
    <details className="workbench-tools"><summary><Terminal size={17}/>离线诊断<ChevronRight size={16}/></summary>{diagnostics}</details>
    {deleteTarget&&<DeleteRunDialog run={deleteTarget} onClose={()=>setDeleteTarget(undefined)} onDelete={onDelete}/>}
  </div>;
}
