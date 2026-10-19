"""Detached per-job supervisor. Only launched after sealed submit authorization."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from abaqus_agent import (Backend, atomic_json, read_json, sha, process_identity,
                          now, windows_command, hidden_flags, terminal_evidence, AgentError)


def supervise(path):
    row = read_json(path)
    backend = Backend(row['config_path'])
    req = row['request']
    row.update(state='STARTING', worker_pid=os.getpid(), worker_identity=process_identity(os.getpid()))
    atomic_json(path, row)
    for file, expected in row['sealed']['dependencies'].items():
        if not Path(file).is_file() or sha(file) != expected:
            raise AgentError('Frozen dependency changed before execution: ' + file)
    for key, file in (('config_sha256', backend.config_path), ('launcher_sha256', backend.launcher),
                      ('worker_sha256', Path(__file__)), ('runner_sha256', Path(__file__).with_name('abaqus_agent.py')),
                      ('probe_sha256', Path(__file__).with_name('odb_probe.py')), ('history_sha256', Path(__file__).with_name('history_peak.py'))):
        if sha(file) != row['sealed'][key]:
            raise AgentError('Frozen runtime changed: ' + key)
    scratch = Path(req['scratch'])
    scratch.mkdir(parents=True, exist_ok=True)
    stream_path = Path(req['workdir']) / (req['job'] + '.agent-stdout.log')
    with stream_path.open('xb') as stream:
        process = subprocess.Popen(windows_command(row['command_args']), cwd=req['workdir'],
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=hidden_flags(), close_fds=True)
        row.update(state='RUNNING', launcher_pid=process.pid, launcher_identity=process_identity(process.pid),
                   started_at=now(), started_epoch=time.time())
        atomic_json(path, row)
        next_probe, stop_sent = 0.0, False
        while process.poll() is None:
            current = time.time()
            row['last_observed_at'] = now()
            row['walltime_seconds'] = current - row['started_epoch']
            reason = None
            if row['walltime_seconds'] >= req['walltime_limit_seconds']:
                row['walltime_limit_reached'] = True
                if req['terminate_at_walltime']:
                    reason = 'AUTHORIZED_WALLTIME_LIMIT'
                else:
                    row['attention'] = 'WALLTIME_LIMIT_REACHED_NO_TERMINATION_AUTHORITY'
            odb = Path(req['workdir']) / (req['job'] + '.odb')
            if req.get('probe') and odb.is_file() and current >= next_probe:
                try:
                    probe = backend.probe_odb(req['probe'])
                    row['latest_probe'] = probe
                    if req['stop_after_confirmed_peak'] and probe.get('strict_confirmed') and probe.get('usable_peak'):
                        reason = 'AUTHORIZED_CONFIRMED_PEAK_STOP'
                except Exception as exc:
                    row['latest_probe_error'] = str(exc)
                next_probe = current + req['probe_interval_seconds']
            if reason and not stop_sent:
                # Verify original launcher identity, never terminate by a broad executable name.
                if process_identity(process.pid) != row['launcher_identity']:
                    row['attention'] = 'LAUNCHER_IDENTITY_CHANGED_NO_STOP'
                else:
                    command = [str(backend.launcher), 'terminate', 'job=' + req['job']]
                    try:
                        result = subprocess.run(windows_command(command), cwd=req['workdir'],
                            capture_output=True, timeout=20, creationflags=hidden_flags())
                        row['stop_request'] = dict(reason=reason, time=now(), returncode=result.returncode,
                            output=result.stdout.decode(errors='replace')[-1500:])
                    except Exception as exc:
                        row['stop_request'] = dict(reason=reason, time=now(), error=str(exc))
                    stop_sent = True
                    row['state'] = 'STOP_REQUESTED'
            atomic_json(path, row)
            time.sleep(2)
        row['launcher_returncode'] = process.returncode
    evidence = terminal_evidence(req['workdir'], req['job'])
    row['terminal_evidence'] = evidence
    if row.get('stop_request'):
        row['state'] = 'TERMINATED_BY_AUTHORIZED_POLICY'
    elif evidence['abort_marker'] or evidence['dat_error_lines'] or process.returncode:
        row['state'] = 'INPUT_CHECK_FAILED' if req.get('operation') == 'datacheck' else 'ABORTED'
    elif evidence['completion_marker']:
        row['state'] = 'INPUT_CHECK_PASSED' if req.get('operation') == 'datacheck' else 'COMPLETED'
    else:
        row['state'] = 'ENDED_UNCONFIRMED'
    if req.get('probe') and odb.is_file():
        try:
            row['latest_probe'] = backend.probe_odb(req['probe'])
        except Exception as exc:
            row['latest_probe_error'] = str(exc)
    row.update(finished=True, finished_at=now(), walltime_seconds=time.time() - row['started_epoch'])
    atomic_json(path, row)
    return row


if __name__ == '__main__':
    record_path = Path(sys.argv[1])
    try:
        supervise(record_path)
    except Exception as exc:
        row = read_json(record_path)
        # An existing launcher may still be running. Never pretend supervisor failure stopped it.
        still_live = bool(row.get('launcher_pid')) and process_identity(row.get('launcher_pid')) == row.get('launcher_identity')
        row.update(state='SUPERVISOR_FAILED_LAUNCHER_MAY_BE_RUNNING' if still_live else 'START_FAILED',
            finished=not still_live, error=str(exc), traceback=traceback.format_exc(), failure_time=now())
        atomic_json(record_path, row)
        sys.exit(2)
