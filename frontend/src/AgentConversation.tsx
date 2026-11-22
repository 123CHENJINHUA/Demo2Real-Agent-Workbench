import {useEffect, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import {ArrowUpRight, Send, X} from 'lucide-react';
import {post} from './api';

export interface AgentQuestion {
 id:string;
 agent:string;
 prompt:string;
 context?:string;
 report_path?:string;
 answer?:string;
 pending:0|1;
}

export default function AgentConversation({runId,questions,artifacts,onClose,onReply}:{
 runId:string;
 questions:AgentQuestion[];
 artifacts:{id:string;path:string}[];
 onClose:()=>void;
 onReply:()=>Promise<void>;
}){
 const dialog=useRef<HTMLDialogElement>(null);
 const lastTurn=useRef<HTMLDivElement>(null);
 const [selected,setSelected]=useState<string>();
 const pending=questions.filter(item=>item.pending);
 const history=questions.filter(item=>!item.pending);
 const current=pending.find(item=>item.id===selected)??pending[0];
 useEffect(()=>{dialog.current?.showModal()},[]);
 useEffect(()=>{lastTurn.current?.scrollIntoView({block:'nearest'})},[pending.length]);

 function turn(question:AgentQuestion){
  const evidence=artifacts.find(item=>item.path===question.report_path);
  return <div className="agent-conversation-turn" key={question.id}>
   <article aria-current={question.id===current?.id?true:undefined}>
    <strong>{question.agent}</strong>
    <p>{question.prompt}</p>
    {question.context&&<small>{question.context}</small>}
    {evidence&&<a href={'/api/artifacts/'+evidence.id} target="_blank" rel="noreferrer">查看证据<ArrowUpRight size={12}/></a>}
    {!!question.pending&&question.id!==current?.id&&<button type="button" className="text-button" onClick={()=>setSelected(question.id)}>回复此问题</button>}
   </article>
   {question.answer&&<article className="agent-conversation-user"><strong>你</strong><p>{question.answer}</p></article>}
  </div>;
 }

 return <dialog className="agent-conversation" ref={dialog} onClose={onClose} aria-labelledby="agent-conversation-title">
  <header>
   <h2 id="agent-conversation-title">Agent 对话</h2>
   <button type="button" className="icon-button" title="关闭对话" aria-label="关闭对话" onClick={()=>dialog.current?.close()}><X size={18}/></button>
  </header>
  <div className="agent-conversation-thread" aria-live="polite">
   {!pending.length&&<p>当前没有需要回复的问题。</p>}
   {pending.map(turn)}
   <div ref={lastTurn}/>
   {!!history.length&&<details className="agent-conversation-history">
    <summary>历史对话（{history.length}）</summary>
    {history.map(turn)}
   </details>}
  </div>
  {current&&<ReplyForm key={current.id} runId={runId} question={current} onReply={onReply}/>}
 </dialog>;
}

function ReplyForm({runId,question,onReply}:{runId:string;question:AgentQuestion;onReply:()=>Promise<void>}){
 const [answer,setAnswer]=useState('');
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 async function submit(event:FormEvent){
  event.preventDefault();
  if(busy||!answer.trim())return;
  setBusy(true);
  setError('');
  try{
   await post(`/runs/${runId}/questions/${question.id}/reply`,{answer});
   await onReply();
  }catch(e){
   setError((e as Error).message);
  }finally{
   setBusy(false);
  }
 }
 return <form onSubmit={submit} className="agent-conversation-form">
  <label htmlFor="agent-reply">回复 {question.agent}</label>
  <textarea id="agent-reply" rows={3} maxLength={3000} value={answer} onChange={event=>setAnswer(event.target.value)}
   placeholder="写下你的观察，或指出相关示教画面" required disabled={busy}/>
  {error&&<p className="error" role="alert">{error}</p>}
  <button className="button primary small" type="submit" disabled={busy||!answer.trim()}><Send size={14}/>{busy?'正在发送':'回复并继续'}</button>
 </form>;
}
