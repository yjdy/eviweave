"""Bounded round scheduling and recovery outside the unchanged frozen runner."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import threading

from experiment_store import (KIND, atomic_bytes, attempts, completed, evidence, fingerprint, internal,
                              load, locked, original, plan_for, read, registered_inputs, repair, select, sha, uid, write)
from standalone import clean_env, import_evaluation


def initialize(root, prepared):
    (root / 'resources').mkdir(parents=True)
    import_evaluation(prepared['paths']['evaluation'], root / 'resources/evaluation')
    atomic_bytes(root / 'config.resolved.yaml', prepared['config_bytes'])
    write(root / 'resources/environment-lock.json', prepared['environment'])
    write(root / 'resources/inputs.json', prepared['inputs'])
    write(root / 'resources/inputs-initial.json', prepared['inputs'])
    write(root / 'selection.json', {})
    return {'kind': KIND, 'format_version': 1, 'identity': prepared['identity'],
            'identity_sha256': fingerprint(prepared['identity']), 'paths': prepared['paths'],
            'inputs_sha256': fingerprint(prepared['inputs']), 'inputs_file': 'resources/inputs-initial.json', 'plans': {}, 'active_plan': None}


def add_plan(root, doc, tasks, repeat, inputs):
    previous = registered_inputs(root, doc)
    if any(inputs.get(t) != files for t, files in previous.items()):
        raise ValueError('registered task input changed')
    pid = uid()
    plan = {'plan_id': pid, 'tasks': tasks, 'repeat': repeat,
            'slots': [f'{t}:{r}' for r in range(1, repeat + 1) for t in tasks]}
    path = root / f'plans/{pid}/plan.json'
    write(path, plan)
    snapshot = f'resources/inputs-{pid}.json'
    write(root / snapshot, inputs)
    doc.update(active_plan=pid, inputs_sha256=fingerprint(inputs), inputs_file=snapshot)
    doc['plans'][pid] = sha(path)
    write(root / 'experiment.json', doc)
    write(root / 'resources/inputs.json', inputs)
    return plan


def pending_slots(root, plan, records):
    latest = {a['slot']: a for a in records if a['plan_id'] == plan['plan_id']}
    return [slot for slot in plan['slots'] if slot not in latest or not completed(root, latest[slot])]


def global_failure(directory):
    """Only structured API failures are global; never scan arbitrary tool text."""
    path = directory / 'stdout.log'
    if not path.is_file():
        return None
    codes = {'invalid_api_key', 'authentication_error', 'unauthorized', 'insufficient_quota',
             'quota_exceeded', 'billing_hard_limit_reached', 'account_deactivated'}
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get('type') not in {'error', 'turn.failed'}:
            continue
        error = event.get('error')
        if isinstance(error, dict) and (error.get('code') in codes or error.get('type') in codes
                                       or error.get('status_code') in (401, 402, 403)):
            return 'structured service authentication/quota failure'
    return None


@contextmanager
def run_environment(root, live=True):
    # Set once, before any workers are created. Worker threads never mutate globals.
    old = dict(os.environ)
    env = clean_env(root, live=live)
    if old.get('DOCKER_HOST'):
        env['DOCKER_HOST'] = old['DOCKER_HOST']
    try:
        os.environ.clear()
        os.environ.update(env)
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def production_runner(config):
    from dataspace_baselines.harnesses import build_harness
    return build_harness('codex', config)


def doctor(config, output):
    from dataspace_baselines.doctor import run_doctor
    tmp = output / 'doctor-tmp'
    tmp.mkdir()
    checks = run_doctor(config, offline=False, temp_dir=tmp)
    write(output / 'doctor.json', [dict(name=c.name, ok=c.ok, detail=c.detail) for c in checks])
    if not all(c.ok for c in checks):
        raise RuntimeError('global environment/sandbox check failed; see doctor.json')


def schedule(root, doc, plan, config, dataset, resume=False, runner_factory=production_runner,
             doctor_fn=doctor, live=True):
    """Caller holds the experiment lock. Internal injection exists only for offline tests."""
    records = repair(root, doc)
    pending = pending_slots(root, plan, records) if resume else list(plan['slots'])
    launch_id = uid()
    launch = {'launch_id': launch_id, 'plan_id': plan['plan_id'], 'resume': resume,
              'pending': pending, 'status': 'running', 'stop_reason': None}
    path = root / f'launches/{launch_id}.json'
    write(path, launch)
    if not pending:
        launch.update(status='complete', started_attempts=0)
        write(path, launch)
        return 0
    if live and not os.environ.get('EVIWEAVE_API_KEY', '').strip():
        launch.update(status='failed', stop_reason='missing dedicated EVIWEAVE_API_KEY', started_attempts=0)
        write(path, launch)
        raise ValueError('set dedicated EVIWEAVE_API_KEY; no credential fallback')
    stopped = threading.Event()
    interrupted = []
    runner = None
    old_handlers = {}
    started = 0
    failure = False

    def stop(signum, frame):
        interrupted.append(signum)
        stopped.set()
        if runner is not None:
            runner.cancel()

    def allocate(slot):
        nonlocal started
        task, repeat = map(int, slot.split(':'))
        parent = root / f'tasks/task_{task}'
        parent.mkdir(parents=True, exist_ok=True)
        number = max([int(p.name.removeprefix('run_')) for p in parent.iterdir()] + [0]) + 1
        directory = parent / f'run_{number}'
        directory.mkdir(mode=0o700)
        previous = [a for a in records if a['plan_id'] == plan['plan_id'] and a['slot'] == slot]
        record = {'attempt_id': directory.relative_to(root).as_posix(), 'task': task, 'repeat': repeat,
                  'plan_id': plan['plan_id'], 'launch_id': launch_id, 'slot': slot,
                  'sequence': max([a['sequence'] for a in records] + [0]) + 1,
                  'previous_attempt': previous[-1]['attempt_id'] if previous else None,
                  'status': 'prepared', 'prediction_sha256': None, 'model_started': None}
        write(directory / 'attempt.json', record)
        records.append(record)
        write(root / 'selection.json', select(records))
        (root / f'predictions/task_{task}/prediction.csv').unlink(missing_ok=True)
        started += 1
        return record

    def worker(record):
        from dataspace_baselines.core.task import TaskSpec
        directory = internal(root, record['attempt_id'])
        try:
            if stopped.is_set():
                record.update(status='interrupted', model_started=False)
                write(directory / 'attempt.json', record)
                return None
            record.update(status='running', runner_called=True)
            write(directory / 'attempt.json', record)
            runner.run(TaskSpec.load(dataset / f'input/task_{record["task"]}'), directory)
            run = read(directory / 'run.json')
            record.update(status=run['status'], model_started=True if run.get('model_turns', 0) > 0 else None)
            if record['status'] == 'submitted':
                run, prediction = evidence(root, record)
                record['prediction_sha256'] = prediction['sha256']
                atomic_bytes(root / f'predictions/task_{record["task"]}/prediction.csv', original(root, record).read_bytes())
            reason = global_failure(directory)
            if reason:
                stopped.set()
                runner.cancel()
            write(directory / 'attempt.json', record)
            return reason
        except Exception as exc:
            record.update(status='execution_error', error=f'{type(exc).__name__}: {exc}')
            (root / f'predictions/task_{record["task"]}/prediction.csv').unlink(missing_ok=True)
            write(directory / 'attempt.json', record)
            # A worker exception alone is not proof of a global sandbox failure.
            return None

    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, stop)
        launch_root = root / f'reports/launch-{launch_id}'
        launch_root.mkdir(parents=True)
        with run_environment(launch_root, live=live):
            doctor_fn(config, launch_root)
            runner = runner_factory(config)
            if stopped.is_set():
                runner.cancel()
            with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
                for repeat in range(1, plan['repeat'] + 1):
                    queue = iter(s for s in pending if int(s.split(':')[1]) == repeat)
                    active = {}
                    exhausted = False
                    while active or not exhausted:
                        while not stopped.is_set() and len(active) < config.concurrency and not exhausted:
                            try:
                                slot = next(queue)
                            except StopIteration:
                                exhausted = True
                                break
                            a = allocate(slot)
                            active[pool.submit(worker, a)] = a
                        if not active:
                            break
                        finished, _ = wait(active, return_when=FIRST_COMPLETED)
                        for future in finished:
                            a = active.pop(future)
                            reason = future.result()
                            failure = failure or a['status'] != 'submitted'
                            if not reason and a['status'] in ('runtime_error', 'execution_error') and not stopped.is_set():
                                try:
                                    probe = launch_root / ('recheck-' + str(a['sequence']))
                                    probe.mkdir()
                                    doctor_fn(config, probe)
                                except Exception as exc:
                                    reason = 'confirmed global environment failure: ' + str(exc)
                                    stopped.set()
                                    runner.cancel()
                            if reason:
                                launch['stop_reason'] = reason
                        if stopped.is_set():
                            exhausted = True
                    if stopped.is_set():
                        break
        launch['status'] = 'interrupted' if interrupted else ('failed' if failure or stopped.is_set() else 'complete')
        return 130 if interrupted else (1 if failure or stopped.is_set() else 0)
    except Exception as exc:
        launch.update(status='failed', stop_reason=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if interrupted:
            launch['stop_reason'] = 'signal ' + str(interrupted[0])
        launch['started_attempts'] = started
        write(path, launch)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
