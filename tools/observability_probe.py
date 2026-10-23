"""Fixed-client, loopback-only capability diagnosis. Never contacts an upstream model.

The server is a finite fixture, not a forwarding proxy. A new user/network
namespace is mandatory; the frozen runner and inner command policy are retained.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import shlex
import socket
import subprocess
import sys
import threading
import time

from experiment_store import atomic_bytes, output_path, sha, write
from standalone import PROJECT, clean_env

CAPABILITIES = ('physical_request_identity', 'attempt_attribution', 'durable_before_send',
                'response_fidelity', 'optional_cost_fidelity', 'terminal_coverage',
                'retry_visibility', 'loss_detection', 'policy_preservation')


def probe(output):
    output = output_path(output)
    if output.exists():
        raise ValueError('diagnostic output must be new')
    output.mkdir(parents=True, mode=0o700)
    env = clean_env(output)
    # Entire process tree has no interface except its private loopback.
    cmd = ['unshare', '--user', '--map-root-user', '--net', sys.executable, '-B',
           str(Path(__file__).resolve()), '--worker', '--output', str(output)]
    with (output / 'diagnostic.log').open('wb') as log:
        process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=180)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    if code:
        raise RuntimeError('isolated diagnostic failed; see diagnostic.log')
    return json.loads((output / 'capability.json').read_text())


def worker(output):
    if socket.if_nameindex() != [(1, 'lo')]:
        raise RuntimeError('diagnostic requires a fresh loopback-only namespace')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    os.environ['EVIWEAVE_API_KEY'] = 'invalid-local-fixture-key'
    import yaml
    from dataspace_baselines.config import load_harness_experiment_config
    from dataspace_baselines.core.task import TaskSpec
    from dataspace_baselines.harnesses import build_harness
    from config_lifecycle import read_config

    runtime = PROJECT / '.runtimes/codex-0.145.0/platform-package/vendor/x86_64-unknown-linux-musl'
    binary = runtime / 'bin/codex'
    env = clean_env(output)
    env['CODEX_HOME'] = str(output / 'home/.codex')
    Path(env['CODEX_HOME']).mkdir(parents=True)
    version = subprocess.check_output([str(binary), '--version'], env=env, timeout=10).decode().strip()
    if version != 'codex-cli 0.145.0':
        raise ValueError('requires fixed codex-cli 0.145.0')
    for args, name in [(['--help'], 'help'), (['exec', '--help'], 'exec-help')]:
        atomic_bytes(output / (name + '.txt'), subprocess.check_output([str(binary), *args], env=env, timeout=10))

    truth, exports = [], []
    current = {'scenario': 'baseline', 'count': 0}
    lock = threading.Lock()
    # These extensions must survive in a full-evidence collector, byte for byte.
    usage = {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
             'input_tokens_details': {'cached_tokens': 25},
             'output_tokens_details': {'reasoning_tokens': 4},
             'x_details': [{'novel': '1.234567890123456789e-18', 'count': 7}]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if size > 8 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            if self.path == '/v1/logs':
                with lock:
                    index = len(exports)
                    name = f'telemetry-{index:04d}.bin'
                    atomic_bytes(output / name, body)
                    exports.append({'file': name, 'content_type': self.headers.get('Content-Type'),
                                    'scenario_at_receive': current['scenario'],
                                    'sha256': hashlib.sha256(body).hexdigest()})
                    write(output / 'exports.json', exports)
                self.send_response(503 if current['scenario'] == 'collector-failure' else 200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')
                return
            if self.path != '/v1/responses':
                self.send_error(404)
                return
            with lock:
                current['count'] += 1
                n = current['count']
                scenario = current['scenario']
                truth.append({'scenario': scenario, 'request': n, 'path': self.path,
                              'body_sha256': hashlib.sha256(body).hexdigest(),
                              'monotonic_ns': time.monotonic_ns()})
                write(output / 'server-truth.json', truth)
            if scenario in ('401', '429', '500'):
                self.send_response(int(scenario))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"fixture failure","type":"fixture_error"}}')
                return
            if scenario in ('timeout', 'cancel'):
                time.sleep(5)
                self.close_connection = True
                return
            if scenario == 'disconnect':
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'event: response.created\ndata: {"type":"response.created","response":{"id":"half","usage":null}}\n\n')
                self.close_connection = True
                return
            if n == 1:
                # Generated command probes network isolation and private evidence visibility.
                script = ("import socket,pathlib,json\n"
                          "assert not pathlib.Path(" + repr(str(output)) + ").exists()\n"
                          "try:\n s=socket.socket(); s.settimeout(1); denied=s.connect_ex(('127.0.0.1'," + str(self.server.server_port) + ")) != 0\n"
                          "except PermissionError:\n denied=True\n"
                          "assert denied\npathlib.Path('/mnt/output/network-probe.json').write_text(json.dumps({'network_denied':denied,'evidence_directory_hidden':True}))\n")
                cmd = 'python3 -c ' + shlex.quote(script)
                item = {'id': 'fc_fixture', 'type': 'function_call', 'call_id': 'call_fixture',
                        'name': 'exec_command', 'arguments': json.dumps({'cmd': cmd})}
            else:
                item = {'id': 'msg_fixture', 'type': 'message', 'status': 'completed', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'Artificial protocol fixture completed.', 'annotations': []}]}
            response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 1,
                        'status': 'completed', 'model': 'qwen3.8-flash', 'output': [item],
                        'usage': usage, 'fixture_cost': {'amount': '0.00000000000000000123', 'currency': 'TEST'}}
            events = [{'type': 'response.created', 'response': dict(response, status='in_progress', output=[], usage=None)},
                      {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
                      {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': response}]
            raw = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode()
            atomic_bytes(output / f'server-response-{scenario}-{n}.sse', raw)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f'http://127.0.0.1:{server.server_port}'
    outcomes = []
    try:
        for scenario in ('baseline', 'json', '401', '429', '500', 'disconnect', 'timeout', 'cancel', 'collector-failure'):
            current.update(scenario=scenario, count=0)
            case = output / scenario
            case.mkdir()
            raw = yaml.safe_load((PROJECT / 'configs/codex.example.yaml').read_text())
            raw['experiment']['model']['openai_base_url'] = address + '/v1'
            raw['experiment']['workbench']['rootfs_path'] = str(PROJECT / '.runtimes/data-workbench-1.0/rootfs')
            raw['harnesses']['codex']['runtime_path'] = str(runtime)
            raw['experiment']['wall_time_seconds'] = 4 if scenario in ('500', 'disconnect', 'timeout', 'cancel', 'collector-failure') else 15
            write(case / 'config.json', raw)
            config = load_harness_experiment_config(case / 'config.json')
            runner = build_harness('codex', config)
            base = runner.invocation_factory
            def invocation(task, run_root, output_root):
                inv = base(task, run_root, output_root)
                command = list(inv.command)
                extra = [] if scenario == 'baseline' else [
                    '-c', 'otel.environment="eviweave-fixture-' + scenario + '"',
                    '-c', 'otel.log_user_prompt=false',
                    '-c', 'otel.exporter={otlp-http={endpoint="' + address + '/v1/logs",protocol="json"}}']
                command.extend(extra)
                local_env = dict(inv.environment, EVIWEAVE_API_KEY='invalid-local-fixture-key')
                write(case / 'invocation.json', {'command': command, 'observation_overrides': extra})
                if scenario in ('baseline', 'json'):
                    split = command.index('--')
                    config_command = command[:split + 1] + [command[split + 1], 'app-server', '--strict-config', *extra]
                    try:
                        read_config(config_command, local_env, case / 'effective-config')
                    except (AssertionError, TimeoutError) as exc:
                        write(case / 'config-read-error.json', {'error': str(exc)})
                return replace(inv, command=tuple(command), environment=local_env, inherit_environment=False)
            runner.invocation_factory = invocation
            run_dir = case / 'attempt'
            timer = threading.Timer(1, runner.cancel) if scenario == 'cancel' else None
            if timer:
                timer.start()
            try:
                result = runner.run(TaskSpec.load(PROJECT / 'examples/synthetic/tasks/task_1'), run_dir)
            finally:
                if timer:
                    timer.cancel()
                    timer.join()
            outcomes.append({'scenario': scenario, 'http_requests': current['count'],
                             'inner_isolation': json.loads((run_dir / 'output/network-probe.json').read_text()) if (run_dir / 'output/network-probe.json').exists() else None,
                             'status': result.status, 'run_file': str((run_dir / 'run.json').relative_to(output))})
        # The unmodified client cannot provide raw extensions it discards. Do not
        # infer a pre-send hook or loss-free telemetry from eventual event counts.
        telemetry = b''.join((output / e['file']).read_bytes() for e in exports)
        telemetry_events = []
        for exported in exports:
            value = json.loads((output / exported['file']).read_bytes())
            for resource in value.get('resourceLogs', []):
                for scope in resource.get('scopeLogs', []):
                    for event in scope.get('logRecords', []):
                        attrs = {a['key']: next(iter(a['value'].values()), None) for a in event.get('attributes', [])}
                        if attrs.get('event.name') in ('codex.api_request', 'codex.sse_event'):
                            telemetry_events.append({'scenario_at_receive': exported['scenario_at_receive'], 'attributes': attrs})
        write(output / 'telemetry-counter-evidence.json', telemetry_events)
        statuses = {name: {'status': 'unknown', 'evidence': 'not established by this finite diagnostic'} for name in CAPABILITIES}
        for name in ('durable_before_send', 'response_fidelity', 'optional_cost_fidelity'):
            statuses[name] = {'status': 'unsupported', 'evidence':
                'No producer pre-send persistence hook exposed; native telemetry is asynchronous and omits raw usage/extensions.'}
        statuses['attempt_attribution'] = {'status': 'unknown',
                                           'evidence': 'sequential isolated sessions only; concurrency/physical-attempt identity not certified'}
        base_config = json.loads((output / 'baseline/effective-config/response.json').read_text())['config']
        observed_config = json.loads((output / 'json/effective-config/response.json').read_text())['config']
        differences = [k for k in set(base_config) | set(observed_config) if base_config.get(k) != observed_config.get(k)]
        write(output / 'effective-config-differences.json', {'changed_keys': differences})
        if set(differences) <= {'otel'} and all(o['inner_isolation'] for o in outcomes[:2]):
            statuses['policy_preservation'] = {'status': 'verified', 'evidence': 'effective config differs only in otel; actual inner command denies network and private directory visibility'}
        completed_calls = sum(x['http_requests'] for x in outcomes)
        report = {'kind': 'eviweave-observation-capability', 'schema_version': 1,
                  'client_version': version, 'binary_sha256': sha(binary),
                  'capabilities': statuses, 'g1': 'UNSUPPORTED_FULL',
                  'online_profiles': [], 'offline_level': 'raw-usage-import',
                  'truth_requests': completed_calls, 'outcomes': outcomes,
                  'telemetry_batches': len(exports),
                  'extensions_present_in_telemetry': b'fixture_cost' in telemetry and b'x_details' in telemetry,
                  'otlp_http_json': 'verified' if telemetry_events else 'unknown',
                  'unverified_cases': ['connection-failure', 'non-streaming', 'concurrent attribution', 'crash-before-send', 'export flush guarantees'],
                  'real_model_calls': 0, 'real_service_auth': 'NOT_TESTED',
                  'namespace': 'private user/net; loopback only',
                  'note': 'Unsupported full evidence. No online counter profile certified; imported evidence cannot prove pre-send persistence.'}
        write(output / 'capability.json', report)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.worker:
        worker(args.output)
    else:
        print(json.dumps(probe(args.output), ensure_ascii=False))
