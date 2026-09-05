#!/usr/bin/env python3
"""Matched, cold-cache loopback requests; raw outputs and server counters retained."""
import argparse, hashlib, json, os, secrets, statistics, time, urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8011'
MODEL = 'deepseek-v4-flash-vision-exp'

def api(path, body=None, timeout=300):
    req = urllib.request.Request(BASE+path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def metrics():
    with urllib.request.urlopen(BASE+'/metrics', timeout=10) as r:
        raw = r.read().decode()
    out = {}
    for line in raw.splitlines():
        if line.startswith('vllm:'):
            key = line.split('{')[0].split()[0]
            out[key] = out.get(key, 0) + float(line.rsplit(' ',1)[1])
    return out

def idle(m):
    return m.get('vllm:num_requests_running',-1)==0 and m.get('vllm:num_requests_waiting',-1)==0

def frozen(root):
    path = root/'prompts.json'
    if path.exists(): return json.loads(path.read_text())
    bundled=Path(__file__).with_name('prompts.json')
    if bundled.exists():
        path.write_bytes(bundled.read_bytes())
        return json.loads(path.read_text())
    cells = [
        {'id':'decode-code','prompt':'Write a complete Python implementation of a TTL cache with a monotonic clock, LRU eviction, an optional maximum size, a decorator interface, type annotations, docstrings, and pytest tests for expiry and concurrent access. Return code only. Be thorough.', 'max_tokens':768},
        {'id':'decode-prose','prompt':'Explain how a database transaction travels from an application through validation, locks, write-ahead logging, commit, replication, and crash recovery. Give concrete examples and cover failure cases in detail. Use clear English prose.', 'max_tokens':768},
    ]
    for length in (8192,32768):
        header='Read the reference records below. Ignore irrelevant entries.\n'
        unit='Reference record: a cache stores key and value pairs with expiration times and uses a monotonic clock.\n'
        count=api('/tokenize',{'model':MODEL,'prompt':unit*64})['count']/64
        n=int(length/count)
        prompt=header+unit*n+'\nThe required answer is PREFILL_OK. Return only PREFILL_OK.'
        cells.append({'id':f'prefill-{length}','prompt':prompt,'max_tokens':16})
    cells += [
        {'id':'quality-math','prompt':'Compute 37 * 29 - 18. Return only the integer.', 'max_tokens':32,'expected':'1055'},
        {'id':'quality-json','prompt':'Return exactly this JSON object, with no markdown: {"name":"cedar","count":7,"enabled":false}', 'max_tokens':64,'expected_json':{'name':'cedar','count':7,'enabled':False}},
        {'id':'quality-tool','prompt':'Call lookup_record with record_id equal to order-728 and include_archived false. Do not reply with text.', 'max_tokens':128,'tools':[{'type':'function','function':{'name':'lookup_record','description':'Look up a record','parameters':{'type':'object','properties':{'record_id':{'type':'string'},'include_archived':{'type':'boolean'}},'required':['record_id','include_archived'],'additionalProperties':False}}}]},
    ]
    path.write_text(json.dumps(cells,indent=2)+'\n')
    return cells

def run(cell):
    before=metrics()
    if not idle(before): raise RuntimeError('Other requests active before measurement')
    body={'model':MODEL,'messages':[{'role':'user','content':cell['prompt']}], 'temperature':0,'seed':42,'max_tokens':cell['max_tokens'],'stream':True,'stream_options':{'include_usage':True},'chat_template_kwargs':{'thinking':False,'enable_thinking':False},'cache_salt':secrets.token_urlsafe(32)}
    if 'tools' in cell: body.update(tools=cell['tools'],tool_choice='auto')
    req=urllib.request.Request(BASE+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    start=time.perf_counter(); first=None; last=None; events=[]; content=''; reasoning=''; usage=None; finish=None; done=False; toolcalls={}
    with urllib.request.urlopen(req,timeout=300) as response:
        for raw in response:
            if not raw.startswith(b'data: '): continue
            if raw.strip()==b'data: [DONE]': done=True; continue
            event=json.loads(raw[6:]); now=time.perf_counter(); events.append({'t':now-start,'event':event})
            if event.get('usage'): usage=event['usage']
            for ch in event.get('choices',[]):
                d=ch.get('delta',{}); c=d.get('content') or ''; r=d.get('reasoning') or d.get('reasoning_content') or ''
                if c or r or d.get('tool_calls'):
                    first=first or now; last=now
                content+=c; reasoning+=r; finish=ch.get('finish_reason') or finish
                for tc in d.get('tool_calls',[]):
                    t=toolcalls.setdefault(tc['index'],{'name':'','arguments':''})
                    for k in ('name','arguments'): t[k]+=tc.get('function',{}).get(k) or ''
    end=time.perf_counter()
    # Give asynchronous metric recording a short bounded opportunity to settle.
    for _ in range(20):
        after=metrics()
        if after.get('vllm:request_success_total',0)>before.get('vllm:request_success_total',0) and idle(after): break
        time.sleep(.1)
    delta={k:after.get(k,0)-before.get(k,0) for k in after}
    if not done or not usage or first is None: raise RuntimeError('Incomplete stream or missing accounting')
    cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',usage.get('cached_tokens',0))
    valid=idle(after) and delta.get('vllm:request_success_total')==1 and delta.get('vllm:request_prefill_time_seconds_count')==1 and cached==0 and delta.get('vllm:prefix_cache_hits_total',0)==0 and not reasoning
    # Free-form throughput outputs have no correctness oracle here. Never label
    # them as passing quality merely because the stream completed.
    quality=None
    if 'expected' in cell: quality=content.strip()==cell['expected']
    if 'expected_json' in cell:
        try: quality=json.loads(content)==cell['expected_json']
        except ValueError: quality=False
    if cell['id'].startswith('prefill'): quality=content.strip()=='PREFILL_OK'
    if 'tools' in cell:
        try: quality=len(toolcalls)==1 and toolcalls[0]['name']=='lookup_record' and json.loads(toolcalls[0]['arguments'])=={'record_id':'order-728','include_archived':False} and finish=='tool_calls'
        except (ValueError,KeyError): quality=False
    n=usage['completion_tokens']; ttft=first-start
    return {'id':cell['id'],'valid':valid,'quality_pass':quality,'usage':usage,'finish_reason':finish,'ttft_s':ttft,'elapsed_s':end-start,'prefill_tok_s':usage['prompt_tokens']/ttft,'server_prefill_s':delta.get('vllm:request_prefill_time_seconds_sum'),'server_decode_s':delta.get('vllm:request_decode_time_seconds_sum'),'decode_tok_s':(n-1)/(last-first) if last>first else None,'content':content,'reasoning':reasoning,'tool_calls':toolcalls,'output_sha256':hashlib.sha256(content.encode()).hexdigest(),'metrics_before':before,'metrics_after':after,'metrics_delta':delta,'events':events}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--label',required=True); ap.add_argument('--only',default=''); ap.add_argument('--repeats',type=int,default=3); args=ap.parse_args()
    args.root.mkdir(parents=True,exist_ok=True); cells=frozen(args.root)
    if args.only: cells=[c for c in cells if c['id'] in args.only.split(',')]
    result={'label':args.label,'model':MODEL,'base':BASE,'started':time.time(),'status':'RUNNING','runs':[]}
    path=args.root/'results'/f'{args.label}.json'; path.parent.mkdir(exist_ok=True)
    try:
        for cell in cells:
            for i in range(-1,args.repeats):
                if (args.root/'SAFE_STOP').exists(): raise RuntimeError('SAFE_STOP requested')
                r=run(cell); r.update(repeat=i,warmup=i<0); result['runs'].append(r)
                path.write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps({k:r[k] for k in ('id','repeat','valid','quality_pass','ttft_s','prefill_tok_s','decode_tok_s','output_sha256')}),flush=True)
                if not r['valid']: raise RuntimeError('Invalid or contaminated measurement')
        result['status']='COMPLETE'
    except Exception as exc:
        result.update(status='FAILED',error=repr(exc)); raise
    finally:
        result['finished']=time.time(); path.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__': main()
