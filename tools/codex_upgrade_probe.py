#!/usr/bin/env python3
"""Run real Codex against finite synthetic responses in a private loopback network."""
from __future__ import annotations
import argparse
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'src'), str(PROJECT / 'tools')]
from standalone import clean_env, fresh
from experiment_store import write


CASES = ('baseline', 'upgrade', 'persistent', 'resume', '401', '402', '403', 'timeout', 'cancel')


def completed_turn_usage(cumulative, previous):
    """Check the delta of completed synthetic turns without experiment helpers."""
    required = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if (not all(k in cumulative and k in previous for k in required)
            or any(type(v) is not int or v < 0 for values in (cumulative, previous) for v in values.values())):
        raise ValueError('completed synthetic usage is missing or invalid')
    delta = {k: v - previous.get(k, 0) for k, v in cumulative.items()}
    if any(v < 0 for v in delta.values()) or delta['cached_input_tokens'] > delta['input_tokens']:
        raise ValueError('completed synthetic usage is inconsistent')
    return delta


def probe(output, rootfs, baseline, cases=CASES):
    output = fresh(output); output.mkdir(parents=True)
    env = clean_env(output)
    env['PYTHONPATH'] = str(PROJECT / 'src') + ':' + str(PROJECT / 'tools')
    command = ['bwrap', '--die-with-parent', '--unshare-net', '--cap-add', 'CAP_NET_ADMIN',
               '--ro-bind', '/', '/', '--unshare-pid', '--proc', '/proc', '--dev-bind', '/dev/null', '/dev/null',
               '--bind', str(output), str(output), '--', sys.executable, '-B',
               str(Path(__file__).resolve()), '--worker', '--output', str(output),
               '--rootfs', str(rootfs), '--baseline-runtime', str(baseline), '--cases', *cases]
    write(output / 'launcher.json', {'command': command, 'real_model_calls': 0})
    with (output / 'worker.log').open('w') as log:
        p = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = p.wait(timeout=300)
        except BaseException:
            os.killpg(p.pid, signal.SIGKILL); p.wait(); raise
    if code:
        raise RuntimeError('offline compatibility failed; see ' + str(output / 'worker.log'))
    return json.loads((output / 'result.json').read_text())


def worker(output, rootfs, baseline, network_ready=False, cases=CASES):
    if socket.if_nameindex() != [(1, 'lo')]:
        raise RuntimeError('private loopback network required; no host fallback')
    if not network_ready:
        subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
        os.execv('/usr/bin/setpriv', ['setpriv', '--inh-caps=-all', '--ambient-caps=-all',
            sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--network-ready',
            '--output', str(output), '--rootfs', str(rootfs), '--baseline-runtime', str(baseline), '--cases', *cases])
    os.environ['EVIWEAVE_API_KEY'] = 'invalid-offline-fixture'
    import yaml
    from dataspace_baselines.config import load_harness_experiment_config
    from dataspace_baselines.core.task import TaskSpec
    from run_v8 import build_v8_harness
    from config_lifecycle import read_config
    from experiment_scheduler import global_failure
    from codex_upgrade import sha

    task = TaskSpec.load(PROJECT / 'examples/synthetic/tasks/task_1')
    current = {'scenario': '', 'count': 0}
    requests = []
    usage = {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
             'input_tokens_details': {'cached_tokens': 25}, 'output_tokens_details': {'reasoning_tokens': 0}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_error(404)
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            body = json.loads(raw)
            current['count'] += 1
            scenario, n = current['scenario'], current['count']
            record = {'scenario': scenario, 'number': n, 'path': self.path,
                      'body': body, 'content_encoding': self.headers.get('Content-Encoding')}
            requests.append(record)
            write(output / 'requests.json', requests)
            if self.path != '/v1/responses':
                self.send_error(404); return
            if scenario in ('401', '402', '403'):
                self.send_response(int(scenario)); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(b'{"error":{"message":"synthetic authentication failure","type":"invalid_api_key"}}')
                return
            if scenario in ('timeout', 'cancel'):
                time.sleep(4); self.close_connection = True; return
            if n == 1:
                script = """import os,socket,json,pathlib
p=pathlib.Path
assert not p(HIDDEN).exists()
assert not os.environ.get('EVIWEAVE_API_KEY')
try:
 s=socket.socket(); s.settimeout(1); denied=s.connect_ex(('127.0.0.1',PORT))!=0
except PermissionError:
 denied=True
assert denied
try:
 p('/mnt/workspace/should-not-write').write_text('bad')
except OSError:
 readonly=True
else:
 readonly=False
assert readonly
p('/mnt/output/prediction.csv').write_text('answer\\n42\\n')
p('/mnt/output/isolation.json').write_text(json.dumps({'network_denied':denied,'input_readonly':readonly,'credentials_absent':True,'host_hidden':True}))
""".replace('HIDDEN', repr(str(output))).replace('PORT', str(self.server.server_port))
                if scenario == 'pdf':
                    script += """import subprocess
data=json.loads(subprocess.check_output(['python3','/mnt/runtime/eviweave-pdf-reader.py','parse','/mnt/workspace/fixture.pdf','--pages','1','--language','eng']))
assert 'Codex offline fixture 42' in data['pages'][0]['text'], data
p('/mnt/output/pdf-check.json').write_text(json.dumps(data))
"""
                item = {'id': 'fc_' + str(n), 'type': 'function_call', 'call_id': 'call_' + str(n),
                        'name': 'exec_command', 'arguments': json.dumps({'cmd': 'python3 -c ' + shlex.quote(script)})}
            else:
                item = {'id': 'msg_' + str(n), 'type': 'message', 'status': 'completed', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'Synthetic fixture completed.', 'annotations': []}]}
            response = {'id': 'resp_' + str(n), 'object': 'response', 'created_at': 1, 'status': 'completed',
                        'model': 'qwen3.8-flash', 'output': [item], 'usage': usage}
            events = [{'type': 'response.created', 'response': dict(response, status='in_progress', output=[], usage=None)},
                      {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
                      {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': response}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    outcomes = []
    saved = None
    newruntime = PROJECT / '.runtimes/codex-0.170.0/platform-package/vendor/x86_64-unknown-linux-musl'
    try:
        for scenario in cases:
            current.update(scenario=scenario, count=0)
            case = output / scenario; case.mkdir()
            task = TaskSpec.load(PROJECT / 'examples/synthetic/tasks/task_1')
            if scenario == 'pdf':
                directory = case / 'task_1'; (directory / 'context').mkdir(parents=True)
                write(directory / 'task.json', {'task_id': 'task_1', 'question': 'Synthetic offline PDF reading check.'})
                stream = b'BT /F1 12 Tf 10 100 Td (Codex offline fixture 42) Tj ET'
                objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                    b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                    b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                    b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
                pdf = b'%PDF-1.4\n'; offsets = [0]
                for number, obj in enumerate(objects, 1):
                    offsets.append(len(pdf)); pdf += f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n'
                start = len(pdf)
                pdf += b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010} 00000 n \n'.encode() for n in offsets[1:])
                pdf += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
                (directory / 'context/fixture.pdf').write_bytes(pdf)
                task = TaskSpec.load(directory)
            raw = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
            version = '0.145.0' if scenario == 'baseline' else '0.170.0'
            runtime = baseline if scenario == 'baseline' else newruntime
            raw['harnesses']['codex'].update(version=version, runtime_path=str(runtime))
            raw['experiment']['model']['openai_base_url'] = f'http://127.0.0.1:{server.server_port}/v1'
            raw['experiment']['workbench']['rootfs_path'] = str(rootfs)
            raw['experiment']['wall_time_seconds'] = 2 if scenario == 'timeout' else 20
            write(case / 'fixture-config.json', raw)
            config = load_harness_experiment_config(case / 'fixture-config.json')
            runner = build_v8_harness(config); runner.termination_grace_seconds = 0
            original = runner.invocation_factory
            runroot = case / 'attempt'
            if scenario == 'resume':
                shutil.copytree(saved[0] / 'runtime/home/.codex', runroot / 'runtime/home/.codex')
            def invocation(task, run_root, output_root):
                inv = original(task, run_root, output_root)
                command = list(inv.command); boundary = command.index('--')
                if scenario in ('baseline', 'upgrade'):
                    config_command = command[:boundary+1] + [command[boundary+1], 'app-server', '--strict-config']
                    read_config(config_command, inv.environment, case / 'effective-config', expected_version=version)
                    for args, name in [(['--version'], 'version'), (['exec', '--help'], 'exec-help'),
                                       (['exec', 'resume', '--help'], 'resume-help')]:
                        data = subprocess.check_output(command[:boundary+1] + [command[boundary+1], *args], env=inv.environment, timeout=15)
                        (case / (name + '.txt')).write_bytes(data)
                if scenario == 'persistent': command.remove('--ephemeral')
                if scenario == 'resume':
                    command = command[:boundary+1] + [command[boundary+1], 'exec', 'resume', '--json',
                        '--strict-config', '--skip-git-repo-check', '--ignore-rules', saved[1], command[-1]]
                write(case / 'invocation.json', {'command': command, 'metadata': inv.metadata})
                return replace(inv, command=tuple(command))
            runner.invocation_factory = invocation
            timer = threading.Timer(1.5, runner.cancel) if scenario == 'cancel' else None
            if timer: timer.start()
            try: result = runner.run(task, runroot)
            finally:
                if timer: timer.cancel(); timer.join()
            row = {'scenario': scenario, 'status': result.status, 'requests': current['count'],
                   'usage': dict(result.usage), 'tool_actions': result.tool_actions,
                   'global_failure': global_failure(runroot)}
            events = [json.loads(l) for l in (runroot / 'stdout.log').read_text().splitlines() if l.startswith('{')]
            sessions = [e['thread_id'] for e in events if e.get('type') == 'thread.started']
            if scenario in ('baseline', 'upgrade', 'persistent', 'resume', 'pdf'):
                assert result.status == 'submitted', row
                assert current['count'] == 2, row
                assert result.tool_actions >= 1, row
                factor = 2 if scenario == 'resume' else 1
                assert result.usage['input_tokens'] == 200 * factor and result.usage['output_tokens'] == 20 * factor, row
                assert result.usage['cached_input_tokens'] == 50 * factor, row
                isolation = json.loads((runroot / 'output/isolation.json').read_text())
                assert all(isolation.values()), isolation
                row['isolation'] = isolation
                if scenario == 'pdf':
                    assert (runroot / 'output/pdf-check.json').is_file()
                    row['pdf_reader'] = 'native text extraction verified'
                if scenario == 'persistent':
                    assert len(sessions) == 1
                    files = list((runroot / 'runtime/home/.codex/sessions').rglob('*'+sessions[0]+'.jsonl'))
                    assert len(files) == 1
                    saved = (runroot, sessions[0], sha(files[0]), files[0], dict(result.usage))
                if scenario == 'resume':
                    assert sessions == [saved[1]], sessions
                    assert sha(saved[3]) == saved[2], 'original session mutated'
                    row['source_session_unchanged'] = True
                    delta = completed_turn_usage(result.usage, saved[4])
                    assert delta['input_tokens'] == 200 and delta['output_tokens'] == 20
                    row['resumed_turn_usage'] = delta
                if scenario in ('baseline', 'upgrade'):
                    assert not list((runroot / 'runtime/home/.codex/sessions').rglob('*.jsonl'))
            elif scenario in ('401', '402', '403'):
                assert result.status == 'runtime_error' and row['global_failure'], row
            elif scenario == 'timeout': assert result.status == 'wall_time_exhausted', row
            else: assert result.status == 'stopped', row
            outcomes.append(row)
            write(output / 'progress.json', outcomes)
        before = json.loads((output / 'baseline/effective-config/response.json').read_text())['config'] if 'baseline' in cases else {}
        after = json.loads((output / 'upgrade/effective-config/response.json').read_text())['config'] if 'upgrade' in cases else {}
        differences = {k: {'old': before.get(k), 'new': after.get(k)} for k in set(before) | set(after) if before.get(k) != after.get(k)}
        write(output / 'effective-config-diff.json', differences)
        initial = {name: next((r['body'] for r in requests if r['scenario']==name), {}) for name in ('baseline', 'upgrade')}
        write(output / 'request-comparison.json', {k: {'old': initial['baseline'].get(k), 'new': initial['upgrade'].get(k)}
              for k in set(initial['baseline']) | set(initial['upgrade']) if initial['baseline'].get(k)!=initial['upgrade'].get(k)})
        assert all(r['body']['model'] == 'qwen3.8-flash' for r in requests)
        assert all(r['path']=='/v1/responses' and not r['content_encoding'] for r in requests)
        assert all(not any(t.get('name') in ('create_goal', 'update_goal', 'get_goal') for t in r['body'].get('tools', []))
                   for r in requests if r['scenario'] != 'baseline')
        write(output / 'result.json', {'status': 'PASS', 'real_model_calls': 0, 'network': 'private loopback only',
            'test_budget_only': '2/20 seconds; production config unchanged', 'outcomes': outcomes,
            'effective_config_changed_keys': sorted(differences), 'real_service_compatibility': 'NOT_TESTED'})
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rootfs', type=Path, required=True, help='explicit read-only Workbench fixture')
    p.add_argument('--baseline-runtime', type=Path, required=True, help='explicit read-only 0.145.0 vendor directory')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--network-ready', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--cases', nargs='+', choices=(*CASES, 'pdf'), default=CASES)
    a = p.parse_args()
    if 'resume' in a.cases and ('persistent' not in a.cases or a.cases.index('persistent') > a.cases.index('resume')):
        p.error('resume requires an earlier persistent case')
    if a.worker: worker(a.output, a.rootfs, a.baseline_runtime, a.network_ready, a.cases)
    else: print(json.dumps(probe(a.output.resolve(), a.rootfs.resolve(), a.baseline_runtime.resolve(), a.cases), indent=2))


if __name__ == '__main__': main()
