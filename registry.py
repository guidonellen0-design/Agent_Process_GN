"""registry.py — project discovery over the evolved registry (Phase 3).

DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md §3.5. A worker cannot read a
project's .agentops/ until it can LOCATE the project; the registry is that
bootstrap. This is known-mod-repos.json's evolution, not a parallel artifact
(§3.5) — the harness's legacy flat allowlist merges in at Phase 4; both
shapes are accepted meanwhile.

No secrets, no machine paths here: the registry maps project_id -> remote +
config paths only. Machine-local project_id -> clone-path/creds mapping lives
in *.local sidecars (§3.5), which this module deliberately does not read.
"""

import os

import profile_runtime

DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "registry.json")


def load(path=None):
    """Parse the registry (BOM-tolerant). Returns the dict; a missing/broken
    file returns an empty v2 registry rather than raising, so a caller can
    treat 'unknown project' and 'no registry' identically."""
    try:
        return profile_runtime.read_json(path or DEFAULT_REGISTRY)
    except (OSError, ValueError):
        return {"schema_version": 2, "projects": {}}


def resolve(registry, project_id):
    """Return the registry entry dict for project_id, or None. Normalizes both
    shapes: v2 ({schema_version:2, projects:{id:{...}}}) and the legacy flat
    allowlist ({repo: url}) — a legacy hit yields a synthesized entry whose
    project_id equals the repo and whose remote is the url string."""
    if not project_id:
        return None
    if isinstance(registry, dict) and registry.get("schema_version") == 2:
        entry = (registry.get("projects") or {}).get(project_id)
        if not isinstance(entry, dict):
            return None
        out = dict(entry)
        out.setdefault("repo", project_id)
        out.setdefault("manifest_path", ".agentops/project.json")
        out.setdefault("checks_path", ".agentops/checks.json")
        out["project_id"] = project_id
        return out
    # legacy flat allowlist: {repo: url-or-truthy}, no adapter/config paths
    val = (registry or {}).get(project_id)
    if not val or project_id == "_comment":
        return None
    return {"project_id": project_id, "repo": project_id,
            "remote": val if isinstance(val, str) else None,
            "adapter_id": "stellaris-game",
            "manifest_path": ".agentops/project.json",
            "checks_path": ".agentops/checks.json"}


def corresponds(registry, project_id, repo):
    """Does project_id map to `repo` in the registry? Delegates to the ONE
    implementation in profile_runtime so the worker's submit-time check and
    the executor agree."""
    return profile_runtime._registry_corresponds(registry, project_id, repo)


def config_paths(entry):
    """(manifest_path, checks_path) for a resolved entry, with defaults."""
    entry = entry or {}
    return (entry.get("manifest_path") or ".agentops/project.json",
            entry.get("checks_path") or ".agentops/checks.json")
