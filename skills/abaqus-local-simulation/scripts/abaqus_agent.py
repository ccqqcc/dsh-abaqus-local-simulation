"""Windows local Abaqus backend and JSON CLI. Python >=3.9, stdlib only."""
from __future__ import annotations
import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = '1.0'
SOLVER_NAMES = {'standard.exe', 'explicit.exe', 'explicit_dp.exe', 'pre.exe'}
UNSAFE_CMD = re.compile(r'[&|<>^%!"\r\n]')
JOB_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


class AgentError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def digest_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def safe_argument(value):
    value = str(value)
    if UNSAFE_CMD.search(value) or '\x00' in value:
        raise AgentError('Unsafe cmd.exe argument; use a path without &, |, <, >, ^, %, !, quotes or newlines')
    return value


def windows_command(args):
    args = [safe_argument(v) for v in args]
    if os.name != 'nt':
        return args
    # Explicit BAT wrapper. Outer quotes protect the full /s /c command.
    comspec = safe_argument(os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'))
    inner = ' '.join('"' + value + '"' for value in args)
    return subprocess.list2cmdline([comspec]) + ' /d /s /c "' + inner + '"'


def hidden_flags(detached=False):
    if os.name != 'nt':
        return 0
    return subprocess.CREATE_NO_WINDOW | (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if detached else 0)


def process_identity(pid):
    if not pid:
        return None
    if os.name != 'nt':
        try:
            os.kill(int(pid), 0)
            return int(pid)
        except OSError:
            return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_uint64)] * 4
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, 0, int(pid))
    if not handle:
        return None
    try:
        exit_code = ctypes.c_uint32()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
            return None
        values = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in values]):
            return None
        return values[0].value
    finally:
        kernel.CloseHandle(handle)


def powershell_json(script):
    if os.name != 'nt':
        return {'error': 'WINDOWS_HOST_REQUIRED'}
    script = '[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); ' + script
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, timeout=20, creationflags=hidden_flags())
    if result.returncode:
        return {'error': result.stderr.decode('utf-8', errors='replace')[-1500:]}
    text = result.stdout.decode('utf-8-sig', errors='replace').strip()
    return json.loads(text) if text else None


def process_snapshot():
    result = powershell_json("@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('standard.exe','explicit.exe','explicit_dp.exe','pre.exe')} | Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Depth 4 -Compress")
    if isinstance(result, dict) and 'error' in result:
        raise AgentError('Cannot inspect existing solver processes: ' + result['error'])
    return result if isinstance(result, list) else ([result] if result else [])


def tail(path, limit=20000):
    path = Path(path)
    if not path.is_file():
        return ''
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read().decode('utf-8', errors='replace')


def terminal_evidence(workdir, job):
    rows = {ext: tail(Path(workdir) / (job + ext)) for ext in ('.log', '.msg', '.sta', '.dat')}
    text = '\n'.join(rows.values()).upper()
    completed = 'ANALYSIS COMPLETED' in text or 'COMPLETED SUCCESSFULLY' in text
    aborted = any(k in text for k in ('ANALYSIS HAS BEEN TERMINATED', 'ANALYSIS ABORTED', 'ABAQUS/STANDARD ANALYSIS EXITED WITH AN ERROR', 'ABAQUS/EXPLICIT ANALYSIS EXITED WITH AN ERROR'))
    input_errors = re.findall(r'^.*\*\*\*ERROR.*$', rows['.dat'], flags=re.I | re.M)
    return dict(completion_marker=completed, abort_marker=aborted, dat_error_lines=input_errors[-20:],
        numerical_markers={k: k in text for k in ('TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM',
        'TOO MANY INCREMENTS', 'NEGATIVE EIGENVALUES', 'EXCESSIVE DISTORTION', 'LICENSE')},
        note='Markers are evidence, not a general-purpose causal diagnosis; launcher rc0 is not a peak')


class Backend:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or BASE / 'assets' / 'local_config.json').resolve()
        self.config = read_json(self.config_path)
        self.launcher = Path(self.config['abaqus_launcher']).resolve()
        self.registry = Path(self.config['registry_dir']).resolve()
        self.roots = [Path(p).resolve() for p in self.config['allowed_roots']]
        if not self.roots:
            raise AgentError('At least one explicit allowed root is required')
        self.allowed(self.registry)
        for key in ('max_total_cpus', 'max_concurrent', 'max_total_memory_mb'):
            if int(self.config[key]) < 1:
                raise AgentError('Positive capacity required: ' + key)

    def allowed(self, path, must_exist=False):
        raw = Path(path)
        if not raw.is_absolute():
            raise AgentError('Absolute paths required: ' + str(path))
        path = raw.resolve()
        if not any(path == root or root in path.parents for root in self.roots):
            raise AgentError('Path outside configured roots: ' + str(path))
        if must_exist and not path.exists():
            raise AgentError('Missing path: ' + str(path))
        return path

    @contextlib.contextmanager
    def lock(self):
        self.registry.mkdir(parents=True, exist_ok=True)
        path = self.registry / 'submission.lock'
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AgentError('Registry lock exists; do not auto-delete. Inspect owner/crash before manual recovery')
        try:
            os.write(fd, json.dumps({'pid': os.getpid(), 'creation': process_identity(os.getpid()), 'time': now()}).encode())
            os.close(fd)
            yield
        finally:
            path.unlink(missing_ok=True)

    def records(self):
        return [read_json(p) for p in sorted(self.registry.glob('job-*.json'))]

    def doctor(self, query_release=False):
        hardware = powershell_json("$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor; [pscustomobject]@{physical_cores=($p|Measure-Object NumberOfCores -Sum).Sum;logical_processors=$c.NumberOfLogicalProcessors;ram_bytes=$c.TotalPhysicalMemory;last_boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')}|ConvertTo-Json -Compress")
        disks = []
        for raw in self.config.get('scratch_roots', []):
            path = Path(raw).resolve()
            existing = path
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            try:
                usage = shutil.disk_usage(existing)
                disks.append({'path': str(path), 'free_bytes': usage.free, 'total_bytes': usage.total})
            except OSError as exc:
                disks.append({'path': str(path), 'error': str(exc)})
        result = dict(schema_version=SCHEMA_VERSION, host='Windows' if os.name == 'nt' else os.name,
            launcher=str(self.launcher), launcher_exists=self.launcher.is_file(), python=sys.executable,
            hardware=hardware, solver_processes=process_snapshot() if os.name == 'nt' else [],
            scratch_disks=disks, registry=str(self.registry), configured_limits={k: self.config[k] for k in
            ('max_total_cpus', 'max_concurrent', 'max_total_memory_mb')},
            license_availability='UNVERIFIED: release/ODB access does not prove analysis license checkout',
            subroutine_tools=[dict(path=p, exists=Path(p).is_file()) for p in self.config.get('compiler_candidates', [])],
            no_solver_submitted=True)
        if query_release:
            if not self.launcher.is_file():
                raise AgentError('Abaqus launcher missing')
            self.registry.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(windows_command([self.launcher, 'information=release']), cwd=self.registry,
                capture_output=True, timeout=30, creationflags=hidden_flags())
            result['release_query'] = dict(returncode=completed.returncode,
                stdout=completed.stdout.decode('utf-8', errors='replace')[-5000:],
                stderr=completed.stderr.decode('utf-8', errors='replace')[-2000:])
        return result

    def input_dependencies(self, inp, extras):
        found, visiting = {}, set()
        def visit(path):
            path = self.allowed(path, True)
            if path in visiting:
                raise AgentError('Cyclic *INCLUDE: ' + str(path))
            if str(path) in found:
                return
            visiting.add(path)
            found[str(path)] = sha(path)
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            for line in text.splitlines():
                if re.match(r'^\s*\*INCLUDE\s*(?:,|$)', line, flags=re.I):
                    match = re.search(r'(?:^|,)\s*INPUT\s*=\s*("[^"]+"|\'[^\']+\'|[^,]+)', line, re.I)
                    if not match:
                        raise AgentError('Unresolved include line: ' + line)
                    visit(path.parent / match.group(1).strip().strip('\"\''))
            visiting.remove(path)
        visit(inp)
        for path in extras:
            path = self.allowed(path, True)
            found[str(path)] = sha(path)
        return found

    def inspect_restart(self, directory, job, solver='standard'):
        directory = self.allowed(directory, True)
        if not JOB_NAME.fullmatch(job):
            raise AgentError('Invalid job name')
        if solver not in ('standard', 'explicit'):
            raise AgentError('Invalid solver')
        exts = ['.res', '.mdl', '.stt', '.prt', '.odb']
        if solver == 'explicit':
            exts += ['.abq', '.pac', '.sel']
        rows = [dict(path=str(directory / (job + ext)), exists=(directory / (job + ext)).is_file(),
            bytes=(directory / (job + ext)).stat().st_size if (directory / (job + ext)).is_file() else 0) for ext in exts]
        return dict(files=rows, restart_files_present=all(r['exists'] and r['bytes'] for r in rows),
            restart_state_verified=False, restart_increment=None, restart_displacement=None,
            last_converged_RF=None, note='File inventory only. Binary restart readability/version/state and increment require solver-specific evidence; ODB alone is insufficient')

    def capacity(self, request):
        records = self.records()
        active = []
        for row in records:
            if row.get('finished'):
                continue
            if process_identity(row.get('worker_pid')) == row.get('worker_identity') and row.get('worker_pid'):
                active.append(row)
            elif process_identity(row.get('launcher_pid')) == row.get('launcher_identity') and row.get('launcher_pid'):
                active.append(row)
            elif row.get('state') in ('RESERVED', 'STARTING') and time.time() - row.get('submitted_epoch', 0) < 30:
                active.append(row)
        processes = process_snapshot() if os.name == 'nt' else []
        unknown = []
        for process in processes:
            command = process.get('CommandLine') or ''
            if not any(re.search(r'(?i)(?:^|[\\/\s="\'])' + re.escape(row['request']['job']) + r'(?=[.\s"\'=]|$)', command) for row in active):
                unknown.append(process)
        if unknown:
            raise AgentError('Unmanaged or unidentifiable existing solver jobs detected; no resource assumption or takeover allowed')
        used_cpu = sum(row['request']['cpus'] for row in active)
        used_mem = sum(row['request']['memory_mb'] for row in active)
        if used_cpu + request['cpus'] > self.config['max_total_cpus']:
            raise AgentError('CPU capacity exceeded')
        if used_mem + request['memory_mb'] > self.config['max_total_memory_mb']:
            raise AgentError('Memory reservation exceeded')
        if len(active) >= self.config['max_concurrent']:
            raise AgentError('Concurrency capacity reached')
        return dict(active_jobs=len(active), used_cpus=used_cpu, reserved_memory_mb=used_mem)

    def plan(self, raw):
        allowed_keys = {'job','input','workdir','scratch','solver','cpus','memory_mb','operation','user_subroutine',
            'additional_files','oldjob','restart_state_verified','restart_evidence','walltime_limit_seconds',
            'terminate_at_walltime','stop_after_confirmed_peak','probe','probe_interval_seconds'}
        if set(raw) - allowed_keys:
            raise AgentError('Unknown job request keys: ' + ', '.join(sorted(set(raw) - allowed_keys)))
        job = raw['job']
        if not JOB_NAME.fullmatch(job):
            raise AgentError('job must start with a letter; ASCII letters/digits/_/-; at most 64 chars')
        inp = self.allowed(raw['input'], True)
        work = self.allowed(raw['workdir'], True)
        scratch = self.allowed(raw['scratch'])
        if work in self.roots or work == self.registry or scratch in self.roots or scratch == self.registry or scratch == work:
            raise AgentError('Job-specific work/scratch paths required; not a workspace root or registry')
        if inp.parent != work or inp.suffix.lower() != '.inp':
            raise AgentError('Input must be an .inp in the explicit workdir; copy/build it there under user authority')
        scratch_roots = [Path(p).resolve() for p in self.config['scratch_roots']]
        if not any(root in scratch.parents for root in scratch_roots):
            raise AgentError('scratch must be a unique child of a configured scratch root')
        solver = raw.get('solver', 'standard').lower()
        if solver not in ('standard', 'explicit'):
            raise AgentError('solver must be standard or explicit')
        operation = raw.get('operation', 'analysis')
        if operation not in ('analysis', 'datacheck'):
            raise AgentError('operation must be analysis or datacheck')
        for key in ('cpus', 'memory_mb', 'walltime_limit_seconds', 'probe_interval_seconds'):
            if key in raw and (not isinstance(raw[key], int) or isinstance(raw[key], bool)):
                raise AgentError(key + ' must be a JSON integer, not boolean/float/string')
        req = dict(job=job, input=str(inp), workdir=str(work), scratch=str(scratch), solver=solver, operation=operation,
            cpus=int(raw.get('cpus', 2)), memory_mb=int(raw.get('memory_mb', 2048)),
            additional_files=raw.get('additional_files', []),
            walltime_limit_seconds=int(raw.get('walltime_limit_seconds', 5400)),
            terminate_at_walltime=raw.get('terminate_at_walltime', False),
            stop_after_confirmed_peak=raw.get('stop_after_confirmed_peak', False),
            probe_interval_seconds=int(raw.get('probe_interval_seconds', 60)))
        if isinstance(raw.get('cpus'), bool) or not 1 <= req['cpus'] <= self.config['max_total_cpus']:
            raise AgentError('Invalid cpus')
        if req['memory_mb'] < 256 or req['memory_mb'] > self.config['max_total_memory_mb']:
            raise AgentError('Invalid memory_mb')
        if req['walltime_limit_seconds'] < 30 or req['probe_interval_seconds'] < 10:
            raise AgentError('Walltime >=30s and probe interval >=10s required')
        for name in ('terminate_at_walltime', 'stop_after_confirmed_peak'):
            if not isinstance(req[name], bool):
                raise AgentError(name + ' must be boolean')
        collisions = list(work.glob(job + '.*'))
        protected = [p for p in collisions if p.suffix.lower() != '.inp']
        if protected:
            raise AgentError('Existing job output/lock found; no overwrite: ' + ', '.join(p.name for p in protected))
        if any(row['request']['job'].lower() == job.lower() for row in self.records()):
            raise AgentError('Managed job identity already exists; use a new attempt name, never overwrite')
        if any(not row.get('finished') and Path(row['request']['scratch']).resolve() == scratch for row in self.records()):
            raise AgentError('scratch already assigned to another managed job')
        if scratch.exists() and any(scratch.iterdir()):
            raise AgentError('scratch must be empty; do not delete or repurpose existing scratch')
        extras = list(req['additional_files'])
        local_env = work / 'abaqus_v6.env'
        if local_env.is_file():
            extras.append(str(local_env))
        if raw.get('user_subroutine'):
            user = self.allowed(raw['user_subroutine'], True)
            if user.suffix.lower() not in ('.f', '.for', '.f90', '.c', '.cpp', '.obj', '.dll'):
                raise AgentError('Unsupported user subroutine extension')
            req['user_subroutine'] = str(user)
            extras.append(str(user))
        dependencies = self.input_dependencies(inp, extras)
        deck = '\n'.join(Path(p).read_text(encoding='utf-8-sig', errors='replace') for p in dependencies if Path(p).suffix.lower() not in ('.dll', '.obj', '.res', '.mdl', '.stt', '.prt', '.odb', '.abq', '.pac', '.sel'))
        has_explicit = bool(re.search(r'^\s*\*DYNAMIC\s*,[^\n]*EXPLICIT', deck, re.I | re.M))
        if (solver == 'explicit') != has_explicit:
            raise AgentError('solver metadata does not match *DYNAMIC, EXPLICIT in audited input/includes')
        is_restart = bool(re.search(r'^\s*\*RESTART\s*,[^\n]*\bREAD\b', deck, re.I | re.M))
        if bool(raw.get('oldjob')) != is_restart:
            raise AgentError('*RESTART, READ and oldjob must be supplied together')
        if raw.get('oldjob'):
            old = self.allowed(raw['oldjob'])
            audit = self.inspect_restart(str(old.parent), old.name, solver)
            if not audit['restart_files_present'] or raw.get('restart_state_verified') is not True or not raw.get('restart_evidence'):
                raise AgentError('Restart requires files plus independently verified state/increment evidence')
            req.update(oldjob=str(old), restart_state_verified=True, restart_evidence=raw['restart_evidence'])
            for item in audit['files']:
                dependencies[item['path']] = sha(item['path'])
        if raw.get('probe'):
            if not isinstance(raw['probe'], dict):
                raise AgentError('probe must be an object')
            req['probe'] = dict(raw['probe'])
            req['probe'].update(odb=str(work / (job + '.odb')), mode='peak', solver=solver)
            if not req['probe'].get('step') or not req['probe'].get('history_regions'):
                raise AgentError('A live peak probe needs explicit step/history_regions')
        if req['stop_after_confirmed_peak'] and 'probe' not in req:
            raise AgentError('Peak-stop requires an explicit ODB probe contract')
        ancestor = scratch
        while not ancestor.exists():
            ancestor = ancestor.parent
        free = min(shutil.disk_usage(work).free, shutil.disk_usage(ancestor).free)
        if free < int(self.config.get('min_free_disk_bytes', 8 * 1024**3)):
            raise AgentError('Free disk below configured protective floor')
        args = [str(self.launcher), 'job=' + job, 'input=' + str(inp), 'cpus=' + str(req['cpus']),
                'memory=' + str(req['memory_mb']) + 'mb', 'scratch=' + str(scratch), 'interactive', 'ask_delete=OFF']
        if req.get('user_subroutine'):
            args.append('user=' + req['user_subroutine'])
        if req.get('oldjob'):
            args.append('oldjob=' + req['oldjob'])
        if solver == 'explicit':
            args += ['double=both', 'parallel=domain', 'domains=' + str(req['cpus'])]
        if operation == 'datacheck':
            args.append('datacheck')
        for arg in args:
            safe_argument(arg)
        if not self.launcher.is_file():
            raise AgentError('Missing Abaqus launcher')
        capacity = self.capacity(req)
        sealed = dict(schema_version=SCHEMA_VERSION, request=req, dependencies=dependencies,
            launcher_sha256=sha(self.launcher), config_sha256=sha(self.config_path),
            runner_sha256=sha(Path(__file__)), worker_sha256=sha(BASE / 'scripts' / 'job_worker.py'),
            probe_sha256=sha(BASE / 'scripts' / 'odb_probe.py'), history_sha256=sha(BASE / 'scripts' / 'history_peak.py'))
        return dict(sealed=sealed, plan_token=digest_json(sealed), command_args=args,
                    command_preview=windows_command(args), capacity=capacity,
                    no_solver_submitted=True, authorization_required=True,
                    input_audit_limit='*INCLUDE plus explicit additional_files; external FILE= dependencies and Fortran INCLUDE files must be listed manually',
                    notice='Approval covers exact hashes, CPUs, memory, timeout and optional peak-stop. No automatic recovery')

    def submit(self, request, plan_token, authorize=False, authorization_note=''):
        if authorize is not True or not authorization_note.strip():
            raise AgentError('Explicit human authorization and a note quoting its scope are required')
        if os.name != 'nt':
            raise AgentError('Analysis submission supported only on Windows')
        with self.lock():
            plan = self.plan(request)
            if plan_token != plan['plan_token']:
                raise AgentError('Plan changed or token incorrect; re-plan and verify scope')
            identity = uuid.uuid4().hex
            path = self.registry / ('job-' + identity + '.json')
            row = dict(id=identity, schema_version=SCHEMA_VERSION, state='RESERVED', finished=False,
                request=plan['sealed']['request'], sealed=plan['sealed'], command_args=plan['command_args'],
                plan_token=plan_token, authorization_note=authorization_note,
                submitted_at=now(), submitted_epoch=time.time(), config_path=str(self.config_path))
            atomic_json(path, row)
            log_path = self.registry / ('worker-' + identity + '.log')
            try:
                with log_path.open('xb') as stream:
                    subprocess.Popen([sys.executable, str(BASE / 'scripts' / 'job_worker.py'), str(path)],
                        cwd=self.registry, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                        creationflags=hidden_flags(True), close_fds=True)
            except Exception as exc:
                row.update(state='WORKER_START_FAILED', finished=True, error=str(exc))
                atomic_json(path, row)
                raise
        return dict(job_id=identity, state='RESERVED', record=str(path),
            note='Asynchronous worker created; inspect job status. This is not evidence of solver success')

    def record_path(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise AgentError('Managed job_id must be the returned 32-character id')
        path = self.registry / ('job-' + job_id + '.json')
        if not path.is_file():
            raise AgentError('Unknown managed job id')
        return path

    def status(self, job_id):
        row = read_json(self.record_path(job_id))
        req = row['request']
        worker_alive = bool(row.get('worker_pid')) and process_identity(row.get('worker_pid')) == row.get('worker_identity')
        launcher_alive = bool(row.get('launcher_pid')) and process_identity(row.get('launcher_pid')) == row.get('launcher_identity')
        state = row['state']
        if not row.get('finished') and not worker_alive:
            state = 'ORPHANED_LAUNCHER' if launcher_alive else 'STALE_RECORD_NEEDS_RECONCILIATION'
            if time.time() - row['submitted_epoch'] < 10 and not row.get('worker_pid'):
                state = 'WORKER_STARTING'
        return dict(job_id=job_id, observed_state=state, recorded_state=row['state'],
            worker_alive=worker_alive, launcher_alive=launcher_alive, record=row,
            terminal=terminal_evidence(req['workdir'], req['job']),
            files=[dict(path=str(p), bytes=p.stat().st_size, modified_epoch=p.stat().st_mtime)
                   for p in sorted(Path(req['workdir']).glob(req['job'] + '.*')) if p.is_file()],
            no_automatic_restart=True)

    def terminate(self, job_id, authorize=False, authorization_note=''):
        if authorize is not True or not authorization_note.strip():
            raise AgentError('Explicit authorization to stop this exact managed job required')
        status = self.status(job_id)
        row = status['record']
        if not status['launcher_alive']:
            raise AgentError('No matching live launcher identity; refusing blind terminate')
        req = row['request']
        args = [str(self.launcher), 'terminate', 'job=' + req['job']]
        completed = subprocess.run(windows_command(args), cwd=req['workdir'], capture_output=True,
            timeout=20, creationflags=hidden_flags())
        return dict(job_id=job_id, stop_requested=True, returncode=completed.returncode,
            authorization_note=authorization_note,
            stdout=completed.stdout.decode(errors='replace')[-1500:],
            note='Requested Abaqus termination only; no taskkill, no file deletion, no assurance it already stopped')

    def probe_odb(self, request):
        request = dict(request)
        request['odb'] = str(self.allowed(request['odb'], True))
        self.registry.mkdir(parents=True, exist_ok=True)
        name = 'probe-' + uuid.uuid4().hex
        source, dest = self.registry / (name + '-request.json'), self.registry / (name + '-result.json')
        atomic_json(source, request)
        args = [str(self.launcher), 'python', str(BASE / 'scripts' / 'odb_probe.py'), str(source), str(dest)]
        completed = subprocess.run(windows_command(args), cwd=self.registry, capture_output=True,
            timeout=int(self.config.get('probe_timeout_seconds', 90)), creationflags=hidden_flags())
        if not dest.is_file():
            raise AgentError('Probe did not create output: ' + completed.stderr.decode(errors='replace')[-1500:])
        result = read_json(dest)
        if completed.returncode or result.get('error'):
            raise AgentError('ODB probe failed (not a physical-model conclusion): ' + str(result.get('error', completed.returncode)))
        result.update(evidence_path=str(dest), launcher_returncode=completed.returncode)
        # Stable-shape evidence is not automatically final or authorized for classification.
        if result.get('peak_status') == 'PEAK_STABLE_PROVISIONAL':
            result['usable_peak'] = False
            result['usage_note'] = 'Stable provisional shape only; agent must verify terminal/current state and an explicit provisional-use policy'
        return result


def main():
    parser = argparse.ArgumentParser(description='Safe local Abaqus JSON API; use doctor/plan before submit')
    parser.add_argument('--config', default=str(BASE / 'assets' / 'local_config.json'))
    sub = parser.add_subparsers(dest='action', required=True)
    d = sub.add_parser('doctor'); d.add_argument('--query-release', action='store_true')
    p = sub.add_parser('plan'); p.add_argument('--request', required=True)
    s = sub.add_parser('submit'); s.add_argument('--request', required=True); s.add_argument('--plan-token', required=True)
    s.add_argument('--authorize', action='store_true'); s.add_argument('--authorization-note', default='')
    j = sub.add_parser('status'); j.add_argument('--job-id', required=True)
    t = sub.add_parser('terminate'); t.add_argument('--job-id', required=True); t.add_argument('--authorize', action='store_true'); t.add_argument('--authorization-note', default='')
    r = sub.add_parser('restart-audit'); r.add_argument('--directory', required=True); r.add_argument('--job', required=True); r.add_argument('--solver', choices=('standard', 'explicit'), default='standard')
    o = sub.add_parser('probe'); o.add_argument('--request', required=True)
    args = parser.parse_args()
    try:
        backend = Backend(args.config)
        if args.action == 'doctor': result = backend.doctor(args.query_release)
        elif args.action == 'plan': result = backend.plan(read_json(args.request))
        elif args.action == 'submit': result = backend.submit(read_json(args.request), args.plan_token, args.authorize, args.authorization_note)
        elif args.action == 'status': result = backend.status(args.job_id)
        elif args.action == 'terminate': result = backend.terminate(args.job_id, args.authorize, args.authorization_note)
        elif args.action == 'restart-audit': result = backend.inspect_restart(args.directory, args.job, args.solver)
        else: result = backend.probe_odb(read_json(args.request))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'error_type': type(exc).__name__, 'no_implicit_retry': True}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
