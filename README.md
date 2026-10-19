# dsh-abaqus-local-simulation

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) plugin that
bundles the **abaqus-local-simulation** skill: audited local Windows
Abaqus/Standard or Explicit submission, INP dependency plans, process-aware
monitoring, restart evidence, and read-only ODB peak extraction — exposed through
a stdlib-only Python CLI and a local stdio MCP server.

The plugin carries the Abaqus calling backend (author's new MIT bridge code only).
It does **not** bundle the Abaqus solver, a license, Fortran source, or any
proprietary model/ODB. Abaqus and a valid license must already be installed on the
host; this plugin only drives the existing local install.

## Layout

```
dsh-abaqus-local-simulation/
├─ package.json          # DSH plugin manifest (dsh.bundle.patch, engines.dsh)
├─ cordis.patch.yml      # mounts the host plugin that registers the bundled skill
├─ plugin/index.mjs      # stable Cordis entry (re-exports skills/.../host-plugin.js)
├─ skills/
│  └─ abaqus-local-simulation/
│     ├─ SKILL.md         # model-facing skill body
│     ├─ host-plugin.js   # bundled skill provider (Node, Cordis)
│     ├─ scripts/         # abaqus_agent.py (CLI) + mcp_server.py + odb_probe.py …
│     ├─ references/      # full calling handbook, user subroutines, troubleshooting
│     ├─ assets/          # local_config.json (machine config) + request examples
│     └─ validation/      # run_validation.py + RESULTS.json
└─ LICENSE
```

## Install

### As a DSH bundle (recommended)

From a profile directory, install the npm/bundle:

```bash
dsh plugin --profile desktop add <npm-or-github specifier>
```

The bundled `skills/abaqus-local-simulation/SKILL.md` is auto-discovered by DSH's
skill catalog; invoke it with `/abaqus-local-simulation` or let the model load it
when an Abaqus task matches.

### As a user skill (no bundle install)

Drop the skill directory into the user skill root; DSH discovers it on the next
turn:

```
C:\Users\<you>\.dsh\skills\abaqus-local-simulation\
```

This is the lowest-friction path on a machine where the profile is managed.

## Configure for the host

Edit `skills/abaqus-local-simulation/assets/local_config.json`:

- `abaqus_launcher` — absolute path to `abaqus.bat`.
- `allowed_roots` — every INP/include/user/ODB/work/scratch path must live inside.
- `registry_dir` — this plugin's own job/audit records (never a historical queue).
- `scratch_roots` — each job uses a unique, empty sub-directory.
- `max_total_cpus` / `max_concurrent` / `max_total_memory_mb` — physical-core budget.

The shipped config targets the author's machine. Review every path before use.

## Verify

From the skill directory, with the configured Python:

```powershell
$py = 'D:\anaconda3\python.exe'
$skill = '<path to skills/abaqus-local-simulation>'
& $py (Join-Path $skill 'validation\run_validation.py') `
   --config (Join-Path $skill 'assets\local_config.json') `
   --real-odb 'H:\STAGE7_20261003\S7_E1_X\S7_E1_X.odb' `
   --real-odb 'H:\STAGE7_20261003\S7_E2_X\S7_E2_X.odb'
```

Expects 49 unit tests passing, doctor probe OK, two read-only ODB reads with
`analysis_jobs_submitted: 0`. It submits no analysis, terminates no solver, and
modifies no historical input.

## Scope and limits

Verified: skill structure, protection logic, peak algorithm, MCP stdio handshake,
real Abaqus version query, and read-only ODB extraction against existing Stage-7
ODBs. **Not** verified here (and must not be overstated): a fresh solver
submission/datacheck end-to-end, a real license checkout, UMAT/UINTER
recompilation, actual restart continuation, or WorkBuddy UI import.

License: MIT for the newly authored bridge only. Abaqus/SIMULIA, official
documentation, and the original research project are not covered by this license.
