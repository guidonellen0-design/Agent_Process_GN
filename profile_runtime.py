"""profile_runtime.py — the ONE shared profile/schema runtime (Phase 1).

Introduced by DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md (§3.7, §5, §13
Phase 1). This module is the single implementation of:

  - canonical JSON serialization and effective-profile hashing (§3.7);
  - in-memory normalization of legacy (v1) queue records to the v2 view
    (§5.1: readers accept both schemas, queue files are NEVER rewritten);
  - legacy-vs-v2 checklist precedence (§5.3);
  - the three-tier amendment rules (§3.7): append always, declared fields
    only, replacement never;
  - lightweight validation for .agentops/project.json and checks.json
    (mirrors schemas/*.schema.json; no external jsonschema dependency).

CANONICAL HOME: this file (Phase 4, done 2026-07-22). The harness's
profile_runtime.py is now a SHIM that imports and re-exports this module via
_agentops_bootstrap (auto-provisioning the public _agent_process peer if the
sibling clone is missing) — one implementation, no byte-copy, no drift. Edit
HERE. Both execution lanes (local skill, farm worker) call THESE functions —
never a second interpretation of checks.json.

No Stellaris knowledge lives here (§4.3 boundary): no install paths, no
playsets, no coordinates, no tiers beyond opaque strings.
"""

import hashlib
import json
import re

SCHEMA_V2 = 2

# Legacy records normalize to these defaults IN MEMORY only (§5.1).
LEGACY_ADAPTER_ID = "stellaris-game"
LEGACY_JOB_TYPES = {  # tier -> job_type for the in-memory v2 view
    "tier1": "stellaris-test",
    "gui": "stellaris-gui",
    "restore": "restore",
    "bootstrap": "bootstrap",
    "maintenance": "maintenance",
}

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Declared-type names -> accepted Python types. bool is NOT a number.
_TYPE_MAP = {
    "string": (str,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}

# Profile fields that can never be amended (tier 3: replacement never).
IMMUTABLE_PROFILE_FIELDS = ("commands", "grading")


def read_json(path):
    """BOM-tolerant JSON read (utf-8-sig): the harness-wide read discipline."""
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def canonical_json(obj):
    """THE canonical serialization (§3.7): sorted keys, fixed separators,
    ensure_ascii. Every hash in the schemas/ contracts is computed over
    exactly this form — never hash any other dump."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def effective_profile_hash(project_id, config_revision, profile_name,
                           profile, parameters=None, amendments=None):
    """sha256 over the canonical serialization of the effective profile
    identity (§3.7): project id, pinned revision, profile name, base
    profile contents, typed parameters, amendments. Amendment entries are
    hashed in application ORDER (append order is meaningful); everything
    else is key-sorted by canonicalization."""
    identity = {
        "project_id": project_id,
        "project_config_revision": config_revision,
        "check_profile": profile_name,
        "profile": profile,
        "parameters": parameters or {},
        "amendments": list(amendments or []),
    }
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def job_schema_version(job):
    """1 for legacy jobs (no/unknown schema_version), else the declared int."""
    v = job.get("schema_version")
    return v if isinstance(v, int) and v >= SCHEMA_V2 else 1


def derive_project_id(repo):
    """Legacy project identity: per repo, never a blanket 'stellaris' (§3.5)."""
    return repo or ""


def normalize_job(job):
    """Return a NEW dict presenting any job in the v2 view. Read-side only:
    the on-disk record is never rewritten (§5.1). Legacy jobs gain derived
    project_id/adapter_id/job_type; v2 jobs pass through with defaults
    filled. The input dict is not mutated."""
    out = dict(job)
    if job_schema_version(job) >= SCHEMA_V2:
        out.setdefault("adapter_id", LEGACY_ADAPTER_ID)
        out.setdefault("project_id", derive_project_id(job.get("repo")))
        out.setdefault("job_type",
                       LEGACY_JOB_TYPES.get(_tier_of(job), "automated-check"))
        return out
    out["schema_version"] = 1
    out["project_id"] = derive_project_id(job.get("repo"))
    out["adapter_id"] = LEGACY_ADAPTER_ID
    out["job_type"] = LEGACY_JOB_TYPES.get(_tier_of(job), "stellaris-test")
    return out


def _tier_of(job):
    t = job.get("tier")
    return "tier1" if t in (None, "", "tier1") else t


def checklist_authority(job):
    """§5.3 precedence: legacy jobs -> the checklist text is authoritative;
    v2 jobs -> profile + hash are authoritative and the checklist is a
    rendered snapshot."""
    if job_schema_version(job) >= SCHEMA_V2 and job.get("check_profile"):
        return "profile"
    return "checklist"


def project_tag(job):
    """The Slack project prefix for a job: '[<project_id>] ' for v2 jobs,
    '' for legacy jobs (legacy lines stay byte-identical; CONTRACTS.md C3)."""
    if job_schema_version(job) >= SCHEMA_V2:
        pid = job.get("project_id") or derive_project_id(job.get("repo"))
        if pid:
            return "[%s] " % pid
    return ""


# --- amendments (§3.7 three tiers) -----------------------------------------

def validate_amendments(profile, amendments):
    """Validate a job's structured amendment list against a base profile.
    Returns (ok, problems). Tier 1 (append-step): always permitted. Tier 2
    (field): only fields the profile declares in amendable_fields, with
    matching declared type. Tier 3 (replacement): no such tier exists —
    any attempt to touch commands/grading, and any unknown tier value,
    is rejected."""
    problems = []
    declared = profile.get("amendable_fields") or {}
    for i, a in enumerate(amendments or []):
        where = "amendment[%d]" % i
        if not isinstance(a, dict):
            problems.append("%s: not an object" % where)
            continue
        tier = a.get("tier")
        if tier == "append-step":
            if not isinstance(a.get("step"), str) or not a.get("step").strip():
                problems.append("%s: append-step needs non-empty 'step'" % where)
        elif tier == "field":
            field = a.get("field")
            if field in IMMUTABLE_PROFILE_FIELDS:
                problems.append(
                    "%s: '%s' can never be amended (replacement is tier 3: "
                    "a material recipe change is a checks.json commit + a "
                    "new job)" % (where, field))
            elif not field or field not in declared:
                problems.append(
                    "%s: field '%s' is not declared in amendable_fields"
                    % (where, field))
            else:
                want = _TYPE_MAP.get((declared[field] or {}).get("type"))
                val = a.get("value")
                if want and (not isinstance(val, want)
                             or (isinstance(val, bool)
                                 and want != _TYPE_MAP["boolean"])):
                    problems.append(
                        "%s: value for '%s' is not of declared type %s"
                        % (where, field, (declared[field] or {}).get("type")))
        else:
            problems.append(
                "%s: unknown amendment tier %r (only append-step and "
                "declared-field amendments exist)" % (where, tier))
    return (not problems, problems)


def apply_amendments(profile, amendments):
    """Produce the EFFECTIVE profile: base profile + appended steps +
    declared-field overrides. Never touches commands/grading (validate
    first — this function assumes a validated list). Returns a new dict;
    appended steps land in 'appended_steps' in application order."""
    eff = dict(profile)
    appended = list(eff.get("appended_steps") or [])
    for a in amendments or []:
        if a.get("tier") == "append-step":
            appended.append(a["step"])
        elif a.get("tier") == "field":
            eff[a["field"]] = a.get("value")
    if appended:
        eff["appended_steps"] = appended
    return eff


def validate_parameters(profile, parameters):
    """Typed parameters (§3.6): every supplied parameter must be declared
    by the profile with a matching type; undeclared keys are a
    profile-resolution failure. Returns (ok, problems)."""
    problems = []
    declared = profile.get("parameters") or {}
    for k, v in (parameters or {}).items():
        if k not in declared:
            problems.append("parameter '%s' is not declared by the profile" % k)
            continue
        want = _TYPE_MAP.get((declared[k] or {}).get("type"))
        if want and (not isinstance(v, want)
                     or (isinstance(v, bool) and want == _TYPE_MAP["number"])):
            problems.append("parameter '%s' is not of declared type %s"
                            % (k, (declared[k] or {}).get("type")))
    return (not problems, problems)


# --- manifest validation (mirrors schemas/*.schema.json) --------------------

def validate_project_manifest(d):
    """Lightweight .agentops/project.json validation. Returns (ok, problems)."""
    problems = []
    if not isinstance(d, dict):
        return (False, ["project.json is not an object"])
    if d.get("schema_version") != 1:
        problems.append("project.json schema_version must be 1")
    pid = d.get("project_id")
    if not isinstance(pid, str) or not _PROJECT_ID_RE.match(pid):
        problems.append("project_id missing or not [A-Za-z0-9][A-Za-z0-9_-]{1,63}")
    if not isinstance(d.get("adapter_id"), str) or not d.get("adapter_id"):
        problems.append("adapter_id missing")
    return (not problems, problems)


def validate_checks(d):
    """Lightweight .agentops/checks.json validation. Returns (ok, problems)."""
    problems = []
    if not isinstance(d, dict):
        return (False, ["checks.json is not an object"])
    if d.get("schema_version") != 1:
        problems.append("checks.json schema_version must be 1")
    checks = d.get("checks")
    if not isinstance(checks, dict) or not checks:
        return (False, problems + ["checks.json has no checks{}"])
    for name, prof in checks.items():
        if not _PROFILE_NAME_RE.match(name or ""):
            problems.append("profile name %r invalid" % name)
        if not isinstance(prof, dict):
            problems.append("profile %r is not an object" % name)
            continue
        cmds = prof.get("commands")
        if (not isinstance(cmds, list) or not cmds
                or not all(isinstance(c, str) and c.strip() for c in cmds)):
            problems.append("profile %r: commands must be a non-empty list "
                            "of strings" % name)
        grading = prof.get("grading")
        if not isinstance(grading, dict) or not grading.get("type"):
            problems.append("profile %r: grading.type required" % name)
        for bad in IMMUTABLE_PROFILE_FIELDS:
            if bad in (prof.get("amendable_fields") or {}):
                problems.append("profile %r: '%s' may not be declared "
                                "amendable (tier 3)" % (name, bad))
    return (not problems, problems)


# --- v2 job field validation (called by stella_queue.validate_job) ----------

def validate_v2_job_fields(job, registry=None):
    """Extra validation for jobs declaring schema_version 2. Additive: v1
    jobs never reach this. `registry` is the parsed known-mod-repos.json
    (or None to skip the correspondence check). Returns (ok, problems)."""
    problems = []
    pid = job.get("project_id")
    if not isinstance(pid, str) or not _PROJECT_ID_RE.match(pid):
        problems.append("v2 job: project_id missing/invalid")
    if not job.get("adapter_id"):
        problems.append("v2 job: adapter_id missing")
    if not job.get("job_type"):
        problems.append("v2 job: job_type missing")
    h = job.get("effective_profile_hash")
    if job.get("check_profile"):
        if not isinstance(h, str) or not _HASH_RE.match(h):
            problems.append("v2 job: check_profile without a valid "
                            "effective_profile_hash (sha256:<64 hex>)")
    if h and not job.get("check_profile"):
        problems.append("v2 job: effective_profile_hash without check_profile")
    for i, a in enumerate(job.get("amendments") or []):
        # Submit-time SHAPE check only; field-tier entries are re-validated
        # against the resolved profile's amendable_fields at resolution time.
        where = "v2 job amendment[%d]" % i
        if not isinstance(a, dict):
            problems.append("%s: not an object" % where)
        elif a.get("tier") == "append-step":
            if not isinstance(a.get("step"), str) or not a["step"].strip():
                problems.append("%s: append-step needs non-empty 'step'" % where)
        elif a.get("tier") == "field":
            if a.get("field") in IMMUTABLE_PROFILE_FIELDS:
                problems.append("%s: '%s' can never be amended (tier 3)"
                                % (where, a.get("field")))
            elif not a.get("field"):
                problems.append("%s: field amendment without 'field'" % where)
        else:
            problems.append("%s: unknown amendment tier %r"
                            % (where, a.get("tier")))
    if pid and job.get("repo") and registry is not None:
        if not _registry_corresponds(registry, pid, job.get("repo")):
            problems.append("v2 job: project_id %r does not correspond to "
                            "repo %r in known-mod-repos.json" %
                            (pid, job.get("repo")))
    return (not problems, problems)


def _registry_corresponds(registry, project_id, repo):
    """Registry correspondence (§3.5). Today's registry is the flat v1
    allowlist {repo: truthy}; correspondence there means project_id == repo.
    An evolved v2 registry ({schema_version:2, projects:{id:{...}}}) maps
    explicitly; its entry may carry 'repo' (defaults to the project id)."""
    if isinstance(registry, dict) and registry.get("schema_version") == 2:
        entry = (registry.get("projects") or {}).get(project_id)
        if not isinstance(entry, dict):
            return False
        return (entry.get("repo") or project_id) == repo
    return project_id == repo
