#!/usr/bin/env python3
"""Prepare a pinned MiaAI checkout. Does not start or stop services."""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess
from pathlib import Path

HERE=Path(__file__).resolve().parent

def run(*args,cwd):
    return subprocess.check_output(args,cwd=cwd,text=True).strip()

def render(text,values):
    for key,value in values.items():
        pattern=r'(?m)^'+re.escape(key)+r'=.*$'
        if re.search(r'(?m)^\s*export\s+'+re.escape(key)+r'=',text) or len(re.findall(pattern,text))>1:
            raise ValueError('Resolve duplicate or exported setting first: '+key)
        text=re.sub(pattern,lambda _:key+'='+value,text) if re.search(pattern,text) else text.rstrip()+'\n'+key+'='+value+'\n'
    return text

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('checkout',type=Path)
    ap.add_argument('--head-hcas',required=True,help='Two comma-separated local HCA names, from ibdev2netdev')
    ap.add_argument('--worker-hcas',required=True,help='Two comma-separated worker HCA names')
    ap.add_argument('--apply',action='store_true',help='Write files; default checks only')
    a=ap.parse_args(); target=a.checkout.resolve()
    for value in (a.head_hcas,a.worker_hcas):
        if not re.fullmatch(r'[A-Za-z0-9_]+,[A-Za-z0-9_]+',value):ap.error('Supply exactly two HCA names per node')
    lock=json.loads((HERE/'source-lock.json').read_text())
    if run('git','rev-parse','HEAD',cwd=target)!=lock['base_commit']:
        raise SystemExit('Wrong source version. Use the pinned base in source-lock.json.')
    env=target/'.env.dspark'
    if not env.is_file():raise SystemExit('First create and configure .env.dspark using the pinned MiaAI instructions.')
    values=json.loads((HERE/'profile.json').read_text())
    values.update(NCCL_IB_HCA=a.head_hcas,WORKER_NCCL_IB_HCA=a.worker_hcas)
    text=render(env.read_text(),values)
    patch=HERE/'patches/block-k.patch'
    dirty=run('git','status','--porcelain','--untracked-files=no',cwd=target)
    already=all((target/name).is_file() and hashlib.sha256((target/name).read_bytes()).hexdigest()==sha for name,sha in lock['patched_files_sha256'].items())
    if not already:
        if dirty:raise SystemExit('Tracked source has edits. Use a fresh pinned checkout.')
        subprocess.run(['git','apply','--check',str(patch)],cwd=target,check=True)
    else:
        changed=set(run('git','diff','--name-only','HEAD',cwd=target).splitlines())
        if not changed.issubset(lock['patched_files_sha256']):raise SystemExit('Unrelated tracked edits found. Use a fresh checkout.')
    if not a.apply:
        print('Checks passed. Add --apply to write the profile. No service action performed.');return
    # Backup remains on the user's machine, with private file permissions.
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=target/('.env.dspark.before-speed-'+stamp)
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(env.read_bytes())
    if not already:subprocess.run(['git','apply',str(patch)],cwd=target,check=True)
    for name,sha in lock['patched_files_sha256'].items():
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=sha:raise SystemExit('Source verification failed: '+name)
    tmp=env.with_name('.env.dspark.speed-next')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
    os.replace(tmp,env)
    print('Profile written; source hashes match the tested recipe. No service action performed.')
    print('Private env backup: '+str(backup))

if __name__=='__main__':main()
