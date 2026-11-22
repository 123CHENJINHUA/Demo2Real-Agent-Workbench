"""Submit real episode-split recovery learning to the workbench."""
from pathlib import Path
import argparse,json,time,urllib.request
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--batches',nargs='+',required=True);p.add_argument('--epochs',type=int,default=500);p.add_argument('--wait',action='store_true');a=p.parse_args();base='http://127.0.0.1:8787'
 def get(path):return json.load(urllib.request.urlopen(base+path,timeout=30))
 token=get('/api/session')['token'];request=urllib.request.Request(base+'/api/learning/recovery',data=json.dumps({'collection_batches':a.batches,'epochs':a.epochs}).encode(),headers={'Content-Type':'application/json','X-Session-Token':token},method='POST');run=json.load(urllib.request.urlopen(request,timeout=30));rid=run['id'];print(json.dumps({'run_id':rid,'batches':a.batches}),flush=True)
 if not a.wait:return
 while True:
  r=get('/api/runs/'+rid);print(json.dumps({'run_id':rid,'status':r['status'],'stages':{t['stage']:t['status'] for t in r['tasks']}},ensure_ascii=False),flush=True)
  if r['status'] not in ('queued','running'):
   output=ROOT/r.get('output_directory','runs/'+rid)/'learning_summary.json';output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(r,ensure_ascii=False,indent=2));return
  time.sleep(5)
if __name__=='__main__':main()
