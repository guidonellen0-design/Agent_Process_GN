# _agent_process — reusable agent-ops framework (project policy)

This repo is the reusable, versioned framework from
`_stellaris_test_harness\DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md` (§4.1).
As of **Phase 3** it is a *minimal skeleton* that also serves as its own
first pilot project (dogfood): its `.agentops/` profiles run its own Python
test suite through its own generic executor.

## What lives here (Phase 3 scope)
- `profile_runtime.py` — the ONE profile resolver + canonical hashing +
  three-tier amendments + schema validation (canonical copy; the harness
  keeps a copy until **Phase 4** makes the harness *alias* this one — do not
  let the two drift in the meantime).
- `queue_protocol.py` — NOT here yet; it stays in the harness until Phase 4.
- `generic_executor.py` — project-agnostic: resolve a profile → run approved
  commands in a sanitized environment → grade → schema-v2 result envelope.
- `registry.py` + `registry.json` — the evolved (v2) project registry
  (known-mod-repos.json's successor; both shapes accepted meanwhile).
- `schemas/` — the versioned contracts (copied from the harness in Phase 1).
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
