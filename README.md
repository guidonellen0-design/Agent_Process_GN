# _agent_process

Reusable, versioned agent-ops framework — the project-neutral core extracted
from the Stellaris test harness per
`_stellaris_test_harness/DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md`.

**Status: Phase 3 skeleton (2026-07-22).** Minimal core + a dogfood pilot
(this repo's own test suite) proving the generic executor, the evolved
registry, and both execution lanes on a real non-Stellaris project. Full
extraction of the queue protocol / runner / clerk is **Phase 4**; the
Stellaris adapter boundary is **Phase 5**.

## Lanes
- **Local lane** (`run_local.py`): a skill/CLI generates a local envelope
  and calls the generic executor directly — isolated worktree of HEAD,
  sanitized environment, standard result. No queue state.
- **Farm lane** (Phase 3b, via the harness runner): a queued job with
  `adapter_id: generic-command` routes to the SAME generic executor at the
  pinned commit. Same resolver, same grader, same result shape.

Both lanes call ONE profile runtime and ONE executor — the equivalence proof
is a matching `effective_profile_hash`, command order, grading, and status
taxonomy (environment differences are permitted and reported).

## Layout
```
_agent_process/
├── profile_runtime.py     # resolver + canonical hash + amendments + validation
├── generic_executor.py    # resolve → sanitized run → grade → schema-v2 result
├── registry.py / .json    # evolved v2 project registry (known-mod-repos successor)
├── schemas/               # versioned JSON contracts (CANONICAL, Phase 4)
├── run_local.py           # local lane entry
├── tests/run_tests.py     # this project's real tests (the dogfood pilot)
├── .agentops/             # project.json + checks.json (fast, full)
└── CLAUDE.md              # project policy
```

See `CLAUDE.md` for policy. As of Phase 4 the transitional duplication is
gone: `profile_runtime.py`, `queue_protocol.py`, `winos.py` and `schemas/` are
canonical HERE — the harness carries import shims for the modules and nothing
at all for the schemas.
