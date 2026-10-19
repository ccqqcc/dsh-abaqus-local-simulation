"""Read-only Abaqus Python 2.7/3 adapter. Invoke with request.json output.json."""
from __future__ import print_function, division
import bisect
import json
import math
import os
import sys
import traceback
from history_peak import evaluate, validate_series, finite


def native_path(path):
    # Abaqus 2023 Python2 openOdb rejects unicode on this host; encode filesystem path.
    if sys.version_info[0] == 2 and isinstance(path, unicode):
        return path.encode(sys.getfilesystemencoding() or 'mbcs')
    return path


def sample(series, time):
    if not series:
        return None
    times = [p[0] for p in series]
    i = bisect.bisect_left(times, time)
    if i < len(times) and times[i] == time:
        return series[i][1]
    if i == 0 or i == len(times):
        return None  # Do not extrapolate missing energy output.
    a, b = series[i - 1], series[i]
    return a[1] + (b[1] - a[1]) * (time - a[0]) / (b[0] - a[0])


def value_magnitude(value):
    try:
        data = value.data
    except Exception:
        data = value.dataDouble
    try:
        return float(data)
    except (TypeError, ValueError):
        return math.sqrt(sum(float(x) ** 2 for x in data))


def p95(values):
    rows = sorted(values)
    x = .95 * (len(rows) - 1)
    lo, hi = int(math.floor(x)), int(math.ceil(x))
    return rows[lo] + (rows[hi] - rows[lo]) * (x - lo)


def probe(request):
    from odbAccess import openOdb
    odb = openOdb(path=native_path(request['odb']), readOnly=True)
    try:
        inventory = dict(odb=request['odb'], read_only=True, steps=[])
        for name, step in odb.steps.items():
            inventory['steps'].append(dict(name=name, frames=len(step.frames),
                history_regions=[dict(name=n, outputs=sorted(r.historyOutputs.keys()))
                                 for n, r in step.historyRegions.items()],
                latest_field_keys=sorted(step.frames[-1].fieldOutputs.keys()) if step.frames else []))
        if request.get('mode', 'inventory') == 'inventory':
            return inventory
        if request.get('mode') != 'peak':
            raise ValueError('mode must be inventory or peak')
        # Abaqus 2023 repositories, not only openOdb paths, reject unicode keys.
        step = odb.steps[native_path(request['step'])]
        names = [native_path(name) for name in request['history_regions']]
        if not names or len(set(names)) != len(names):
            raise ValueError('Explicit unique history_regions required; no heuristic RP selection')
        axis = request['axis'].upper()
        if axis not in ('X', 'Y', 'Z'):
            raise ValueError('axis must be X, Y, Z')
        rk, uk = 'RF' + str('XYZ'.index(axis) + 1), 'U' + str('XYZ'.index(axis) + 1)
        forces, motions = [], []
        for name in names:
            region = step.historyRegions[name]
            forces.append(list(region.historyOutputs[rk].data))
            motions.append(list(region.historyOutputs[uk].data))
        # For multiple top-node histories: sum RF, but require all U/time records aligned.
        if len(names) == 1:
            rf, uu = forces[0], motions[0]
        else:
            all_series = forces + motions
            maps = [validate_series(row) for row in all_series]
            if any(bad or dup or unordered for m, bad, dup, unordered in maps):
                raise ValueError('INVALID_AGGREGATE_HISTORY')
            times = set(maps[0][0])
            if any(set(m) != times for m, bad, dup, unordered in maps):
                raise ValueError('UNPAIRED_AGGREGATE_HISTORY')
            rf, uu = [], []
            tolerance = float(request.get('rules', {}).get('displacement_tolerance', 1e-10))
            for t in sorted(times):
                uvals = [maps[i + len(names)][0][t] for i in range(len(names))]
                if max(uvals) - min(uvals) > tolerance:
                    raise ValueError('TOP_HISTORY_DISPLACEMENTS_DIFFER: use a verified loaded RP')
                rf.append((t, sum(maps[i][0][t] for i in range(len(names)))))
                uu.append((t, uvals[0]))
        # Reject force reversal; abs(sum RF), never sum(abs nodal RF).
        signs = set(1 if v > 0 else -1 for t, v in rf if finite(v) and abs(v) > 1e-9)
        result = evaluate([(t, abs(v)) for t, v in rf], [(t, abs(v)) for t, v in uu], request.get('rules'))
        result.update(odb=request['odb'], step=request['step'], axis=axis,
                      history_regions=names, force_aggregation='abs(sum signed RF)',
                      read_only=True, energy_ratios={}, field_summary={})
        reasons = []
        if len(signs) > 1:
            reasons.append('FORCE_SIGN_REVERSAL_UNSUPPORTED')
        finite_motion = [abs(v) for t, v in uu if finite(t) and finite(v)]
        motion_tol = float(request.get('rules', {}).get('displacement_tolerance', 1e-10))
        if any(finite_motion[i] < finite_motion[i-1] - motion_tol for i in range(1, len(finite_motion))):
            reasons.append('NONMONOTONIC_DISPLACEMENT_HISTORY_UNSUPPORTED')
        energy_regions = [n for n, r in step.historyRegions.items() if 'ALLIE' in r.historyOutputs]
        energy_name = request.get('energy_region')
        if energy_name is not None:
            energy_name = native_path(energy_name)
        if energy_name is None and len(energy_regions) == 1:
            energy_name = energy_regions[0]
        energies = {}
        if energy_name:
            region = step.historyRegions[energy_name]
            for key in ('ALLIE', 'ALLKE', 'ALLAE', 'ALLVD', 'ALLSD', 'ALLCD', 'ALLDMD', 'ETOTAL'):
                if key in region.historyOutputs:
                    series = list(region.historyOutputs[key].data)
                    m, bad, dup, unordered = validate_series(series)
                    if bad or dup or unordered:
                        reasons.append('INVALID_ENERGY_HISTORY_' + key)
                    energies[key] = sorted(m.items())
        result['energy_region'] = energy_name
        result['available_energy_regions'] = energy_regions
        result['energy_keys'] = sorted(energies)
        peak = result.get('candidate_RF')
        peak_time = result.get('candidate_time')
        ratio_maps = {}
        if peak and 'ALLIE' in energies:
            samples = [(t, v) for t, v in rf if finite(t) and finite(v) and abs(v) >= .2 * peak]
            for key in ('ALLKE', 'ALLAE', 'ALLVD', 'ALLSD', 'ALLCD'):
                if key not in energies:
                    continue
                values, missing = [], 0
                for t, v in samples:
                    a, b = sample(energies[key], t), sample(energies['ALLIE'], t)
                    if a is None or b is None or abs(b) <= 1e-9:
                        missing += 1
                    else:
                        values.append(abs(a) / abs(b))
                a, b = sample(energies[key], peak_time), sample(energies['ALLIE'], peak_time)
                metrics = dict(max=max(values) if values else None,
                    at_peak=abs(a) / abs(b) if a is not None and b is not None and abs(b) > 1e-9 else None,
                    evaluated_points=len(values), unavailable_points=missing,
                    scope='RF >= 20 percent candidate; includes postpeak; linear energy interpolation only')
                ratio_maps[key] = metrics
        result['energy_ratios'] = ratio_maps
        solver = request.get('solver', 'standard').lower()
        if solver not in ('standard', 'explicit'):
            raise ValueError('solver must be standard or explicit')
        gates = request.get('energy_gates', {})
        required = {'ALLKE': float(gates.get('ALLKE', .05))} if solver == 'explicit' else {}
        if 'ALLAE' in gates:
            required['ALLAE'] = float(gates['ALLAE'])
        if request.get('require_stabilization_energy'):
            required['ALLSD'] = float(gates.get('ALLSD', .01))
        for key, limit in required.items():
            metric = ratio_maps.get(key, {})
            if metric.get('max') is None or metric.get('unavailable_points', 0):
                reasons.append('ENERGY_EVIDENCE_MISSING_' + key)
            elif metric['max'] >= limit:
                reasons.append('ENERGY_GATE_FAILED_' + key)
        frame = min(step.frames, key=lambda f: abs(f.frameValue - peak_time)) if peak_time is not None and step.frames else None
        requested_fields = request.get('fields', ['DAMAGEC', 'DAMAGET', 'PEEQ', 'SDEG', 'QUADSCRT',
                                                'CSDMG', 'CSQUADSCRT', 'CPRESS', 'CSHEAR', 'CSLIP', 'COPEN', 'SDV'])
        result['field_requests_unavailable'] = list(requested_fields)
        if frame:
            result['peak_field_time'] = frame.frameValue
            result['peak_field_time_offset'] = abs(frame.frameValue - peak_time)
            found = set()
            for key, field in frame.fieldOutputs.items():
                matched = [prefix for prefix in requested_fields if key.upper() == prefix.upper() or key.upper().startswith(prefix.upper() + ' ') or (key.upper().startswith(prefix.upper()) and key[len(prefix):len(prefix)+1].isdigit())]
                if not matched:
                    continue
                found.update(matched)
                values = [value_magnitude(value) for value in field.values]
                good = [v for v in values if finite(v)]
                bad = len(values) - len(good)
                if bad:
                    reasons.append('NONFINITE_FIELD_' + key)
                result['field_summary'][key] = dict(count=len(values), nonfinite=bad,
                    mean=sum(good) / len(good) if good else None, min=min(good) if good else None,
                    max=max(good) if good else None, p95=p95(good) if good else None,
                    weighting='unweighted output point count; NOT area or volume weighted',
                    scalarization='scalar unchanged; vector/tensor Euclidean data norm')
            result['field_requests_unavailable'] = [k for k in requested_fields if k not in found]
        result['quality_reasons'] = reasons
        result['energy_admissible'] = not reasons
        result['area_fractions'] = 'NOT_COMPUTED_REQUIRES_GEOMETRIC_WEIGHTS'
        result['friction_contribution'] = 'NOT_SEPARATED_BY_THIS_GENERIC_PROBE'
        result['provisional_requires_explicit_use_policy'] = True
        if reasons:
            result.update(peak_status='INVALID_QUALITY', usable_peak=False, RF_peak=None, U_peak=None)
        return result
    finally:
        odb.close()


def main():
    dest = sys.argv[2]
    code = 0
    try:
        with open(sys.argv[1], 'rb') as stream:
            request = json.loads(stream.read().decode('utf-8-sig'))
        result = probe(request)
    except Exception as exc:
        code = 2
        result = dict(error=str(exc), traceback=traceback.format_exc(), peak_status='ODB_PROBE_ERROR', usable_peak=False, read_only=True)
    with open(dest, 'w') as stream:
        json.dump(result, stream, ensure_ascii=True, allow_nan=False, indent=2)
    print(json.dumps({'status': result.get('peak_status', 'INVENTORY'), 'output': dest, 'error': result.get('error')}))
    return code


if __name__ == '__main__':
    sys.exit(main())
