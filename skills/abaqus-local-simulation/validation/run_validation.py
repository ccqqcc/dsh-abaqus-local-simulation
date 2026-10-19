"""Repeatable validation; version queries/ODB reads only, NEVER analysis submission."""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
from abaqus_agent import Backend, sha, atomic_json


def mcp_probe(config, request):
    messages = [
        {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'validation-stdio-client','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/list'},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'abaqus_probe_odb','arguments':{'request':request}}}
    ]
    result = subprocess.run([sys.executable, str(BASE/'scripts'/'mcp_server.py'), '--config', str(config)],
        input=''.join(json.dumps(row) + '\n' for row in messages), text=True, encoding='utf-8',
        capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr)
    responses = [json.loads(row) for row in result.stdout.splitlines()]
    tool = responses[-1]['result']
    if tool.get('isError'):
        raise RuntimeError(tool['content'][0]['text'])
    return {'responses':responses, 'result':json.loads(tool['content'][0]['text']), 'stderr':result.stderr}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(BASE/'assets'/'local_config.json'))
    parser.add_argument('--real-odb', action='append', default=[])
    parser.add_argument('--step', default='COMPRESSION')
    parser.add_argument('--history-region', default='Node PART-1-1.9712')
    parser.add_argument('--axis', default='X')
    parser.add_argument('--solver', choices=('standard','explicit'), default='explicit')
    args = parser.parse_args()
    output = {'time_utc':datetime.now(timezone.utc).isoformat(), 'analysis_jobs_submitted':0,
              'analysis_jobs_terminated':0, 'old_model_files_modified':False}
    suite = unittest.defaultTestLoader.discover(str(BASE/'tests'))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output['unit_tests'] = {'run':result.testsRun, 'failures':len(result.failures),
                           'errors':len(result.errors), 'skipped':len(result.skipped), 'log':stream.getvalue()}
    if not result.wasSuccessful():
        raise RuntimeError(stream.getvalue())
    backend = Backend(args.config)
    output['doctor'] = backend.doctor(query_release=True)
    output['real_odb_tests'] = []
    for i, path in enumerate(args.real_odb):
        original_hash = sha(path)
        inventory = backend.probe_odb({'odb':path,'mode':'inventory'})
        request = {'odb':path,'mode':'peak','step':args.step,'history_regions':[args.history_region],
                   'axis':args.axis,'solver':args.solver,'energy_gates':{'ALLKE':.05,'ALLAE':.05},
                   'fields':['DAMAGEC','PEEQ','CPRESS','CSDMG','CSQUADSCRT','CSHEARMAG','CSLIP1','CSLIP2','CSLIPEQ']}
        if i == 0:
            response = mcp_probe(args.config, request)
            peak = response['result']
            output['real_mcp_protocol'] = response['responses']
            transport = 'MCP stdio with actual Abaqus Python child'
        else:
            peak = backend.probe_odb(request)
            transport = 'shared CLI/backend actual Abaqus Python child'
        current_hash = sha(path)
        if current_hash != original_hash:
            raise RuntimeError('Read-only invariant failed for ' + path)
        if not peak.get('strict_confirmed') or not peak.get('usable_peak') or not peak.get('energy_admissible'):
            raise RuntimeError('Real ODB quality not accepted: ' + json.dumps(peak))
        output['real_odb_tests'].append(dict(path=path, sha256_before=original_hash, sha256_after=current_hash,
            unchanged=True, transport=transport, inventory=inventory, peak=peak))
    output['limitations'] = [
        'Submission and authorization guards tested with mocks; NO new FE job or datacheck executed',
        'NO new UMAT/UINTER compilation or binary restart continuation executed',
        'Real solver license availability not proved by version or ODB reading',
        'MCP protocol validated with an independent stdio client, not inside WorkBuddy UI',
        'No automatic OS startup/reboot resume, model creation, parameter calibration or queue takeover'
    ]
    output['scripts_sha256'] = {str(path.relative_to(BASE)):sha(path) for path in sorted((BASE/'scripts').glob('*.py'))}
    dest = BASE/'validation'/'RESULTS.json'
    atomic_json(dest, output)
    print(json.dumps({'tests':output['unit_tests']['run'],'failures':0,'real_odb_count':len(output['real_odb_tests']),
        'read_only_unchanged':all(row['unchanged'] for row in output['real_odb_tests']),
        'peaks':[{'RF':row['peak']['RF_peak'],'status':row['peak']['peak_status'], 'energy':row['peak']['energy_ratios']} for row in output['real_odb_tests']],
        'output':str(dest),'analysis_jobs_submitted':0},ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
