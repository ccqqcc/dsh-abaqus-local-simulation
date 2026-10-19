"""Python 2.7/3 compatible monotonic RF/U evidence; no Abaqus imports."""
from __future__ import division
import math


def finite(value):
    return not math.isnan(float(value)) and not math.isinf(float(value))


def slope(x, y):
    if len(x) < 2:
        return 0.0
    mx, my = sum(x) / len(x), sum(y) / len(y)
    den = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / den if den else 0.0


def validate_series(series):
    bad, duplicate, nonincreasing = 0, 0, 0
    result = {}
    previous = None
    for t, value in series:
        if not finite(t) or not finite(value):
            bad += 1
            continue
        if t in result:
            duplicate += 1
        if previous is not None and t < previous:
            nonincreasing += 1
        result[t] = float(value)
        previous = t
    return result, bad, duplicate, nonincreasing


def evaluate(force_series, displacement_series, rules=None):
    """Values are axial force/displacement magnitudes supplied by the ODB adapter."""
    rules = rules or {}
    strict_drop = float(rules.get('confirmed_drop', 0.05))
    screen_drop = float(rules.get('screen_drop', 0.02))
    stable_drop = float(rules.get('stable_drop', 0.005))
    stable_points = int(rules.get('stable_points', 30))
    min_post = int(rules.get('minimum_postpeak_points', 3))
    tol = float(rules.get('displacement_tolerance', 1e-10))
    if not (0 < stable_drop <= screen_drop <= strict_drop < 1):
        raise ValueError('Require 0 < stable_drop <= screen_drop <= confirmed_drop < 1')
    if stable_points < 1 or min_post < 1 or tol < 0:
        raise ValueError('Invalid peak rule counts/tolerance')
    rf, nb1, du1, nt1 = validate_series(force_series)
    ud, nb2, du2, nt2 = validate_series(displacement_series)
    times = sorted(set(rf).intersection(ud))
    unpaired = len(set(rf).symmetric_difference(ud))
    result = dict(peak_status='NO_CONFIRMED_PEAK', usable_peak=False,
                  RF_peak=None, U_peak=None, candidate_RF=None, candidate_U=None,
                  nonfinite=nb1 + nb2, duplicate_points=du1 + du2,
                  unordered_points=nt1 + nt2, unexpected_unpaired=unpaired,
                  paired_points=len(times), strict_confirmed=False,
                  screen_confirmed=False, stable_supported=False)
    if not times:
        result['error'] = 'NO_PAIRED_HISTORY'
        return result
    force, disp = [rf[t] for t in times], [ud[t] for t in times]
    if min(force) < 0 or min(disp) < 0:
        raise ValueError('Adapter must supply magnitude histories, not signed values')
    pk = max(range(len(force)), key=lambda i: force[i])
    peak = force[pk]
    post = len(times) - pk - 1
    drop = (peak - min(force[pk + 1:])) / peak if post and peak > 0 else 0.0
    increasing = post > 0 and disp[-1] > disp[pk] + tol and all(
        disp[i] >= disp[i - 1] - tol for i in range(pk + 1, len(disp)))
    tail = pk + 1 + post // 2
    trend = slope(disp[tail:], force[tail:])
    clean = not (nb1 + nb2 + du1 + du2 + nt1 + nt2 + unpaired)
    strict = clean and peak > 0 and increasing and post >= min_post and drop >= strict_drop
    screen = clean and peak > 0 and increasing and post >= min_post and drop >= screen_drop
    stable = clean and peak > 0 and increasing and post >= stable_points and trend < 0 and drop >= stable_drop
    status = 'PEAK_VALID' if strict else ('SCREEN_PEAK_VALID' if screen else (
        'PEAK_STABLE_PROVISIONAL' if stable else 'NO_CONFIRMED_PEAK'))
    if not clean:
        status = 'INVALID_HISTORY'
    result.update(peak_status=status, usable_peak=strict or screen or stable,
                  RF_peak=peak if strict or screen or stable else None,
                  U_peak=disp[pk] if strict or screen or stable else None,
                  candidate_RF=peak, candidate_U=disp[pk], candidate_time=times[pk],
                  candidate_index=pk, postpeak_points=post, postpeak_drop=drop,
                  displacement_continues=increasing, postpeak_tail_slope=trend,
                  negative_tail_trend=trend < 0, strict_confirmed=strict,
                  screen_confirmed=screen, stable_supported=stable,
                  latest_time=times[-1], latest_RF=force[-1], latest_U=disp[-1])
    return result
