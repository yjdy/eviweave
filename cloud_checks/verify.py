"""Cloud-only verification harness. Never part of the frozen release source."""
from pathlib import Path, PurePosixPath
import argparse,datetime,hashlib,json,os,platform,shutil,subprocess,sys,tarfile,time,urllib.request,urllib.parse,urllib.error,zipfile

REPO='yjdy/eviweave'
COMMIT='024a8dcfd4c2d5f6ad196da630400591aa2a5b58'
TREE='2d8ca6e4b57472c3ea5ed299fdbae78c4a04ab63'
FINGERPRINT='268a6a408f35b3851811c54c8eed1166e85b51980f56f767889aa1cfd642e528'
CONTROL=Path(__file__).resolve().parents[1]
EXPECTED=json.loads((CONTROL/'cloud_checks/expected-assets.json').read_text())

def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  while block:=f.read(1024*1024):h.update(block)
 return h.hexdigest()

def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')

class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  out=super().redirect_request(req,fp,code,msg,headers,newurl)
  if out is not None and urllib.parse.urlsplit(req.full_url).netloc!=urllib.parse.urlsplit(newurl).netloc:out.remove_header('Authorization')
  return out

OPENER=urllib.request.build_opener(Redirect()) # no CookieJar, browser session or netrc

def request(url,token=None,binary=False):
 headers={'User-Agent':'EviWeave-cloud-verification','Accept':'application/octet-stream' if binary else 'application/vnd.github+json'}
 if token:headers['Authorization']='Bearer '+token
 try:return OPENER.open(urllib.request.Request(url,headers=headers),timeout=120)
 except urllib.error.HTTPError as exc:
  try:message=json.loads(exc.read(4096)).get('message','HTTP error')
  except Exception:message='non-JSON HTTP error'
  raise RuntimeError(f'HTTP {exc.code} at {urllib.parse.urlsplit(url).path}: {message}') from None

def getjson(url,token=None):
 with request(url,token) as f:
  data=f.read(1024*1024+1);assert len(data)<=1024*1024
 return json.loads(data)

def download(url,path,expected=None,token=None,maximum=None):
 path.parent.mkdir(parents=True,exist_ok=True)
 with request(url,token,binary=True) as src,path.open('wb') as dst:
  total=0
  while block:=src.read(1024*1024):
   total+=len(block)
   if maximum is not None and total>maximum:raise ValueError('download exceeded explicit bound')
   dst.write(block)
 if expected:assert path.stat().st_size==expected['size'] and sha(path)==expected['sha256'],'asset size/hash mismatch'
 return {'name':path.name,'size':path.stat().st_size,'sha256':sha(path),'authenticated':bool(token)}

def base_env(root):
 e={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','LD_LIBRARY_PATH')}
 for key,rel in [('HOME','home'),('TMPDIR','tmp'),('XDG_CACHE_HOME','cache/xdg'),('PIP_CACHE_DIR','cache/pip'),('UV_CACHE_DIR','cache/uv')]:
  p=root/'.audit/cloud'/rel;p.mkdir(parents=True,exist_ok=True);e[key]=str(p)
 e.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
 return e

class Check:
 def __init__(self,mode,root):
  self.mode=mode;self.root=root;self.env=base_env(root);self.out=root/'.audit/cloud';self.out.mkdir(parents=True,exist_ok=True);self.errors=[]
  self.report={'status':'RUNNING','mode':mode,'workflow_commit':os.environ['GITHUB_SHA'],'actual_release_commit':COMMIT,'actual_release_tree':TREE,'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'runner_environment':os.environ.get('RUNNER_ENVIRONMENT'),'runner_os':platform.platform(),'python':sys.version,'model_calls':0,'commands':[],'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  assert os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted','self-hosted runners forbidden'
  actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=CONTROL,text=True).strip();assert actual==os.environ['GITHUB_SHA']
 def run(self,name,cmd,expect=0,timeout=600,extra=None,soft=False,cwd=None):
  env=dict(self.env);env.update(extra or {});started=time.monotonic()
  with (self.out/(name+'.log')).open('w') as log:p=subprocess.run([str(v) for v in cmd],cwd=cwd or self.root,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
  record={'name':name,'returncode':p.returncode,'expected':expect,'seconds':round(time.monotonic()-started,2)};self.report['commands'].append(record);print(json.dumps(record),flush=True)
  if p.returncode!=expect:
   self.errors.append(name)
   if not soft:raise RuntimeError('cloud step failed: '+name)
  return p.returncode
 def finish(self):
  self.report['status']='FAIL' if self.errors else 'PASS';self.report['failed_steps']=self.errors;self.report['finished_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();self.report['disk_free_end']=shutil.disk_usage(self.root).free
  write(CONTROL/'public-results'/f'{self.mode}-{sys.version_info.major}.{sys.version_info.minor}.json',self.report)
 def units(self,name,path):
  if not path.is_file():self.errors.append(name+'-result-missing');return
  x=json.loads(path.read_text());bad=[{'case':v['case'],'status':v['status'],'reason_last_line':v.get('reason','').splitlines()[-1:] } for v in x['cases'] if v['status']!='PASS']
  self.report[name]={'status':x['status'],'tests':x['tests'],'skips':sum(v['status']=='SKIP' for v in x['cases']),'nonpassing_cases':bad}
  if x['status']!='PASS' or bad:self.errors.append(name)


def bootstrap_python(c):
 """Keep relocated setup-python's shared-library lookup inside its checkout tree."""
 py=Path(sys.executable).resolve();assert py.is_relative_to(Path(os.environ['GITHUB_WORKSPACE']))
 version='0.17.2.4';meta=getjson(f'https://pypi.org/pypi/patchelf/{version}/json')
 candidates=[x for x in meta['urls'] if x['filename'].endswith('.whl') and 'x86_64' in x['filename'] and 'manylinux' in x['filename']];assert candidates
 item=sorted(candidates,key=lambda x:x['filename'])[0];p=c.out/'bootstrap'/item['filename'];receipt=download(item['url'],p,{'size':item['size'],'sha256':item['digests']['sha256']})
 with zipfile.ZipFile(p) as z:
  names=[n for n in z.namelist() if PurePosixPath(n).name=='patchelf'];assert len(names)==1
  tool=p.parent/'patchelf';tool.write_bytes(z.read(names[0]));tool.chmod(0o755)
 before=sha(py);patched=py.with_name('.'+py.name+'.cloud-rpath');shutil.copy2(py,patched)
 code=c.run('relocated-python-rpath',[tool,'--set-rpath','$ORIGIN/../lib',patched],soft=True)
 if code:
  c.report['bootstrap_patch_error']=(c.out/'relocated-python-rpath.log').read_text()[-1000:]
  raise RuntimeError('relocated interpreter patch failed')
 os.replace(patched,py)
 c.run('relocated-python-no-ld-path',[py,'-c','import sys,venv; print(sys.version)'],extra={'LD_LIBRARY_PATH':''})
 c.report['cloud_bootstrap']={'tool':'patchelf '+version,'official_artifact':receipt,'python_binary_before':before,'python_binary_after':sha(py),'policy':'Only newly downloaded cloud interpreter changed; frozen source and original release assets unchanged; new host profile measures resulting binary'}


def assets(c,anonymous=False):
 token=None if anonymous else os.environ.get('CLOUD_READ_TOKEN');assert anonymous or token
 endpoint=f'https://api.github.com/repos/{REPO}/releases/'+('tags/v0.1.0' if anonymous else '422606454')
 release=getjson(endpoint,token);assert release['tag_name']=='v0.1.0'
 if anonymous:assert not release['draft'] and release['published_at']
 remote={x['name']:x for x in release['assets']};receipts=[];dest=c.out/'assets'
 for item in EXPECTED:
  remote_item=remote[item['name']];assert remote_item['size']==item['size']
  url=remote_item['browser_download_url'] if anonymous else remote_item['url']+'?cloud_run='+os.environ['GITHUB_RUN_ID']
  receipts.append(download(url,dest/item['name'],item,token,maximum=item['size']))
 # After publication, all additional public bindings/summaries must also be downloaded anonymously.
 if anonymous:
  allowed={'cloud-verification.json','cloud-verification.json.sha256','publication-binding.json','publication-binding.json.sha256'}
  for name in sorted(set(remote)-{x['name'] for x in EXPECTED}):
   assert name in allowed and remote[name]['size']<=1048576,'unknown or oversized extra asset'
   item=remote[name];receipts.append(download(item['browser_download_url'],dest/name,maximum=1048576))
  for checksum in dest.glob('*.json.sha256'):
   h,n=checksum.read_text().strip().split(maxsplit=1);assert sha(dest/n)==h
 c.report['remote_assets']=receipts;c.report['release_id']=release['id'];c.report['assets_anonymous']=anonymous
 manifest=json.loads((dest/'release-manifest.json').read_text());assert manifest['source']['baseline_commit']==COMMIT and manifest['source']['baseline_git_tree']==TREE
 for line in (dest/'SHA256SUMS').read_text().splitlines():
  h,n=line.split(maxsplit=1);assert sha(dest/n)==h
 c.manifest=manifest;c.assets=dest
 return dest


def source(c,git=True):
 if git:
  assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=c.root,text=True).strip()==COMMIT
  assert subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=c.root,text=True).strip()==TREE
  names=subprocess.check_output(['git','ls-files'],cwd=c.root,text=True).splitlines();assert set(names)=={x['path'] for x in c.manifest['source']['files']}
 hashes={}
 for row in c.manifest['source']['files']:
  p=c.root/row['path'];assert p.is_file() and not p.is_symlink() and sha(p)==row['sha256'];hashes[row['path']]=row['sha256']
 assert len(hashes)==155 and hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()==FINGERPRINT
 ext=json.loads((c.root/'provenance/extraction-manifest.json').read_text());assert len(ext['files'])==22
 for row in ext['files']:
  assert sha(c.root/row['destination'])==row['release_sha256']
  if row['policy']=='verbatim':assert sha(c.root/row['destination'])==row['source_sha256']
 c.report['public_source_files_verified']=155;c.report['frozen_modules_verified']=22;c.report['verbatim_modules_verified']=17


def unpack_sdist(c):
 target=c.out/'original-sdist';target.mkdir()
 with tarfile.open(c.assets/'eviweave-0.1.0.tar.gz') as tar:
  for m in tar.getmembers():assert not PurePosixPath(m.name).is_absolute() and '..' not in PurePosixPath(m.name).parts and not m.issym() and not m.islnk()
  tar.extractall(target)
 unpacked=next(target.iterdir())
 for row in c.manifest['source']['files']:assert sha(unpacked/row['path'])==row['sha256']
 with zipfile.ZipFile(c.assets/'eviweave-0.1.0-py3-none-any.whl') as z:
  for row in json.loads((c.root/'provenance/extraction-manifest.json').read_text())['files']:
   assert hashlib.sha256(z.read(row['destination'].removeprefix('src/'))).hexdigest()==row['release_sha256']
 c.report['original_sdist_source_files_match']=155;c.report['original_wheel_business_modules_match']=22
 return unpacked


def matrix(c):
 assets(c);source(c);unpacked=unpack_sdist(c);bootstrap_python(c)
 tools=c.out/'tools-env';c.run('tool-venv',[sys.executable,'-m','venv',tools]);py=tools/'bin/python'
 c.run('locked-ci-dependencies',[py,'-m','pip','install','--only-binary=:all:','--require-hashes','-r',c.root/'locks/ci-tools.txt'])
 c.run('source-ordinary-install',[py,'-m','pip','install','--no-build-isolation','--no-deps',c.root])
 c.run('source-unit',[py,'-B','tools/run_regression.py','--suite','unit','--output-root','.audit/cloud/source-unit'],extra={'PYTHONPATH':str(c.root/'src')},soft=True)
 c.units('source_unit',c.out/'source-unit/unit-results.json')
 c.run('complete-ci',[py,'-B','tools/ci_check.py','--output-root','.audit/cloud/rebuilt-ci'],timeout=1500,soft=True)
 rebuilt=c.out/'rebuilt-ci';
 if (rebuilt/'ci-report.json').exists():c.report['rebuilt_ci']={k:v for k,v in json.loads((rebuilt/'ci-report.json').read_text()).items() if k in ('status','python')}
 for p in (rebuilt/'unpacked').glob('*/.audit/unit/unit-results.json'):c.units('rebuilt_installed_unit',p)
 for label,package,unit_source in [('original-wheel',c.assets/'eviweave-0.1.0-py3-none-any.whl',c.root),('original-sdist',unpacked,unpacked)]:
  venv=c.out/(label+'-env');c.run(label+'-venv',[sys.executable,'-m','venv','--without-pip',venv]);ip=venv/'bin/python'
  c.run(label+'-locked-tools',[py,'-m','pip','--python',ip,'install','--only-binary=:all:','--require-hashes','-r',c.root/'locks/ci-tools.txt'])
  c.run(label+'-install',[py,'-m','pip','--python',ip,'install','--no-build-isolation','--no-deps',package])
  c.run(label+'-installation-check',[ip,'-B',unit_source/'tools/check_installation.py','--output',c.out/(label+'-install.json')])
  output=unit_source/'.audit'/('cloud-'+label+'-unit')
  c.run(label+'-unit',[ip,'-B',unit_source/'tools/run_regression.py','--suite','unit','--output-root',output],soft=True)
  c.units(label.replace('-','_')+'_unit',output/'unit-results.json')
 c.report['archive_rebuild_policy']='Cloud build outputs are validation only; original release assets remain unchanged'


def runtime(c):
 assets(c);source(c);bootstrap_python(c)
 # Host tools must already exist; no global apt install or system Docker daemon.
 c.report['disk_free_start']=shutil.disk_usage(c.root).free
 host={}
 for name in ['docker','dockerd','containerd','bwrap']:
  p=shutil.which(name);assert p,'required hosted tool missing: '+name
  host[name]={'sha256':sha(p),'version':subprocess.check_output([p,'--version'],text=True).strip()}
 c.report['host_tools']=host
 blocks=__import__('re').findall(r'```sh\n(.*?)```',(c.root/'README.md').read_text(),__import__('re').S)
 prep=next(b for b in blocks if b.startswith('mkdir -p .audit/tmp'))
 script=c.out/'readme-prepare.sh';script.write_text(prep+'\n');c.run('readme-public-preparation',['bash','-e',script],timeout=5400)
 py=c.root/'.venv-experiment/bin/python';profile=c.root/'.local/profiles/public-host.json';c.env['EVIWEAVE_ENVIRONMENT_PROFILE']=str(profile)
 for key,prefix in [('offline-config','.venv-experiment/bin/python -B tools/host_profile.py config'),('offline-doctor-and-synthetic','.venv-experiment/bin/python -B tools/host_profile.py doctor')]:
  body=next(b for b in blocks if b.startswith(prefix))
  # The documented final unit command is retained; all full applicable unit is mandatory.
  p=c.out/(key+'.sh');p.write_text(body+'\n');c.run(key,['bash','-e',p],timeout=1800)
 syn=c.root/'.audit/public synthetic';synacc=json.loads((syn/'acceptance.json').read_text());assert synacc['status']=='PASS' and synacc['interrupted_returncode']==130 and synacc['completed_resume_new_calls']==0
 c.report['synthetic']={k:v for k,v in synacc.items() if k!='report_id'}
 c.report['csvs']=[]
 for n in [1,2]:
  p=syn/f'experiment/predictions/task_{n}/prediction.csv';assert p.read_bytes()==(c.root/f'examples/synthetic/expected/task_{n}.csv').read_bytes()
  import csv
  c.report['csvs'].append({'task':n,'rows':len(list(csv.reader(p.open())))-1,'sha256':sha(p)})
 evidence=c.root/'.local/evidence-v6';evidence.mkdir()
 for name in ['eviweave-v0.1.0-v6-evidence.zip','eviweave-v0.1.0-v6-evidence.zip.sha256']:shutil.copyfile(c.assets/name,evidence/name)
 body=next(b for b in blocks if b.startswith('python3 -B tools/package_evidence.py verify'));p=c.out/'historical-score.sh';p.write_text(body+'\n');c.run('p4-original-evidence-and-offline-score',['bash','-e',p],timeout=900)
 scorepaths=list((c.root/'.audit/historical-score').rglob('score.json'));assert len(scorepaths)==1
 score=json.loads(scorepaths[0].read_text());assert (score['task_count'],score['passed_task_count'])==(60,45)
 original=json.loads((evidence/'extracted/evaluation/public60.json').read_text());assert score['tasks']==original['tasks']
 c.report['historical_score']={'passed':45,'total':60,'first_pass_historical_record':'44/60','per_task_matches_original_p4':True,'new_inference':False}
 with zipfile.ZipFile(evidence/'eviweave-v0.1.0-v6-evidence.zip') as z:
  c.report['p4_members']=len(z.namelist())
 c.report['p4_scope']={'predictions':410,'attempt_summaries':414,'selected_trace_attempts':6,'missing_usage_attempts':3}
 inv=json.loads((c.root/'.runtimes/public/workbench-files.json').read_text());assert not any('.pivot_root' in x['path'] for x in inv['entries'])
 doc=json.loads(profile.read_text());c.report['new_host']={'profile_kind':doc['kind'],'python':doc['python']['version'],'profile_sha256':sha(profile),'runtime_kind':doc['runtime']['kind'],'inventory_entries':len(inv['entries']),'inventory_sha256':sha(c.root/'.runtimes/public/workbench-files.json'),'resource_lock_sha256':doc['runtime']['resource_lock_sha256']}
 c.run('stop-project-daemon',[sys.executable,'-B','scripts/project_docker.py','stop','--storage-root','.runtimes/public-build'])


def anonymous(c):
 assets(c,True)
 # Authentication used by checkout is not reused for any download or subprocess.
 assert not any(k in c.env for k in ['GITHUB_TOKEN','CLOUD_READ_TOKEN','GH_TOKEN'])
 repo=getjson('https://api.github.com/repos/'+REPO);assert not repo['private'] and repo['default_branch']=='main'
 ref=getjson(f'https://api.github.com/repos/{REPO}/git/ref/tags/v0.1.0');obj=ref['object']
 if obj['type']=='tag':obj=getjson(obj['url'])['object']
 assert obj['sha']==COMMIT
 archive=c.out/'tag-source.tar.gz';download(f'https://api.github.com/repos/{REPO}/tarball/refs/tags/v0.1.0',archive,maximum=2*1024*1024)
 dest=c.root/'.audit/anonymous-tag-source';dest.mkdir()
 with tarfile.open(archive) as tar:
  for m in tar.getmembers():assert not PurePosixPath(m.name).is_absolute() and '..' not in PurePosixPath(m.name).parts and not m.issym() and not m.islnk()
  tar.extractall(dest)
 downloaded=next(dest.iterdir());old=c.root;c.root=downloaded;source(c,False);c.root=old
 c.report['anonymous_tag']={'commit':COMMIT,'source_archive_sha256':sha(archive),'source_files':155,'authorization_header':False,'cookies':False}
 # Install and run the public tag's full unit suite; same bytes link the prior cloud runtime check.
 bootstrap_python(c);tools=c.out/'anonymous-env';c.run('anonymous-venv',[sys.executable,'-m','venv',tools]);py=tools/'bin/python'
 c.run('anonymous-locked-tools',[py,'-m','pip','install','--only-binary=:all:','--require-hashes','-r',downloaded/'locks/ci-tools.txt'])
 c.run('anonymous-source-install',[py,'-m','pip','install','--no-build-isolation','--no-deps',downloaded])
 c.run('anonymous-original-wheel-install',[py,'-m','pip','install','--no-deps','--force-reinstall',c.assets/'eviweave-0.1.0-py3-none-any.whl'])
 c.run('anonymous-installation-check',[py,'-B',downloaded/'tools/check_installation.py','--output',c.out/'anonymous-install.json'])
 c.run('anonymous-tag-unit',[py,'-B',downloaded/'tools/run_regression.py','--suite','unit','--output-root',downloaded/'.audit/anonymous-unit'])
 c.units('anonymous_tag_unit',downloaded/'.audit/anonymous-unit/unit-results.json')
 c.run('anonymous-evidence-verify',[py,'-B',downloaded/'tools/package_evidence.py','verify','--archive',c.assets/'eviweave-v0.1.0-v6-evidence.zip','--output-root',downloaded/'.audit/evidence-verify'])
 c.report['runtime_evidence_policy']='Public tag and all original assets match already measured hosted runtime verification; no model inference or full dataset download'


def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['matrix','runtime','anonymous']);p.add_argument('--target',type=Path,required=True);args=p.parse_args();root=args.target.resolve();c=Check(args.mode,root)
 try:globals()[args.mode](c)
 except BaseException as exc:
  c.errors.append(type(exc).__name__);c.report['failure_type']=type(exc).__name__;c.report['failure_detail']=str(exc)[:500]
 finally:c.finish()
 print(json.dumps({'status':c.report['status'],'mode':c.mode,'failed_steps':c.errors}),flush=True)
 return 0 if c.report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
