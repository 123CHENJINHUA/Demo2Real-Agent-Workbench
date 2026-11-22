"""OpenAI-compatible HTTP model adapter, backend-only credentials."""
import json, os, re, urllib.request, urllib.error
from pathlib import Path

class CompatibleProvider:
    def __init__(self,root):self.root=Path(root);self.path=self.root/'.runtime/secrets/provider.json'
    def load(self):
        try:return json.loads(self.path.read_text())
        except FileNotFoundError:return {'base_url':'','model':'','api_key':'','timeout':90}
    def public(self):
        data=self.load();return {k:v for k,v in data.items() if k!='api_key'}|{'has_api_key':bool(data.get('api_key')),'configured':bool(data.get('base_url') and data.get('model') and data.get('api_key'))}
    def save(self,data):
        current=self.load()
        for key in ('base_url','model','api_key','timeout'):
            if key in data:current[key]=data[key]
        url=current.get('base_url','')
        if url and not url.startswith(('http://','https://')):raise ValueError('模型服务地址必须是http(s) URL')
        timeout=float(current.get('timeout',90))
        if not 1<=timeout<=300:raise ValueError('timeout应在1至300秒内')
        current['timeout']=timeout
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as out:json.dump(current,out,ensure_ascii=False,indent=2)
        os.chmod(self.path,0o600)
        return self.public()
    def request(self,messages,**kwargs):
        data=self.load()
        if not data.get('base_url') or not data.get('model') or not data.get('api_key'):return {'status':'needs_configuration','reason':'尚未配置实际模型服务URL、model和API key；本地免鉴权服务可填入明确占位key。未执行语义推断。'}
        headers={'Content-Type':'application/json'}
        if data.get('api_key'):headers['Authorization']='Bearer '+data['api_key']
        body=json.dumps({'model':data['model'],'messages':messages,'temperature':0.1}).encode()
        request=urllib.request.Request(data['base_url'].rstrip('/')+'/chat/completions',body,headers,method='POST')
        try:
            with urllib.request.urlopen(request,timeout=float(data.get('timeout',90))) as response:result=json.load(response)
        except urllib.error.HTTPError as exc:
            return {'status':'failed','error':f'模型服务HTTP {exc.code}；请检查URL、模型和凭据。'}
        except (urllib.error.URLError,TimeoutError,OSError,ValueError):
            return {'status':'failed','error':'模型服务连接、超时或响应解析失败；请检查配置。'}
        try:content=result['choices'][0]['message']['content']
        except (KeyError,IndexError,TypeError):return {'status':'failed','error':'模型响应缺少choices[0].message.content'}
        return {'status':'succeeded','content':content,'model':data['model'],'usage':result.get('usage',{}),'evidence_scope':'model_inference_not_independent_validation'}


class Provider:
    """Provider facade; transport choice does not change task or HTTP contracts."""
    KINDS={'openai_compatible','codex_cli'}
    def __init__(self,root):
        self.root=Path(root).resolve();self.compatible=CompatibleProvider(self.root);self.path=self.compatible.path
    def load(self):
        return {'kind':'openai_compatible'}|self.compatible.load()
    def codex_status(self):
        from orchestration.providers.codex_cli import CodexCliProvider
        return CodexCliProvider(self.root,self.load()).status()
    def public(self):
        data=self.load();public={k:v for k,v in data.items() if k!='api_key'}
        public['has_api_key']=bool(data.get('api_key'))
        if data['kind']=='codex_cli':
            state=self.codex_status();public.update(configured=bool(state.get('configured')),codex=state,requires_api_key=False)
        else:public.update(configured=bool(data.get('base_url') and data.get('model') and data.get('api_key')),requires_api_key=True)
        return public
    def save(self,data):
        if not isinstance(data,dict):raise ValueError('Provider配置必须是对象')
        unknown=set(data)-{'kind','base_url','model','api_key','timeout','reasoning_effort'}
        if unknown:raise ValueError('未知Provider配置字段')
        current=self.load();kind=data.get('kind',current['kind'])
        if kind not in self.KINDS:raise ValueError('未注册的模型Provider')
        current.update(data);current['kind']=kind
        if 'reasoning_effort' in data:
            effort=data['reasoning_effort']
            if kind!='codex_cli':raise ValueError('推理强度设置用于本机 Codex')
            if not isinstance(effort,str) or effort and not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',effort):
                raise ValueError('推理强度须填写模型支持的值，例如 high，或留空')
        for field in ('base_url','model','api_key'):
            if not isinstance(current.get(field,''),str):raise ValueError(field+'必须是字符串')
        if current.get('base_url') and not current['base_url'].startswith(('http://','https://')):raise ValueError('模型服务地址必须是http(s) URL')
        current['timeout']=float(current.get('timeout',90))
        if not 1<=current['timeout']<=1800:raise ValueError('timeout应在1至1800秒内')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as out:json.dump(current,out,ensure_ascii=False,indent=2)
        os.chmod(self.path,0o600)
        return self.public()
    def request(self,messages,**kwargs):
        config=self.load()
        if config['kind']=='codex_cli':
            from orchestration.providers.codex_cli import CodexCliProvider
            return CodexCliProvider(self.root,config).request(messages,**kwargs)
        return self.compatible.request(messages,**kwargs)
