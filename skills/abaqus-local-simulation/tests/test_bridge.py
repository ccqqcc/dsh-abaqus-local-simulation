"""Behavioral tests: never start Abaqus analyses; mocked submit uses only temp files."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import abaqus_agent as bridge
from history_peak import evaluate
import mcp_server
import job_worker


class Peaks(unittest.TestCase):
    def curve(self, values, displacements=None, times=None):
        times = times or list(range(len(values)))
        displacements = displacements or list(range(len(values)))
        return evaluate(list(zip(times, values)), list(zip(times, displacements)))

    def test_endpoint_never_peak(self):
        result = self.curve([0, 1, 2, 3, 4])
        self.assertEqual(result['peak_status'], 'NO_CONFIRMED_PEAK')
        self.assertIsNone(result['RF_peak'])
        self.assertEqual(result['candidate_RF'], 4)

    def test_strict_five_percent(self):
        result = self.curve([0, 80, 100, 99, 97, 94])
        self.assertEqual(result['peak_status'], 'PEAK_VALID')
        self.assertTrue(result['strict_confirmed'])

    def test_screen_two_not_strict(self):
        result = self.curve([0, 80, 100, 99, 98.5, 97])
        self.assertEqual(result['peak_status'], 'SCREEN_PEAK_VALID')
        self.assertFalse(result['strict_confirmed'])

    def test_stable_thirty(self):
        result = self.curve([0, 80, 100] + [100 - .02 * i for i in range(1, 36)])
        self.assertEqual(result['peak_status'], 'PEAK_STABLE_PROVISIONAL')
        self.assertEqual(result['RF_peak'], 100)

    def test_stable_needs_thirty(self):
        result = self.curve([0, 80, 100] + [99.99 - .02 * i for i in range(29)])
        self.assertEqual(result['peak_status'], 'NO_CONFIRMED_PEAK')

    def test_stable_needs_half_percent(self):
        result = self.curve([0, 80, 100] + [100 - .001 * i for i in range(1, 36)])
        self.assertEqual(result['peak_status'], 'NO_CONFIRMED_PEAK')

    def test_postpeak_response_not_peak_lower_bound(self):
        result = self.curve([0, 80, 100, 90, 70, 60])
        self.assertEqual(result['RF_peak'], 100)
        self.assertNotIn('peak_lower_bound', result)

    def test_higher_new_endpoint_invalidates_old_peak(self):
        result = self.curve([0, 80, 100, 90, 80, 101])
        self.assertIsNone(result['RF_peak'])
        self.assertEqual(result['candidate_RF'], 101)

    def test_no_displacement_progress(self):
        result = self.curve([0, 80, 100, 99, 97, 90], [0, 1, 2, 2, 2, 2])
        self.assertEqual(result['peak_status'], 'NO_CONFIRMED_PEAK')

    def test_duplicate_points_invalid(self):
        result = self.curve([0, 80, 100, 99, 97, 90], times=[0, 1, 2, 3, 3, 4])
        self.assertEqual(result['peak_status'], 'INVALID_HISTORY')

    def test_unpaired_points_invalid(self):
        result = evaluate([(0, 0), (1, 100), (2, 90)], [(0, 0), (1, 1), (3, 3)])
        self.assertEqual(result['peak_status'], 'INVALID_HISTORY')

    def test_nonfinite_rejected(self):
        result = self.curve([0, 80, 100, 99, 97, float('nan'), 90])
        self.assertEqual(result['peak_status'], 'INVALID_HISTORY')
        self.assertFalse(result['usable_peak'])

    def test_unordered_time_rejected(self):
        result = self.curve([0, 80, 100, 99, 97, 90], times=[0, 1, 2, 4, 3, 5])
        self.assertEqual(result['peak_status'], 'INVALID_HISTORY')

    def test_bad_rule_order_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([(0, 0)], [(0, 0)], {'screen_drop': .1, 'confirmed_drop': .05})


class Plans(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abaqus_bridge_test_')
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.scratch_root = self.root / 'scratch'; self.scratch_root.mkdir()
        self.launcher = self.root / 'abaqus.bat'; self.launcher.write_text('@echo off\n')
        self.inp = self.work / 'MODEL.inp'
        self.inp.write_text('*HEADING\nTEST FIXTURE ONLY\n*INCLUDE, INPUT=material.inc\n*STEP\n*STATIC\n.01,1,1e-6,.1\n*END STEP\n')
        (self.work / 'material.inc').write_text('*MATERIAL, NAME=TEST\n*ELASTIC\n1000,.2\n')
        self.config = self.root / 'config.json'
        bridge.atomic_json(self.config, dict(abaqus_launcher=str(self.launcher), allowed_roots=[str(self.root)],
            registry_dir=str(self.root / 'registry'), scratch_roots=[str(self.scratch_root)], max_total_cpus=8,
            max_concurrent=4, max_total_memory_mb=8192, min_free_disk_bytes=0, probe_timeout_seconds=30))
        self.backend = bridge.Backend(self.config)
        self.request = dict(job='TEST_X', input=str(self.inp), workdir=str(self.work),
            scratch=str(self.scratch_root / 'TEST_X'), solver='standard', cpus=2, memory_mb=1024)
        self.process_patch = patch('abaqus_agent.process_snapshot', return_value=[])
        self.process_patch.start()

    def tearDown(self):
        self.process_patch.stop()
        self.temp.cleanup()

    def test_plan_reads_only(self):
        before = set(self.root.rglob('*'))
        plan = self.backend.plan(self.request)
        self.assertEqual(before, set(self.root.rglob('*')))
        self.assertEqual(len(plan['sealed']['dependencies']), 2)
        self.assertEqual(len(plan['plan_token']), 64)

    def test_material_mutation_changes_token(self):
        a = self.backend.plan(self.request)['plan_token']
        (self.work / 'material.inc').write_text('*MATERIAL,NAME=OTHER\n')
        b = self.backend.plan(self.request)['plan_token']
        self.assertNotEqual(a, b)

    def test_odb_overwrite_rejected(self):
        (self.work / 'TEST_X.odb').write_bytes(b'legacy result')
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)

    def test_stale_lock_not_deleted(self):
        path = self.work / 'TEST_X.lck'; path.write_text('legacy lock')
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)
        self.assertTrue(path.exists())

    def test_scratch_nonempty_rejected(self):
        path = Path(self.request['scratch']); path.mkdir(); (path / 'unknown').write_text('keep')
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)
        self.assertEqual((path / 'unknown').read_text(), 'keep')

    def test_outside_roots_rejected(self):
        with self.assertRaises(bridge.AgentError): self.backend.allowed(self.root.parent / 'outside.inp')

    def test_relative_path_rejected(self):
        with self.assertRaises(bridge.AgentError): self.backend.allowed('relative.inp')

    def test_bad_job_shell_injection(self):
        for name in ['JOB & whoami', '../JOB', 'Z*', '0JOB', 'JOB%PATH%']:
            with self.subTest(name=name), self.assertRaises(bridge.AgentError):
                self.backend.plan(dict(self.request, job=name))

    def test_command_metacharacters_rejected(self):
        for value in ['path&bad', 'path|bad', 'path%PATH%', 'path\nwrong', 'a"b']:
            with self.subTest(value=value), self.assertRaises(bridge.AgentError): bridge.safe_argument(value)

    def test_all_windows_arguments_quoted(self):
        if os.name != 'nt': self.skipTest('Windows command form')
        command = bridge.windows_command(['C:/Program Files/Tool (x86)/abaqus.bat', 'job=TEST_X'])
        self.assertIn('"C:/Program Files/Tool (x86)/abaqus.bat" "job=TEST_X"', command)

    def test_cyclic_include_rejected(self):
        (self.work / 'material.inc').write_text('*INCLUDE, INPUT=MODEL.inp\n')
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)

    def test_include_escape_rejected(self):
        self.inp.write_text('*INCLUDE,INPUT=' + str(self.root.parent / 'outside.inc'))
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)

    def test_missing_include_rejected(self):
        self.inp.write_text('*INCLUDE,INPUT=missing.inc')
        with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)

    def test_explicit_solver_mismatch_rejected(self):
        with self.assertRaises(bridge.AgentError): self.backend.plan(dict(self.request, solver='explicit'))

    def test_explicit_command_precision_domains(self):
        self.inp.write_text('*STEP\n*DYNAMIC, EXPLICIT\n, .02\n*END STEP\n')
        plan = self.backend.plan(dict(self.request, solver='explicit', cpus=4))
        self.assertIn('double=both', plan['command_args'])
        self.assertIn('domains=4', plan['command_args'])

    def test_datacheck_supported_without_executing(self):
        plan = self.backend.plan(dict(self.request, operation='datacheck'))
        self.assertIn('datacheck', plan['command_args'])

    def test_bad_cpus_rejected(self):
        for count in [True, 2.5, '2', 0, 9]:
            with self.subTest(count=count), self.assertRaises(bridge.AgentError):
                self.backend.plan(dict(self.request, cpus=count))

    def test_unknown_live_solver_blocks(self):
        with patch('abaqus_agent.process_snapshot', return_value=[{'Name':'standard.exe','ProcessId':123,'CommandLine':'unknown original job'}]):
            with self.assertRaises(bridge.AgentError): self.backend.plan(self.request)

    def test_no_submit_without_authorization(self):
        plan = self.backend.plan(self.request)
        with patch('abaqus_agent.subprocess.Popen') as process:
            with self.assertRaises(bridge.AgentError): self.backend.submit(self.request, plan['plan_token'])
            process.assert_not_called()

    def test_changed_plan_never_launches(self):
        plan = self.backend.plan(self.request)
        self.inp.write_text(self.inp.read_text() + '**changed\n')
        with patch('abaqus_agent.subprocess.Popen') as process:
            with self.assertRaises(bridge.AgentError): self.backend.submit(self.request, plan['plan_token'], True, 'test mocked submit only')
            process.assert_not_called()

    def test_mocked_submit_writes_only_own_registry(self):
        if os.name != 'nt': self.skipTest('Windows submit')
        plan = self.backend.plan(self.request)
        with patch('abaqus_agent.subprocess.Popen') as process:
            result = self.backend.submit(self.request, plan['plan_token'], True, 'TEST MOCK ONLY NO ANALYSIS')
            process.assert_called_once()
        record = bridge.read_json(result['record'])
        self.assertEqual(record['state'], 'RESERVED')
        self.assertFalse((self.work / 'TEST_X.odb').exists())

    def test_restart_inventory_not_verification(self):
        for ext in ('.res','.mdl','.stt','.prt','.odb'): (self.work / ('OLD' + ext)).write_bytes(b'fixture')
        audit = self.backend.inspect_restart(str(self.work), 'OLD')
        self.assertTrue(audit['restart_files_present'])
        self.assertFalse(audit['restart_state_verified'])
        self.assertIsNone(audit['restart_increment'])

    def test_missing_restart_state_not_submitted(self):
        self.inp.write_text('*RESTART, READ\n')
        with self.assertRaises(bridge.AgentError): self.backend.plan(dict(self.request, oldjob=str(self.work / 'OLD')))

    def test_pid_reuse_not_running(self):
        self.backend.registry.mkdir()
        job_id = 'a' * 32
        bridge.atomic_json(self.backend.registry / ('job-' + job_id + '.json'),
            dict(id=job_id, request=self.request, state='RUNNING', finished=False,
                 worker_pid=100, worker_identity=1, launcher_pid=101, launcher_identity=2,
                 submitted_epoch=0))
        with patch('abaqus_agent.process_identity', return_value=999):
            status = self.backend.status(job_id)
        self.assertEqual(status['observed_state'], 'STALE_RECORD_NEEDS_RECONCILIATION')

    def test_unknown_job_cannot_be_terminated(self):
        with self.assertRaises(bridge.AgentError): self.backend.terminate('b' * 32, True, 'scope test')

    def test_probe_errors_are_not_success(self):
        odb = self.work / 'ORIGINAL.odb'; odb.write_bytes(b'not opened: mock')
        def fake_run(command, **kwargs):
            destination = sorted(self.backend.registry.glob('*-request.json'))[-1]
            output = destination.with_name(destination.name.replace('-request.json', '-result.json'))
            bridge.atomic_json(output, {'error':'unicode TypeError','peak_status':'ODB_PROBE_ERROR'})
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch('abaqus_agent.subprocess.run', side_effect=fake_run):
            with self.assertRaises(bridge.AgentError): self.backend.probe_odb({'odb': str(odb), 'mode':'inventory'})
        self.assertEqual(odb.read_bytes(), b'not opened: mock')

    def test_provisional_not_automatically_usable(self):
        odb = self.work / 'ORIGINAL.odb'; odb.write_bytes(b'mock')
        def fake_run(command, **kwargs):
            source = sorted(self.backend.registry.glob('*-request.json'))[-1]
            bridge.atomic_json(source.with_name(source.name.replace('-request.json','-result.json')),
                {'peak_status':'PEAK_STABLE_PROVISIONAL','usable_peak':True,'RF_peak':100})
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch('abaqus_agent.subprocess.run', side_effect=fake_run):
            result = self.backend.probe_odb({'odb':str(odb),'mode':'peak'})
        self.assertFalse(result['usable_peak'])

    def test_tools_reject_unknown_extra_arguments(self):
        with self.assertRaises(ValueError): mcp_server.validate_arguments('abaqus_doctor', {'shell':'whoami'})


class Workers(unittest.TestCase):
    def setUp(self):
        Plans.setUp(self)

    def tearDown(self):
        Plans.tearDown(self)

    def prepare(self, updates=None):
        self.request.update(updates or {})
        plan = self.backend.plan(self.request)
        path = self.backend.registry / ('job-' + 'c' * 32 + '.json')
        bridge.atomic_json(path, dict(id='c'*32, request=plan['sealed']['request'], sealed=plan['sealed'],
            state='RESERVED', finished=False, config_path=str(self.config), command_args=plan['command_args'],
            submitted_epoch=0))
        return path

    def fake_launcher_process(self):
        process = MagicMock(pid=os.getpid(), returncode=0)
        process.poll.side_effect = [None, 0]
        return process

    def test_current_windows_process_identity(self):
        self.assertIsNotNone(bridge.process_identity(os.getpid()))
        self.assertEqual(bridge.process_identity(os.getpid()), bridge.process_identity(os.getpid()))

    def test_mocked_worker_finishes_with_raw_marker(self):
        path = self.prepare()
        (self.work/'TEST_X.log').write_text('ANALYSIS COMPLETED\n')
        with patch('job_worker.subprocess.Popen', return_value=self.fake_launcher_process()), patch('job_worker.time.sleep'):
            row = job_worker.supervise(path)
        self.assertEqual(row['state'], 'COMPLETED')
        self.assertTrue(row['finished'])
        self.assertFalse((self.work/'TEST_X.odb').exists())

    def test_datacheck_error_not_masked_by_completed_launcher(self):
        path = self.prepare({'operation':'datacheck'})
        (self.work/'TEST_X.log').write_text('COMPLETED SUCCESSFULLY\n')
        (self.work/'TEST_X.dat').write_text('***ERROR: TEST INPUT INVALID\n')
        with patch('job_worker.subprocess.Popen', return_value=self.fake_launcher_process()), patch('job_worker.time.sleep'):
            row = job_worker.supervise(path)
        self.assertEqual(row['state'], 'INPUT_CHECK_FAILED')

    def test_input_changed_after_submit_blocks_worker(self):
        path = self.prepare()
        self.inp.write_text('*HEADING\nMUTATION\n')
        with patch('job_worker.subprocess.Popen') as process:
            with self.assertRaises(bridge.AgentError): job_worker.supervise(path)
            process.assert_not_called()

    def test_walltime_with_authority_requests_exact_job_only(self):
        path = self.prepare({'terminate_at_walltime':True})
        stopped = subprocess.CompletedProcess([], 0, b'job stopped mock', b'')
        with patch('job_worker.subprocess.Popen', return_value=self.fake_launcher_process()), patch('job_worker.time.sleep'), \
             patch('job_worker.time.time', side_effect=[0,6000,6001]), patch('job_worker.subprocess.run', return_value=stopped) as call:
            row = job_worker.supervise(path)
        self.assertEqual(row['stop_request']['reason'], 'AUTHORIZED_WALLTIME_LIMIT')
        self.assertIn('job=TEST_X', str(call.call_args))
        self.assertNotIn('taskkill', str(call.call_args))

    def test_walltime_without_authority_never_terminates(self):
        path = self.prepare({'terminate_at_walltime':False})
        with patch('job_worker.subprocess.Popen', return_value=self.fake_launcher_process()), patch('job_worker.time.sleep'), \
             patch('job_worker.time.time', side_effect=[0,6000,6001]), patch('job_worker.subprocess.run') as call:
            row = job_worker.supervise(path)
        call.assert_not_called()
        self.assertEqual(row['attention'], 'WALLTIME_LIMIT_REACHED_NO_TERMINATION_AUTHORITY')


class MCPProtocol(unittest.TestCase):
    def test_stdio_lifecycle_tools_and_guard(self):
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'independent-test-client','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'abaqus_submit_job','arguments':{'request':{},'plan_token':'bad','authorize':False,'authorization_note':''}}},
            {'jsonrpc':'2.0','id':4,'method':'ping'},
            {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'abaqus_doctor','arguments':{'shell':'not permitted'}}},
            {'jsonrpc':'2.0','id':6,'method':'resources/list'},
            {'jsonrpc':'2.0','id':7,'method':'resources/read','params':{'uri':'abaqus://handbook'}},
            {'jsonrpc':'2.0','id':8,'method':'resources/read','params':{'uri':'file:///C:/Windows/secret'}}
        ]
        process = subprocess.run([sys.executable, str(SCRIPTS / 'mcp_server.py')],
            input=''.join(json.dumps(m) + '\n' for m in messages), text=True, encoding='utf-8',
            capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        output = [json.loads(row) for row in process.stdout.splitlines()]
        self.assertEqual(len(output), 8)
        self.assertEqual(output[0]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(len(output[1]['result']['tools']), 7)
        self.assertTrue(output[2]['result']['isError'])
        self.assertEqual(output[3]['result'], {})
        self.assertTrue(output[4]['result']['isError'])
        self.assertEqual(len(output[5]['result']['resources']), 4)
        self.assertEqual(output[6]['result']['contents'][0]['mimeType'], 'text/markdown')
        self.assertGreater(len(output[6]['result']['contents'][0]['text']), 1000)
        self.assertIn('error', output[7])


if __name__ == '__main__':
    unittest.main(verbosity=2)
