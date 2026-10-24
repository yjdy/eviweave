"""Cloud-only gate aggregation: verify actual source/asset identities, not green UI."""
from pathlib import Path
import datetime,hashlib,io,json,os,sys,zipfile
import verify as v
P='https://api.github.com/repos/yjdy/eviweave/'
CONTROL=Path(__file__).resolve().parents[1]
SPEC=json.loads((CONTROL/'cloud_checks/gates-input.json').read_text())
TOKEN=os.environ['CLOUD_READ_TOKEN']
assert os.environ['RUNNER_ENVIRONMENT']=='github-hosted'
root=CONTROL/'.audit/gates';root.mkdir(parents=True,exist_ok=True)
checks=[];reports={}
def fetch_report(runid,artifact_name):
 run=v.getjson(P+f'actions/runs/{runid}',TOKEN)
 artifacts=v.getjson(P+f'actions/runs/{runid}/artifacts',TOKEN)['artifacts'];item=next(x for x in artifacts if x['name']==artifact_name);assert item['size_in_bytes']<1048576
 path=root/(str(item['id'])+'.zip');v.download(P+f"actions/artifacts/{item['id']}/zip",path,token=TOKEN,maximum=1048576)
 with zipfile.ZipFile(path) as z:
  names=z.namelist();assert len(names)==1 and names[0].endswith('.json');report=json.loads(z.read(names[0]))
 assert report['status']=='PASS' and report['workflow_commit']==run['head_sha'] and report['actual_release_commit']==v.COMMIT and report['actual_release_tree']==v.TREE
 assert report['runner_environment']=='github-hosted' and report['public_source_files_verified']==155 and report['frozen_modules_verified']==22 and report['verbatim_modules_verified']==17
 expected={x['name']:x for x in v.EXPECTED};actual={x['name']:x for x in report['remote_assets']}
 for name,item in expected.items():assert actual[name]['sha256']==item['sha256'] and actual[name]['size']==item['size']
 jobs=v.getjson(P+f'actions/runs/{runid}/jobs',TOKEN)['jobs'];matching=[j for j in jobs if j['name']==SPEC['jobs'][artifact_name]];assert len(matching)==1 and matching[0]['conclusion']=='success' and 'self-hosted' not in matching[0]['labels']
 checks.append({'artifact':artifact_name,'run_id':runid,'run_url':run['html_url'],'job_id':matching[0]['id'],'job_name':matching[0]['name'],'job_url':matching[0]['html_url'],'job_conclusion':'success','workflow_commit':run['head_sha'],'tested_release_commit':v.COMMIT,'curated_result_sha256':hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'parent_workflow_conclusion':run['conclusion']})
 return report
for spec in SPEC['packages']:
 d=fetch_report(spec['run_id'],spec['artifact']);minor=spec['minor'];count=225 if minor=='3.10' else 232
 for name in ['source_unit','rebuilt_installed_unit','original_wheel_unit','original_sdist_unit']:
  unit=d[name];assert unit['status']=='PASS' and unit['tests']==count and unit['skips']==0 and not unit['nonpassing_cases']
  assert len(unit['not_applicable'])==(7 if minor=='3.10' else 0)
  if minor=='3.10':assert unit['unsupported_profile_rejection_checks']==2
 reports[minor]={'actual_python':d['python'],'tests_each':count,'stages':['source','rebuilt-sdist-wheel-installed','original-wheel-installed','original-sdist-installed'],'unit_skips':0,'not_applicable':d['source_unit']['not_applicable'],'unsupported_profile_rejection_checks':2 if minor=='3.10' else 0,'ordinary_install':'PASS','sdist_to_wheel':'PASS','original_asset_installation':'PASS','source_and_original_sdist_files':155,'original_wheel_modules':22}
r=fetch_report(SPEC['runtime']['run_id'],'runtime-result');assert r['synthetic']['interrupted_returncode']==130 and r['synthetic']['completed_resume_new_calls']==0 and r['synthetic']['model_calls']==0
assert r['historical_score']['passed']==45 and r['historical_score']['total']==60 and r['historical_score']['per_task_matches_original_p4']
assert [x['rows'] for x in r['csvs']]==[2,0]
a=fetch_report(SPEC['assets']['run_id'],'original-assets-result');assert a['original_assets_provenance']['status']=='PASS' and a['original_assets_provenance']['checks']==2723
summary={'schema_version':1,'status':'PASS','scope':'pre-publication mandatory hosted gates; anonymous post-publication verification remains pending','version':'0.1.0','repository':'https://github.com/yjdy/eviweave','release_commit':v.COMMIT,'git_tree':v.TREE,'candidate_content_fingerprint':v.FINGERPRINT,'original_assets':[x for x in v.EXPECTED if x['name'] not in ['release-binding.json','release-binding.json.sha256']],'gate_workflow_commit':os.environ['GITHUB_SHA'],'gate_run_id':os.environ['GITHUB_RUN_ID'],'gate_run_url':f"https://github.com/yjdy/eviweave/actions/runs/{os.environ['GITHUB_RUN_ID']}",'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':reports,'required_job_evidence':checks,'runtime':{'host':r['runner_os'],'new_host':r['new_host'],'host_tools':r['host_tools'],'synthetic':r['synthetic'],'csvs':r['csvs'],'historical_score':r['historical_score'],'p4_scope':r['p4_scope'],'p4_members':r['p4_members'],'commands':r['commands'],'disk_free_start':r['disk_free_start'],'disk_free_end':r['disk_free_end']},'original_asset_provenance':a['original_assets_provenance'],'failed_attempts_retained':SPEC['failed_attempts_retained'],'default_workflow_note':'Frozen-source default workflow used rolling patch versions and undeclared host assumptions and failed. Required scopes are verified by the independent hosted jobs listed here against the exact frozen commit and original assets. No default workflow failure has been erased or relabelled as success.','model_calls':0,'new_benchmark_inference':False,'online_collection':'UNSUPPORTED_G1','local_install_build_or_runtime_checks':False,'anonymous_verification':'PENDING until formal publication'}
v.write(CONTROL/'public-results/gates.json',summary)
print(json.dumps({'status':'PASS','required_jobs_verified':len(checks),'actual_release_commit':v.COMMIT}))
