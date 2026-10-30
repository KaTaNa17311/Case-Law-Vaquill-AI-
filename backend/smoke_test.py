"""Run inside the API container. Default test makes no external model calls."""
import argparse
import os
import sys
import httpx


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--question',help='Optional actual research question; uses configured generation API.')
    p.add_argument('--mode',choices=['keyword','meaning','phrase'],default='keyword')
    a=p.parse_args()
    headers={'Authorization':'Bearer '+os.environ.get('BACKEND_API_TOKEN','')}
    try:
        with httpx.Client(base_url='http://127.0.0.1:8000',headers=headers,timeout=125) as c:
            health=c.get('/api/health');health.raise_for_status();print('BACKEND: running',flush=True)
            status=c.get('/api/status');status.raise_for_status();status=status.json()
            print('DOCUMENTS:',status['coverage']['documents'])
            print('CHUNKS:',status['coverage']['chunks'])
            print('EMBEDDED:',status.get('embedded_chunks',0),flush=True)
            if not status['coverage']['documents']:
                print('No imported records yet. Import a selected source file first.');return
            response=c.post('/api/chat',json={'message':'Show a random full judgment','jurisdiction':'all','include_supreme':False});response.raise_for_status();result=response.json()
            if result['status']!='document':
                print('FULL DOCUMENT:',result.get('message'));return
            document=result['judgment']
            print('RANDOM DOCUMENT:',document['title'])
            print('RAW LENGTH:',len(document['text']))
            print('READABLE LENGTH:',len(document['formatted_text']))
            print('NON-WHITESPACE UNCHANGED:',document['formatting']['non_whitespace_unchanged'])
            print('SPELLING CORRECTION:',document['formatting']['spelling_correction'])
            print('FULL DOCUMENT TEST: passed (no Gemini call)',flush=True)
            if a.question:
                print('RESEARCHING:',a.question,flush=True)
                response=c.post('/api/chat',json={'message':a.question,'jurisdiction':'all','include_supreme':False,'mode':a.mode});response.raise_for_status();result=response.json()
                print('STATUS:',result['status']);print('MESSAGE:',result.get('message',''))
                for claim in result.get('claims',[]):print('ANSWER:',claim['statement'])
                for warning in result.get('warnings',[]):print('WARNING:',warning)
                print('EVIDENCE PASSAGES:',len(result.get('evidence',[])))
    except httpx.HTTPStatusError as e:
        print('HTTP ERROR:',e.response.status_code,e.response.text[:2000]);sys.exit(1)
    except httpx.RequestError:
        print('Backend unavailable or timed out. Run docker compose logs --tail=30 api.');sys.exit(1)
if __name__=='__main__':main()
