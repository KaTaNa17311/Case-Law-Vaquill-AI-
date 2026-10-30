const allowed = /^\/api\/(status|chat|judgments\/[a-zA-Z0-9_-]+(?:\/download)?)$/;
export default {
 async fetch(request,env) {
  const url=new URL(request.url);
  const security={'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Cache-Control':'no-store'};
  if(url.pathname==='/api/health')return Response.json({service:'AdvoHQ gateway',status:'running'},{headers:security});
  if(url.pathname.startsWith('/api/')) {
   if(!allowed.test(url.pathname))return Response.json({detail:'Not found'},{status:404,headers:security});
   if(!env.BACKEND_URL||!env.BACKEND_API_TOKEN)return Response.json({connected:false,coverage:{documents:0,chunks:0,courts:[]},message:'Backend not connected. Launch the included FastAPI service, then configure BACKEND_URL and BACKEND_API_TOKEN on this Site, or use Connection & setup.'},{status:url.pathname==='/api/status'?200:503,headers:security});
   if(!['GET','POST'].includes(request.method))return new Response('Method not allowed',{status:405});
   if(Number(request.headers.get('content-length')||0)>65536)return new Response('Request too large',{status:413});
   try {
    const base=new URL(env.BACKEND_URL);if(base.protocol!=='https:')throw Error('Backend requires HTTPS');
    const headers={'Authorization':'Bearer '+env.BACKEND_API_TOKEN,'Content-Type':'application/json'};
    const body=request.method==='POST'?await request.text():undefined;
    if(body&&body.length>65536)return new Response('Request too large',{status:413});
    const upstream=await fetch(base.origin+base.pathname.replace(/\/$/,'')+url.pathname+url.search,{method:request.method,headers,body,redirect:'error',signal:AbortSignal.timeout(125000)});
    const outgoing=new Headers(security);for(const name of ['content-type','content-disposition']){const value=upstream.headers.get(name);if(value)outgoing.set(name,value)}
    return new Response(upstream.body,{status:upstream.status,headers:outgoing});
   }catch{return Response.json({detail:'Corpus backend is unreachable. Check the Site backend configuration.'},{status:502,headers:security})}
  }
  if(url.pathname!=='/')return new Response('Not found',{status:404});
  return new Response(page,{headers:{...security,'Content-Type':'text/html; charset=utf-8'}});
 }
};
