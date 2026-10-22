#!/usr/bin/env python3
"""Start/stop an independent Docker daemon; every writable path is in project."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from runtime_support import PROJECT, local_path, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'info'])
    parser.add_argument('--storage-root', type=Path, default=PROJECT/'.runtimes/build')
    args = parser.parse_args()
    root = local_path(args.storage_root); root.mkdir(parents=True, exist_ok=True)
    for name in ('data','exec','tmp','home','client','containerd-data','containerd-state','opt','verifiers',
                 'nri.sock','nri-plugins','nri-config','daemon.json','containerd.toml','docker.sock',
                 'docker.pid','containerd.pid','containerd.sock','daemon.log','containerd.log'):
        local_path(root/name)
    pidfile = root/'docker.pid'; socket = root/'docker.sock'
    containerd_pid = root/'containerd.pid'
    command = ['docker', '--config', str(root/'client'), '--host', 'unix://'+str(socket)]
    if args.action == 'stop':
        if pidfile.exists():
            # Do not signal a reused PID belonging to an unrelated daemon.
            pid = int(pidfile.read_text())
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
            if str(root/'daemon.json') not in cmdline: raise ValueError('PID is not this project daemon')
            subprocess.run(['sudo', '-n', 'kill', '-TERM', str(pid)], check=True)
            for _ in range(100):
                if not pidfile.exists(): break
                time.sleep(0.1)
            else: raise RuntimeError('project Docker did not stop in time')
        if containerd_pid.exists():
            pid = int(containerd_pid.read_text())
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
            if str(root/'containerd.toml') not in cmdline: raise ValueError('unexpected containerd PID')
            subprocess.run(['sudo', '-n', 'kill', '-TERM', str(pid)], check=True)
            containerd_pid.unlink()
            for _ in range(100):
                status = Path(f'/proc/{pid}/status')
                if not status.exists() or 'State:\tZ' in status.read_text(): break
                time.sleep(0.1)
            else: raise RuntimeError('project containerd did not stop in time')
            (root/'containerd.sock').unlink(missing_ok=True)
        return
    if args.action == 'start' and not pidfile.exists():
        for name in ('data', 'exec', 'tmp', 'home', 'client'):
            (root/name).mkdir(exist_ok=True)
        config = {'data-root': str(root/'data'), 'exec-root': str(root/'exec'), 'pidfile': str(pidfile),
                  'hosts': ['unix://'+str(socket)], 'storage-driver': 'vfs', 'iptables': False,
                  'ip6tables': False, 'bridge': 'none', 'ip-forward': False, 'ip-masq': False,
                  'features': {'containerd-snapshotter': False}, 'containerd': str(root/'containerd.sock'),
                  'containerd-namespace': 'eviweave', 'containerd-plugins-namespace': 'eviweave-plugins'}
        write_json(root/'daemon.json', config)
        environment = dict(os.environ, DOCKER_TMPDIR=str(root/'tmp'), TMPDIR=str(root/'tmp'),
                           HOME=str(root/'home'), XDG_CONFIG_HOME=str(root/'home/.config'))
        containerd_config = ('version = 3\nroot = '+json.dumps(str(root/'containerd-data'))+'\nstate = '+json.dumps(str(root/'containerd-state'))+
            '\ndisabled_plugins = ["io.containerd.cri.v1.images", "io.containerd.cri.v1.runtime", "io.containerd.grpc.v1.cri", "io.containerd.podsandbox.controller.v1.podsandbox"]\n[grpc]\naddress = '+json.dumps(str(root/'containerd.sock'))+'\n'+
            '[plugins."io.containerd.internal.v1.opt"]\npath = '+json.dumps(str(root/'opt'))+'\n'+
            '[plugins."io.containerd.image-verifier.v1.bindir"]\nbin_dir = '+json.dumps(str(root/'verifiers'))+'\n'+
            '[plugins."io.containerd.nri.v1.nri"]\ndisable = true\nsocket_path = '+json.dumps(str(root/'nri.sock'))+'\nplugin_path = '+json.dumps(str(root/'nri-plugins'))+'\nplugin_config_path = '+json.dumps(str(root/'nri-config'))+'\n')
        (root/'containerd.toml').write_text(containerd_config)
        with (root/'containerd.log').open('ab') as log:
            process = subprocess.Popen(['sudo', '-n', 'env', 'TMPDIR='+str(root/'tmp'), 'HOME='+str(root/'home'),
                'containerd', '--config', str(root/'containerd.toml')], stdout=log, stderr=log,
                stdin=subprocess.DEVNULL, start_new_session=True)
        # sudo may exec in place or retain a supervisor; resolve the actual process below.
        for _ in range(50):
            if (root/'containerd.sock').exists(): break
            time.sleep(0.1)
        else: raise RuntimeError('project containerd failed to start')
        candidates = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit(): continue
            try: words = (proc/'cmdline').read_bytes().split(b'\0')
            except (OSError, PermissionError): continue
            if words and words[0].decode(errors='replace').endswith('containerd') and str(root/'containerd.toml').encode() in words:
                candidates.append(int(proc.name))
        if len(candidates) != 1: raise RuntimeError('could not identify project containerd')
        containerd_pid.write_text(str(candidates[0]))
        with (root/'daemon.log').open('ab') as log:
            subprocess.Popen(['sudo', '-n', 'env',
                              *[key+'='+environment[key] for key in ('DOCKER_TMPDIR','TMPDIR','HOME','XDG_CONFIG_HOME')],
                              'dockerd', '--config-file', str(root/'daemon.json')],
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(30):
            result = subprocess.run([*command, 'info', '--format', '{{.DockerRootDir}}'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                if Path(result.stdout.strip()).resolve() != root/'data': raise ValueError('daemon root mismatch')
                break
            time.sleep(1)
        else: raise RuntimeError('project daemon failed; inspect '+str(root/'daemon.log'))
    subprocess.run([*command, 'info', '--format', '{"root": "{{.DockerRootDir}}", "version": "{{.ServerVersion}}"}'], check=True)


if __name__ == '__main__':
    main()
