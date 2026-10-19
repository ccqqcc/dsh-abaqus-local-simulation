---
name: abaqus-local-simulation
description: Run and audit local Windows Abaqus/Standard or Explicit workflows from agents — INP dependency plans, authorized job submission, process-aware monitoring, restart evidence and read-only ODB peak extraction. Use for Abaqus simulation or its numerical/operational diagnosis, not generic CAD or unsupported material calibration.
whenToUse: Use when the user asks to submit, plan, monitor, diagnose or extract results from a local Abaqus FEA job on this Windows machine, or mentions abaqus.bat, INP decks, ODB peak/energy extraction, datacheck, or the abaqus-local-simulation skill.
---

# Abaqus local simulation (DSH plugin skill)

This skill bundles a Python CLI + local stdio MCP backend that drives the
**local, already-licensed** Abaqus 2023 install on this Windows host
(`D:\SIMULIA\Commands\abaqus.bat`). It does **not** install Abaqus, a solver
license, or a Fortran compiler, and it does **not** contact any cloud. The
backend is stdlib-only Python; on this machine it runs under
`D:\anaconda3\python.exe` (Python 3.13).

## Where the files live

This skill's `resourceBase` is the skill directory. Relative paths below resolve
against it:

- `scripts/abaqus_agent.py` — the CLI bridge (`doctor`, `plan`, `submit`, `status`, `terminate`, `restart-audit`, `probe`).
- `scripts/mcp_server.py` — local stdio MCP server (seven tools; same backend).
- `scripts/odb_probe.py`, `scripts/history_peak.py`, `scripts/job_worker.py` — ODB reader, peak classifier, per-job worker.
- `references/ABAQUS_CALLING_HANDBOOK.md` — full local workflow (read before building/submitting).
- `references/USER_SUBROUTINES.md`, `references/TROUBLESHOOTING.md`, `references/PROJECT_CONTRACT.md` — UMAT/UINTER, recovery, and this project's contract.
- `assets/local_config.json` — **machine config**: launcher, allowed roots, registry, scratch, CPU/RAM budget. Edit only for this host.
- `README.md` — install / CLI / MCP usage and verified capability scope.
- `validation/run_validation.py` — repeatable acceptance (unit tests + real read-only ODB reads; never submits an analysis).

## How to invoke it from DSH

Use the shell tool to run the Python bridge. Pick the matching absolute paths:

```powershell
$py      = 'D:\anaconda3\python.exe'
$skill   = '<this skill directory>'          # resolve resourceBase at runtime
$bridge  = Join-Path $skill 'scripts\abaqus_agent.py'
$config  = Join-Path $skill 'assets\local_config.json'
```

Then, for example:

```powershell
& $py $bridge --config $config doctor --query-release
& $py $bridge --config $config plan   --request '<path-to-request.json>'
& $py $bridge --config $config submit --request '<path>' --plan-token '<token>' --authorize --authorization-note 'human approved exact job/resources/stop policy'
& $py $bridge --config $config status --job-id '<id>'
& $py $bridge --config $config probe  --request '<path-to-probe-request.json>'
```

The local MCP server (`scripts/mcp_server.py --config <config>`) speaks the
official stdio MCP protocol over a hidden window; connect only from a
fully-local client. No HTTP port is opened and no model is uploaded.

## Operating loop (do not skip steps)

1. **`doctor` first.** Inspect launcher, allowed roots, disk, CPU/RAM budget and
   existing live solver jobs. License availability stays `UNVERIFIED` — a version
   query or ODB read does **not** prove a solver checkout.
2. **Plan before submit.** Build an authorized INP (or CAE deck) in its own
   folder; `plan --request` audits recursive INCLUDEs, additional files, code/env
   hashes and capacity, and returns a `plan_token`. It performs no analysis.
3. **Separate datacheck.** For an input test, plan and authorize a unique
   `operation="datacheck"` job; do not overwrite the real job.
4. **Submit only with explicit, recorded human authority** plus the exact
   `plan_token`. The `--authorize` flag records approval; it is not authentication.
5. **Monitor with `status`.** Trust PID-creation identity + raw LOG/DAT/MSG/STA/ODB,
   not cache or filename. Stale/orphaned records need reconciliation, not resubmission.
6. **Probe read-only.** `probe mode=inventory` first, then `probe mode=peak` with the
   real step / history region / axis. Accept a peak only with paired finite
   accepted RF/U, the strict 5% drop rule, and admissible energy gates.
7. **Stop only with explicit authority** for the exact managed job, or a
   preauthorized worker stop policy. Archive evidence; do not auto-advance to new
   physics, fitting, or queue takeover.

## Hard invariants (encode into every action)

- Establish scope before any mutation: job count, locked inputs, permitted
  numerical controls, output evidence, wall-time budget, reuse/restart/recovery
  and stop authority. A diagnosis/status request is not submission authority.
- Unique job name, job dir, and **empty** scratch dir per job. The backend refuses
  to overwrite an existing ODB/lock and refuses unknown live solver jobs rather
  than assuming cores are free.
- Never edit the source model to make the solver finish. Prepare a separate
  authorized attempt and preserve model/include/user/env hashes.
- An ODB is not a restart state. Restart continuation needs an independent proof
  of saved state/increment.
- Read-only invariant: ODBs are opened `readOnly=True`; reading must not change
  the source file (verify by SHA-256 before/after if in doubt).
- Do not claim a new analysis was verified, a license is available, UMAT/UINTER
  were recompiled, or WorkBuddy imported the skill — those are out of scope here.

## What this skill does NOT do

No automatic material calibration, geometry modeling, area-weighted damage
attribution, friction/cohesive component separation, Windows reboot-resume
service, or remote cluster simulation. Geometric/topology/units/experiment
validity still require agent judgment on the real task.

## Acceptance / verification

`validation/run_validation.py` re-runs 49 unit tests, the doctor probe, and reads
the two existing Stage-7 ODBs read-only (no submission). Run it:

```powershell
& $py (Join-Path $skill 'validation\run_validation.py') --config $config `
    --real-odb 'H:\STAGE7_20261003\S7_E1_X\S7_E1_X.odb' `
    --real-odb 'H:\STAGE7_20261003\S7_E2_X\S7_E2_X.odb'
```

It writes `validation/RESULTS.json` and reports `analysis_jobs_submitted: 0`.
See `README.md` and `VALIDATION_REPORT.md` (in the original Codex install) for the
full verified-scope matrix and the boundaries that must not be overstated.
