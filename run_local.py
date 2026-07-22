"""run_local.py — the LOCAL execution lane (design doc §6, §9.2, Phase 3).

The local lane is the sanctioned non-queue path: a skill/CLI generates a
local envelope and invokes the SAME generic executor and profile runtime as
the farm lane — "identical semantics" (§3.8) via ONE resolver, never a
second interpretation of checks.json. No queue claim, no queue state; project
policy, approved profiles, sanitized environment and grading still apply.

Usage:
  python run_local.py <project_id> <check_profile>
      [--param k=v ...] [--note "<operator note>"]
      [--workdir <dir>] [--no-worktree]

Exit 0 = PASS, 1 = FAIL/any non-pass, 2 = bad args / resolution error.
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile

import generic_executor as ge
import registry as reg

LOCALMODS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def _parse_params(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            print(f"REFUSED: --param {p!r} must be key=value")
            sys.exit(2)
        k, v = p.split("=", 1)
        # typed coercion is the profile's job at validation; pass strings and
        # let profile_runtime.validate_parameters check declared types
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("check_profile")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--note")
    ap.add_argument("--workdir")
    ap.add_argument("--no-worktree", action="store_true")
    ap.add_argument("--registry")
    args = ap.parse_args(argv)

    registry = reg.load(args.registry)
    entry = reg.resolve(registry, args.project_id)
    if not entry:
        print(f"REFUSED: project {args.project_id!r} is not in the registry "
              f"({args.registry or reg.DEFAULT_REGISTRY})")
        return 2

    project_dir = args.workdir or os.path.join(LOCALMODS, entry["repo"])
    if not os.path.isdir(project_dir):
        print(f"REFUSED: project dir not found: {project_dir}")
        return 2

    parameters = _parse_params(args.param)
    amendments = []
    if args.note:
        amendments.append({"tier": "field", "field": "operator_notes",
                           "value": args.note, "applied_by": "run_local",
                           "applied_at": ge._utc()})

    # pin the current HEAD so the local run is as reproducible as the farm run
    rc, head = _git(project_dir, "rev-parse", "HEAD")
    head = head.strip() if rc == 0 else None

    workdir = project_dir
    wt = None
    is_git = os.path.isdir(os.path.join(project_dir, ".git"))
    if is_git and not args.no_worktree and head:
        wt = tempfile.mkdtemp(prefix="agentops-local-")
        # git worktree add refuses a non-empty dir; use a child path
        dest = os.path.join(wt, "wt")
        path, err = ge.prepare_worktree(project_dir, head, dest,
                                        git=lambda *a: _git(project_dir, *a))
        if err:
            print(f"REFUSED: could not stage isolated worktree: {err}")
            return 2
        workdir = path

    envelope = {
        "project_id": args.project_id,
        "check_profile": args.check_profile,
        "execution_lane": "local",
        "commit_sha": head,
        "project_config_revision": head,
        "profile_parameters": parameters,
        "amendments": amendments,
    }
    try:
        result = ge.execute(envelope, workdir, socket.gethostname())
    finally:
        if wt:
            ge.cleanup_worktree(project_dir, os.path.join(wt, "wt"),
                                git=lambda *a: _git(project_dir, *a))
            import shutil
            shutil.rmtree(wt, ignore_errors=True)

    _print_result(result)
    return 0 if result.get("verdict") == "PASS" else 1


def _print_result(r):
    print(f"\n[{r.get('execution_lane')}] {r.get('project_id')} / "
          f"{r.get('check_profile')} -> {r.get('verdict')} ({r.get('status')})")
    print(f"  hash: {r.get('effective_profile_hash')}")
    for c in r.get("per_check") or []:
        code = "timeout" if c.get("timed_out") else c.get("exit_code")
        print(f"  cmd exit={code} ({c.get('duration_s')}s): {c.get('command')[:70]}")
    iso = r.get("isolation_report") or {}
    if iso:
        print(f"  isolation: sanitized={iso.get('environment_sanitized')} "
              f"creds_exposed={iso.get('queue_credentials_exposed')}")
    if r.get("status_line"):
        print(f"  {r['status_line']}")


if __name__ == "__main__":
    sys.exit(main())
