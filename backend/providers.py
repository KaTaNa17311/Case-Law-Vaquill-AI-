import json, os, re, time
from urllib.parse import quote
import httpx

class ProviderError(RuntimeError):
    def __init__(self,message,status=None):
        super().__init__(message)
        self.status=status


def error_message(response,service):
    try:
        error=response.json().get('error',{})
        detail=error.get('message','') if isinstance(error,dict) else str(error)
    except (ValueError,AttributeError): detail=''
    # Provider messages are useful for quota diagnosis, but never expose configured secrets.
    for name in ('GEMINI_API_KEY','VOYAGE_API_KEY','QDRANT_API_KEY','BACKEND_API_TOKEN','HF_TOKEN'):
        secret=os.environ.get(name,'')
        if secret: detail=detail.replace(secret,'[redacted]')
    detail=re.sub(r'AIza[\w-]+','[redacted]',detail)[:900]
    if response.status_code==429:
        message=f'{service} rate/quota limit (HTTP 429). Check its current quota and Retry-After guidance; daily limits need a reset or quota change.'
    elif response.status_code==404:
        message=f'{service} resource not found (HTTP 404). '+('Create the vector collection with the embed command.' if service=='Qdrant' else 'Check the configured model ID and account access.')
    else: message=f'{service} returned HTTP {response.status_code}.'
    return message+(' '+detail if detail else '')


def request(method,url,**kwargs):
    budget=float(kwargs.pop('_budget',45)); deadline=time.monotonic()+budget
    service='Gemini' if 'generativelanguage.googleapis.com' in url else ('Voyage' if 'voyageai.com' in url else 'Qdrant')
    for attempt in range(3):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise ProviderError(service+' request time budget exceeded.')
        try:
            with httpx.Client(timeout=max(.1,min(remaining,30))) as c: r=c.request(method,url,**kwargs)
        except httpx.RequestError:
            if attempt==2: raise ProviderError(service+' connection failed or timed out.')
            delay=2**attempt
        else:
            if r.status_code<400:
                try: return r.json()
                except ValueError: raise ProviderError(service+' returned invalid JSON.')
            if r.status_code not in (429,500,502,503,504) or attempt==2:
                raise ProviderError(error_message(r,service),r.status_code)
            try: delay=max(2**attempt,float(r.headers.get('Retry-After',0)))
            except ValueError: delay=2**attempt
            if delay>=deadline-time.monotonic():
                raise ProviderError(error_message(r,service)+' Retry later; no successful saved embeddings are lost.',r.status_code)
        if delay>=deadline-time.monotonic(): raise ProviderError(service+' request time budget exceeded.')
        time.sleep(delay)

def gemini_json(system,payload,schema,budget=50):
    key=os.environ.get('GEMINI_API_KEY'); model=os.environ.get('GEMINI_MODEL')
    if not key or not model: raise ProviderError('Set GEMINI_API_KEY and GEMINI_MODEL on the backend')
    if not re.fullmatch(r'[A-Za-z0-9._-]+',model): raise ProviderError('Invalid GEMINI_MODEL')
    data=request('POST',f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',_budget=budget,headers={'x-goog-api-key':key},json={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':[{'text':json.dumps(payload,ensure_ascii=False)}]}],'generationConfig':{'temperature':0,'responseMimeType':'application/json','responseSchema':schema}})
    try:
        parts=data['candidates'][0]['content']['parts']; content=''.join(p.get('text','') for p in parts if not p.get('thought'))
        result=json.loads(content)
        if not isinstance(result,dict): raise ValueError('Expected a JSON object')
        return result
    except (KeyError,IndexError,ValueError): raise ProviderError('Gemini did not return a valid structured response')

def embeddings(texts,query=False):
    provider=os.environ.get('EMBEDDING_PROVIDER','gemini'); dim=int(os.environ.get('EMBEDDING_DIM','768'))
    if provider=='voyage':
        key=os.environ.get('VOYAGE_API_KEY')
        if not key: raise ProviderError('VOYAGE_API_KEY is required')
        data=request('POST','https://api.voyageai.com/v1/embeddings',headers={'Authorization':'Bearer '+key},json={'input':texts,'model':os.environ.get('VOYAGE_MODEL','voyage-4'),'input_type':'query' if query else 'document','output_dimension':dim})
        return [x['embedding'] for x in sorted(data['data'],key=lambda x:x['index'])]
    model=os.environ.get('GEMINI_EMBEDDING_MODEL','gemini-embedding-001')
    if not re.fullmatch(r'[A-Za-z0-9._-]+',model): raise ProviderError('Invalid embedding model')
    key=os.environ.get('GEMINI_API_KEY')
    if not key: raise ProviderError('GEMINI_API_KEY is required')
    # Embedding 2 aggregates multiple inputs: issue one request per document.
    out=[]
    for text in texts:
        body={'model':'models/'+model,'content':{'parts':[{'text':text}]},'outputDimensionality':dim}
        if model=='gemini-embedding-001': body['taskType']='RETRIEVAL_QUERY' if query else 'RETRIEVAL_DOCUMENT'
        else: body['content']['parts'][0]['text']=('task: search result | query: ' if query else 'title: none | text: ')+text
        data=request('POST',f'https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent',_budget=20 if query else 45,headers={'x-goog-api-key':key},json=body)
        v=data['embedding']['values']; length=sum(x*x for x in v)**0.5
        if not length: raise ProviderError('Embedding provider returned a zero-length vector.')
        out.append([x/length for x in v])
    return out

def qdrant(method,path,body=None):
    url=os.environ.get('QDRANT_URL','http://qdrant:6333').rstrip('/')
    headers={}
    if os.environ.get('QDRANT_API_KEY'): headers['api-key']=os.environ['QDRANT_API_KEY']
    return request(method,url+path,_budget=15,headers=headers,**({'json':body} if body is not None else {}))

def collection():
    name=os.environ.get('QDRANT_COLLECTION','advohq_gemini_768')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name): raise ProviderError('Invalid collection name')
    return name

def model_signature():
    return '|'.join([os.environ.get('EMBEDDING_PROVIDER','gemini'),os.environ.get('GEMINI_EMBEDDING_MODEL','gemini-embedding-001') if os.environ.get('EMBEDDING_PROVIDER','gemini')=='gemini' else os.environ.get('VOYAGE_MODEL','voyage-4'),os.environ.get('EMBEDDING_DIM','768')])

def ensure_collection():
    name=collection(); dim=int(os.environ.get('EMBEDDING_DIM','768'))
    result=qdrant('GET','/collections')['result']['collections']
    if not any(c['name']==name for c in result):
        qdrant('PUT','/collections/'+name,{'vectors':{'dense':{'size':dim,'distance':'Cosine','on_disk':True}}})
    config=qdrant('GET','/collections/'+name)['result']['config']['params']['vectors']
    if config.get('dense',{}).get('size')!=dim: raise ProviderError('Embedding dimension mismatch; create a separate collection')
    for field in ['court_key','document_id']:
        qdrant('PUT',f'/collections/{name}/index',{'field_name':field,'field_schema':'keyword'})
    # Signature prevents accidental reuse of a same-dimension but incompatible index.
    marker='00000000-0000-0000-0000-000000000000'
    found=qdrant('POST',f'/collections/{name}/points',{'ids':[marker],'with_payload':True})['result']
    if found and found[0]['payload'].get('model_signature')!=model_signature(): raise ProviderError('Embedding model mismatch; use a new collection')
    if not found:
        qdrant('PUT',f'/collections/{name}/points?wait=true',{'points':[{'id':marker,'vector':{'dense':[0.0]*dim},'payload':{'model_signature':model_signature(),'is_manifest':True}}]})
    return name
