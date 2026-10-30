// Local adapter for the same Worker module used in production.
import http from 'node:http';
import worker from '../worker/index.js';
const server=http.createServer(async(req,res)=>{
 try{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=Buffer.concat(chunks);
  const request=new Request('http://localhost'+req.url,{method:req.method,headers:req.headers,...(body.length?{body,duplex:'half'}:{})});
  const response=await worker.fetch(request,process.env,{});
  res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(500);res.end('Preview error')}
});server.listen(Number(process.env.PORT||(process.argv.includes('--port')?process.argv[process.argv.indexOf('--port')+1]:3000)),'0.0.0.0');
