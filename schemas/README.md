# schemas/ — the versioned data contracts (CANONICAL)

JSON Schema (draft 2020-12) documents for the envelopes the queue, runner,
adapters and external readers exchange.

**Canonical here as of Phase 4 (2026-07-23).** Phase 1 introduced these in
`_stellaris_test_harness/schemas/` and Phase 3b left a byte-copy in both
repos, held in agreement by a drift guard. The copies are gone: this is the
only place a schema file exists. The harness keeps `schemas/CONTRACTS.md`
only — the *Stellaris-side* external-reader contracts (gui-metrics.csv, the
Slack line shapes), which are adapter knowledge and do not belong in a
project-agnostic framework.

## Files

| File | Governs |
|---|---|
| `job.v2.schema.json` | queue `jobs/<id>.json` (v2 envelope; v1 legacy documented inside) |
| `result.v2.schema.json` | queue `results/<id>.json` (v2 additive envelope) |
| `project.schema.json` | a project repo's `.agentops/project.json` |
| `checks.schema.json` | a project repo's `.agentops/checks.json` (check profiles) |

## Binding rules (from `DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md`; normative)

1. **Additive only.** Existing fields are never renamed or removed within a
   major schema_version. New fields are optional with safe defaults.
   `additionalProperties` stays `true` everywhere — readers MUST tolerate
   unknown fields (that is how v1 readers survive v2 envelopes).
2. **Readers accept both schemas.** Legacy (v1: no `schema_version` key)
   records normalize IN MEMORY: `project_id` derives from `repo` (per repo,
   never a blanket project name), `adapter_id` defaults to the legacy
   adapter, `schema_version` defaults to 1. See `profile_runtime.normalize_job`.
3. **No queue files are ever rewritten** to migrate schema. On-disk records
   stay byte-identical; normalization is read-side only.
4. **Checklist precedence** (§5.3): v1 jobs — `checklist` is authoritative.
   v2 jobs — `check_profile` + `effective_profile_hash` are authoritative and
   `checklist` is a rendered snapshot; a snapshot/hash mismatch is an
   INFRASTRUCTURE failure, never silently reconciled.
5. **Canonical serialization** (§3.7): every hash in these contracts is
   sha256 over canonical JSON — sorted keys, separators `(",", ":")`,
   `ensure_ascii=True`, UTF-8 bytes, no BOM. One implementation:
   `profile_runtime.canonical_json` / `effective_profile_hash`. Never hash a
   non-canonical dump.
6. **Three-tier amendment rules** (§3.7):
   - Tier 1 — append-only step amendments: ALWAYS permitted. Appends cannot
     remove, reorder or replace existing steps/commands; they are recorded
     structurally and change the effective hash.
   - Tier 2 — field-level amendments: ONLY fields the profile declares in
     `amendable_fields` (with types); anything undeclared is rejected.
   - Tier 3 — replacement: NEVER. Commands and core grading rules cannot be
     replaced through any amendment; a material recipe change is a commit to
     `checks.json` and a new job.
7. **BOM tolerance.** All readers parse with `utf-8-sig`; all writers emit
   UTF-8 WITHOUT BOM. (PowerShell redirects write BOMs — job/queue JSON is
   written from Python or Bash only.)
8. **External-reader changes are breaking changes.** Field meanings change
   only deliberately, with a version bump recorded in the owning repo's
   contract doc — even though the readers are outside these repos (gap G7).

## Validation

These are documentation-grade JSON Schema. Runtime validation is the
lightweight in-house checks in `profile_runtime.py` (no external jsonschema
dependency on the worker hosts); `tests/run_tests.py` keeps the two in
agreement.
