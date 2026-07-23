# _agent_process — reusable agent-ops framework (project policy)

This repo is the reusable, versioned framework from
`_stellaris_test_harness\DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md` (§4.1).
As of **Phase 3** it is a *minimal skeleton* that also serves as its own
first pilot project (dogfood): its `.agentops/` profiles run its own Python
test suite through its own generic executor.

## What lives here (Phase 3 scope)
- `profile_runtime.py` — the ONE profile resolver + canonical hashing +
  three-tier amendments + schema validation. **CANONICAL as of Phase 4
  (2026-07-22):** the harness `profile_runtime.py` is now a shim that imports
  and re-exports THIS file via the harness's `_agentops_bootstrap` (which
  auto-provisions this public peer if the sibling clone is missing). Edit here;
  there is no second copy to keep in sync.
- `winos.py` — **CANONICAL as of Phase 4 (2026-07-22):** stdlib-only Win32/
  process primitives (hidden subprocess, integrity, foreground, process scan).
  The harness `winos.py` is a shim loading this AS `sys.modules['winos']`
  (module self-replacement, so fixtures monkeypatching `winos.<fn>` work). A
  clean framework peer — no harness/Stellaris knowledge.
- `queue_protocol.py` — **CANONICAL as of Phase 4c (2026-07-22):** THE git
  queue protocol (claim / race_push / publish_retry / push_or_rollback / three
  pull disciplines / yield / requeue / archive / commit_result / cancel). The
  harness `queue_protocol.py` is a shim re-exporting this via
  `_agentops_bootstrap`. Note: `git_run()` imports `winos` (a generic stdlib
  Windows util still in the harness), so this module is loaded only in a harness
  context for now; relocating `winos` (7 harness importers) is a follow-up.
- `generic_executor.py` — project-agnostic: resolve a profile → run approved
  commands in a sanitized environment → grade → schema-v2 result envelope.
- `registry.py` + `registry.json` — the evolved (v2) project registry
  — **CANONICAL and CONSOLIDATED as of Phase 4 (2026-07-23):** the harness's
  flat `known-mod-repos.json` merged in here, so ONE file does project
  discovery AND the worker's reviewed auto-clone allowlist
  (`remote_for_repo` / `is_allowlisted`). Both shapes still parse, so old
  job files validate unchanged. Stays project-session-writable — this repo
  is ungated, so onboarding never needs the design master.
- `schemas/` — **CANONICAL as of Phase 4 (2026-07-23):** the versioned JSON
  contracts (`job.v2`, `result.v2`, `project`, `checks`) plus the binding
  rules. The Phase-1 harness copies are DELETED, not shimmed — a schema file
  is data, so the fix for a byte-copy is one file, not an import alias. The
  harness keeps `schemas/CONTRACTS.md` alone (gui-metrics.csv columns, Slack
  line shapes: adapter knowledge, §4.3), and a fixture fails if any schema
  reappears there.
- `adapter.py` — **NEW in Phase 5 (2026-07-23):** THE adapter boundary —
  which adapters exist, their versions, which one owns a given job, and
  whether a run takes exclusive machine resources (`owns_machine`). It names
  `stellaris-game` WITHOUT importing it, which is the whole point: the
  framework routes by descriptor, the harness supplies the implementation.
  Imports nothing (asserted by a fixture). An unknown adapter_id resolves to
  a descriptor with `known=False` rather than raising — a claimed job must
  always reach a verdict.
- `budget_core.py` — **CANONICAL as of Phase 4 (2026-07-23):** session-budget
  accounting and attribution — the weighted metric, incremental transcript
  ingest (main + subagent sidechains, which bill to the PARENT session),
  bounded expiring overrides, baton-derived role caps with the release grace,
  and the warn/stop decision. The harness `hooks/budget_guard.py` is now a
  thin adapter owning only the hook-event wiring, its paths, and the
  `stella restore` recovery exemption (verb names are adapter vocabulary).
  Its two doctrines are load-bearing and must survive any edit: FAIL OPEN,
  FAIL LOUD (broken accounting never blocks and never reports a silent
  zero), and every escape is bounded + expiring (no global off switch).
- `run_local.py` — the local lane: generate a local envelope, call the SAME
  generic executor as the farm lane.
- `tests/run_tests.py` — this project's real tests (the dogfood pilot's
  checks). `--fast` = pure-function subset; full adds executor e2e.
- `.agentops/project.json` + `.agentops/checks.json` — the pilot's manifest
  and check profiles (`fast`, `full`).

## Boundaries (design §4.3)
Nothing here may know about Stellaris, Paradox user dirs, OneDrive, playsets,
galaxy generation, screen coordinates, or game tiers. If generic code seems
to need those, the boundary is wrong. The framework is project-agnostic; the
Stellaris adapter stays in `_stellaris_test_harness`.

## Authorization
Framework code is process-baton-gated once the guard's protected-tree map
becomes data (**Phase 6**); until then it is edited by the design master.
`registry.json` is project-session-writable (baton-exempt) — onboarding a
project never requires the master (§3.5, §8.1).

## Testing
`python tests/run_tests.py` (exit 0 = all pass); `--fast` for the pure
subset. This is exactly what `.agentops/checks.json` invokes, so the
framework is graded through its own executor in both lanes.
