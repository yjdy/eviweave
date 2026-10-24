"""Cloud-only host prerequisite: private /usr/bin overlay, all payloads in checkout.

No root-directory bind, global package installation, daemon fallback or sandbox
relaxation. Tests see a real default-path Bubblewrap, even after clearing PATH.
"""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,urllib.request
workspace=Path(os.environ['GITHUB_WORKSPACE']).resolve();store=workspace/'.audit/namespace-host';store.mkdir(parents=True,exist_ok=True)
uid,gid=int(sys.argv[1]),int(sys.argv[2]);assert os.getuid()==0 and uid!=0
subprocess.run(['mount','--make-rprivate','/'],check=True)
metadata=subprocess.check_output(['apt-cache','show','bubblewrap'],text=True)
entries=[]
for block in metadata.split('\n\n'):
 d=dict(line.split(': ',1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
 if d.get('SHA256')=='706763714e5d19fc7ab2572a6418506e3a4a5d5960e99309707b58117e6b40cb' and d.get('Architecture')=='amd64':entries.append(d)
assert entries,'Previously verified Ubuntu Bubblewrap prerequisite changed or unavailable'
d=entries[0];archive=store/'bubblewrap.deb'
with urllib.request.urlopen('https://archive.ubuntu.com/ubuntu/'+d['Filename'],timeout=120) as f:body=f.read(int(d['Size'])+1)
assert len(body)==int(d['Size']) and hashlib.sha256(body).hexdigest()==d['SHA256'];archive.write_bytes(body)
subprocess.run(['dpkg-deb','-x',str(archive),str(store/'extracted')],check=True)
upper=store/'usr-bin-upper';work=store/'usr-bin-work';upper.mkdir();work.mkdir();shutil.copyfile(store/'extracted/usr/bin/bwrap',upper/'bwrap');(upper/'bwrap').chmod(0o755)
subprocess.run(['mount','-t','overlay','overlay','-o',f'lowerdir=/usr/bin,upperdir={upper},workdir={work}','/usr/bin'],check=True)
receipt={'kind':'isolated-cloud-host-tool-overlay','package_version':d['Version'],'package_sha256':d['SHA256'],'binary_sha256':hashlib.sha256((upper/'bwrap').read_bytes()).hexdigest(),'all_new_payloads_inside_checkout':True,'system_package_install':False,'root_directory_bind':False,'ordinary_uid':uid,'mount_namespace':os.readlink('/proc/self/ns/mnt')}
(store/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
os.execvp('setpriv',['setpriv','--reuid',str(uid),'--regid',str(gid),'--init-groups',*sys.argv[3:]])
