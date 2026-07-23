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
  (known-mod-repos.json's successor; both shapes accepted meanwhile).
- `schemas/` — **CANONICAL as of Phase 4 (2026-07-23):** the versioned JSON
  contracts (`job.v2`, `result.v2`, `project`, `checks`) plus the binding
  rules. The Phase-1 harness copies are DELETED, not shimmed — a schema file
  is data, so the fix for a byte-copy is one file, not an import alias. The
  harness keeps `schemas/CONTRACTS.md` alone (gui-metrics.csv columns, Slack
  line shapes: adapter knowledge, §4.3), and a fixture fails if any schema
  reappears there.
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
