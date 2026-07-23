"""generic_executor.py — the project-agnostic command executor (Phase 3).

DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md §3.8/§3.9/§6/§9, §13 Phase 3.
ONE executor both lanes call (local skill + farm worker): resolve a named
check profile through the shared profile_runtime, run its approved commands
in an isolated worktree under a sanitized environment, grade per the
declared contract, and return a schema-v2 result envelope.

It knows NOTHING about any specific project (§4.3 boundary in the other
direction): no Stellaris, no hardcoded commands — the commands come from the
project's own versioned checks.json, never from the job. A job names a
profile; the profile maps it to approved commands (§3.6).

Isolation boundary honesty (§3.9): what is enforceable on the current hosts
(Windows, no containers, runner-as-user) is a sanitized child environment
(no queue git credentials, webhook values, baton/authorization paths, or
session identity in the child) and a runner-owned worktree the child runs
IN but does not publish from. Filesystem-level secrecy and least-privilege
users are stated future hardening, not claimed here. Whatever isolation was
actually enforced is REPORTED in the result's isolation_report, never
silently dropped.
"""

import json
import os
import re
import subprocess
import threading
import time

import profile_runtime

ADAPTER_ID = "generic-command"
ADAPTER_VERSION = "generic-command/0.1"

# Env var names whose VALUES must never reach a tested child process (§3.9).
# Denylist over the inherited environment: queue/git credentials, webhook
# secrets, tokens, baton/authorization identity, and the runner's own session
# id (a child must not be able to act as the runner).
_SECRET_ENV_PAT = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|WEBHOOK|CRED|APIKEY|API_KEY|"
    r"BATON|GH_TOKEN|GITHUB_TOKEN|GIT_ASKPASS|GCM|SSH_AUTH)", re.I)
_SECRET_ENV_EXACT = {
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_API_KEY",
    "ANTHROPIC_API_KEY",
}


def sanitized_env(base=None):
    """Return a COPY of `base` (default os.environ) with every secret /
    credential / queue-identity variable removed (§3.9). Returns
    (env, exposed) where `exposed` lists any secret-pattern keys that
    survived — it MUST be empty for the isolation_report to claim
    queue_credentials_exposed: false."""
    src = os.environ if base is None else base
    env, removed = {}, []
    for k, v in src.items():
        if k in _SECRET_ENV_EXACT or _SECRET_ENV_PAT.search(k):
            removed.append(k)
            continue
        env[k] = v
    env["AGENTOPS_ISOLATED"] = "1"
    exposed = [k for k in env if k in _SECRET_ENV_EXACT
               or _SECRET_ENV_PAT.search(k)]
    return env, exposed


def resolve_plan(project_doc, checks_doc, check_profile,
                 parameters=None, amendments=None,
                 project_id=None, config_revision=None):
    """Resolve a named profile into a normalized execution plan (§6). Returns
    (plan, problems). `plan` is None when problems is non-empty. Raises
    nothing — every failure is a structured problem so the caller can emit a
    profile-resolution-failure result. The effective_profile_hash is computed
    over the CANONICAL identity via profile_runtime — the SAME value both
    lanes must agree on."""
    problems = []
    ok, ps = profile_runtime.validate_project_manifest(project_doc or {})
    problems += ["project.json: " + p for p in ps] if not ok else []
    ok, cs = profile_runtime.validate_checks(checks_doc or {})
    problems += ["checks.json: " + p for p in cs] if not ok else []
    if problems:
        return None, problems

    pid = project_id or project_doc.get("project_id")
    checks = checks_doc.get("checks") or {}
    base = checks.get(check_profile)
    if base is None:
        return None, [f"unknown check_profile {check_profile!r} "
                      f"(known: {', '.join(sorted(checks)) or 'none'})"]

    ok, ps = profile_runtime.validate_parameters(base, parameters)
    problems += ps
    ok, ams = profile_runtime.validate_amendments(base, amendments)
    problems += ams
    if problems:
        return None, problems

    effective = profile_runtime.apply_amendments(base, amendments)
    h = profile_runtime.effective_profile_hash(
        pid, config_revision, check_profile, base,
        parameters=parameters, amendments=amendments)
    plan = {
        "project_id": pid,
        "project_config_revision": config_revision,
        "check_profile": check_profile,
        "effective_profile_hash": h,
        "commands": list(effective.get("commands") or []),
        "grading": dict(effective.get("grading") or {}),
        "timeout_minutes": effective.get("timeout_minutes"),
        "evidence": list(effective.get("evidence") or []),
        "required_capabilities": list(effective.get("required_capabilities") or []),
        "exclusive_resources": list(effective.get("exclusive_resources") or []),
        "appended_steps": list(effective.get("appended_steps") or []),
    }
    return plan, []


def _grade(grading, per_check):
    """Apply the declared grading contract. Returns (verdict, status).
    exit-code: first command's exit status decides. all-commands-pass: every
    command must exit 0. Unknown grading type = infrastructure/adapter
    failure (never a silent PASS)."""
    gtype = (grading or {}).get("type")
    if not per_check:
        return "FAIL", "adapter-failure"
    if gtype == "exit-code":
        ok = per_check[0]["exit_code"] == 0
    elif gtype == "all-commands-pass":
        ok = all(c["exit_code"] == 0 for c in per_check)
    else:
        return "FAIL", "adapter-failure"
    return ("PASS", "success") if ok else ("FAIL", "test-failure")


CANCEL_POLL_S = 5     # how often a running command re-asks should_cancel()


def _run_one(command, cwd, env, timeout_s, tail_bytes=4000,
             should_cancel=None, poll_s=CANCEL_POLL_S):
    """Run one approved command string in `cwd` under `env`. shell=True: the
    command comes from the project's approved checks.json (§3.6), not from a
    job. Returns a per_check dict. A timeout marks the whole run timed-out.

    CANCELLATION (Phase 5, 2026-07-23 — the farm lane's half of gap G4): with
    no `should_cancel` this is the original blocking subprocess.run, byte for
    byte. With one, the command runs under Popen and the callable is re-asked
    every `poll_s` seconds; True kills the process tree and marks the check
    cancelled. The executor deliberately does NOT know WHAT cancellation is —
    the caller owns that question (for the farm lane it is a queue marker),
    which is what keeps this file project-agnostic (§4.3).

    Why this existed for gui and not here: gap G4 was closed in 2026 for both
    gui dispatch paths, but a farm job has always run to its timeout no matter
    what — `stella` could publish a cancel marker and nothing read it."""
    start = time.time()
    if should_cancel is None:
        try:
            r = subprocess.run(command, cwd=cwd, env=env, shell=True,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout_s)
            out = ((r.stdout or "") + (r.stderr or ""))
            return {"command": command, "exit_code": r.returncode,
                    "duration_s": round(time.time() - start, 2),
                    "output_tail": out[-tail_bytes:], "timed_out": False}
        except subprocess.TimeoutExpired:
            return {"command": command, "exit_code": None,
                    "duration_s": round(time.time() - start, 2),
                    "output_tail": f"[TIMEOUT after {timeout_s}s]",
                    "timed_out": True}

    proc = subprocess.Popen(command, cwd=cwd, env=env, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    chunks, cancelled, timed_out = [], False, False
    reader = threading.Thread(target=_drain, args=(proc.stdout, chunks),
                             daemon=True)
    reader.start()
    last_poll = time.time()
    while proc.poll() is None:
        time.sleep(0.2)
        now = time.time()
        if now - start > timeout_s:
            timed_out = True
            break
        if now - last_poll >= poll_s:
            last_poll = now
            try:
                cancelled = bool(should_cancel())
            except Exception:          # noqa: BLE001 — a broken cancel probe
                cancelled = False      # must never kill a healthy run
            if cancelled:
                break
    if timed_out or cancelled:
        _kill_tree(proc)
    reader.join(timeout=2.0)
    out = "".join(chunks)
    if cancelled:
        return {"command": command, "exit_code": None,
                "duration_s": round(time.time() - start, 2),
                "output_tail": (out[-tail_bytes:] + "\n[CANCELLED]").strip(),
                "timed_out": False, "cancelled": True}
    if timed_out:
        return {"command": command, "exit_code": None,
                "duration_s": round(time.time() - start, 2),
                "output_tail": f"[TIMEOUT after {timeout_s}s]", "timed_out": True}
    return {"command": command, "exit_code": proc.returncode,
            "duration_s": round(time.time() - start, 2),
            "output_tail": out[-tail_bytes:], "timed_out": False}


def _drain(stream, chunks):
    try:
        for line in stream:
            chunks.append(line)
    except (OSError, ValueError):
        pass


def _kill_tree(proc):
    """Kill the shell AND what it spawned. shell=True means proc is the shell;
    killing only it orphans the real command, which on Windows keeps running
    and holds the worktree open — the cleanup then fails and the next job
    inherits the mess. Best-effort: taskkill /T where available, plain kill
    otherwise, and never raise (a failed kill must not lose the result)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            proc.kill()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _collect_evidence(workdir, globs):
    """Best-effort: resolve declared evidence globs relative to the workdir
    to a list of existing relative paths. Never fatal."""
    import glob as _glob
    found = []
    for g in globs or []:
        try:
            for p in _glob.glob(os.path.join(workdir, g), recursive=True):
                found.append(os.path.relpath(p, workdir).replace("\\", "/"))
        except OSError:
            pass
    return sorted(set(found))


def execute(envelope, workdir, machine, project_doc=None, checks_doc=None,
            clamp=(30, 3600), log=lambda *a: None, should_cancel=None):
    """Run one check profile and return a schema-v2 result envelope.

    envelope: the normalized job/local envelope — project_id, check_profile,
      profile_parameters, amendments, commit_sha, project_config_revision,
      execution_lane ("local"|"farm").
    workdir: an ALREADY-PREPARED directory (isolated worktree for a real run,
      or a checkout/copy for a fixture) containing .agentops/ — the executor
      does NOT do git here (prepare_worktree is a separate helper the lanes
      call) so this function is testable against a plain temp dir.
    project_doc/checks_doc: parsed .agentops manifests; read from the workdir
      when omitted.
    should_cancel: optional zero-arg callable re-asked every CANCEL_POLL_S
      while a command runs, and once between commands. True stops the run and
      yields verdict CANCELLED / status "cancelled". Omitted = the original
      blocking behaviour, unchanged. The executor never learns what
      cancellation IS; the lane owns that (for the farm lane it is a queue
      marker), which is what keeps this file project-agnostic (§4.3).

    Distinguishes the §5.4 taxonomy: profile-resolution-failure,
    isolation-policy-failure, timeout, test-failure, adapter-failure,
    success. A passing verdict never hides an isolation problem."""
    lane = envelope.get("execution_lane") or "local"
    pid = envelope.get("project_id")
    started = _utc()

    if project_doc is None:
        project_doc = _read_agentops(workdir, ".agentops/project.json")
    if checks_doc is None:
        checks_doc = _read_agentops(
            workdir, (project_doc or {}).get("checks_path") or ".agentops/checks.json")

    result = {
        "schema_version": profile_runtime.SCHEMA_V2,
        "project_id": pid, "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION, "machine": machine,
        "commit_sha": envelope.get("commit_sha"),
        "project_config_revision": envelope.get("project_config_revision"),
        "check_profile": envelope.get("check_profile"),
        "execution_lane": lane, "started_at": started,
    }

    plan, problems = resolve_plan(
        project_doc, checks_doc, envelope.get("check_profile"),
        parameters=envelope.get("profile_parameters"),
        amendments=envelope.get("amendments"),
        project_id=pid,
        config_revision=envelope.get("project_config_revision"))
    if problems:
        result.update({"verdict": "FAIL", "status": "profile-resolution-failure",
                       "status_line": "; ".join(problems[:6]),
                       "per_check": [], "finished_at": _utc()})
        return result

    result["effective_profile_hash"] = plan["effective_profile_hash"]
    # submit-time vs run-time hash agreement (§3.7): if the envelope carried a
    # hash, it MUST match what we resolved now, or the recipe drifted.
    submitted = envelope.get("effective_profile_hash")
    if submitted and submitted != plan["effective_profile_hash"]:
        result.update({"verdict": "FAIL", "status": "profile-resolution-failure",
                       "status_line": "effective_profile_hash mismatch: "
                       f"job {submitted} != resolved {plan['effective_profile_hash']}",
                       "per_check": [], "finished_at": _utc()})
        return result

    env, exposed = sanitized_env()
    isolation_report = {"environment_sanitized": True,
                        "queue_credentials_exposed": bool(exposed),
                        "worktree": os.path.abspath(workdir),
                        "network_policy": {"declared": False, "enforced": False}}
    if exposed:
        # A secret survived sanitization — do NOT run; report it (§3.9: never
        # silently drop requested isolation).
        result.update({"verdict": "FAIL", "status": "isolation-policy-failure",
                       "status_line": "secret env survived sanitization: "
                       + ", ".join(exposed),
                       "isolation_report": isolation_report,
                       "per_check": [], "finished_at": _utc()})
        return result

    lo, hi = clamp
    tmin = plan["timeout_minutes"]
    timeout_s = int(max(lo, min(hi, (tmin or lo / 60) * 60)))
    per_check = []
    timed_out = cancelled = False
    for cmd in plan["commands"]:
        # a cancel that arrives BETWEEN commands must not start the next one
        if should_cancel is not None and per_check:
            try:
                if should_cancel():
                    cancelled = True
                    break
            except Exception:          # noqa: BLE001 — see _run_one
                pass
        pc = _run_one(cmd, workdir, env, timeout_s, should_cancel=should_cancel)
        per_check.append(pc)
        if pc.get("cancelled"):
            cancelled = True
            break
        if pc["timed_out"]:
            timed_out = True
            break

    verdict, status = _grade(plan["grading"], per_check)
    if timed_out:
        verdict, status = "FAIL", "timeout"
    # Cancellation outranks grading: a partial run's exit codes say nothing
    # about the check. It is NOT a test-failure — the distinction matters to
    # every reader that counts failures (§5.4 taxonomy).
    if cancelled:
        verdict, status = "CANCELLED", "cancelled"

    result.update({
        "verdict": verdict, "status": status,
        "per_check": per_check,
        "isolation_report": isolation_report,
        "evidence": _collect_evidence(workdir, plan["evidence"]),
        "cleanup_status": "n/a (runner-owned worktree)",
        "status_line": _status_line(verdict, status, per_check),
        "finished_at": _utc(),
    })
    return result


def _read_agentops(workdir, rel):
    try:
        return profile_runtime.read_json(os.path.join(workdir, rel))
    except (OSError, ValueError):
        return {}


def _status_line(verdict, status, per_check):
    n = len(per_check)
    passed = sum(1 for c in per_check if c.get("exit_code") == 0)
    return f"{verdict} ({status}): {passed}/{n} command(s) exited 0"


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- worktree preparation (the lanes call this; execute() does not) ---------

def prepare_worktree(repo_dir, commit_sha, dest, git=None):
    """Create an isolated detached worktree of `repo_dir` at `commit_sha`
    under `dest` (§3.8/§3.9). Returns (path, None) or (None, problem).
    Farm lane: pinned sha. Local lane may pass HEAD. git: injectable
    (repo_dir, *args) -> (rc, out) for tests; defaults to a real git call."""
    if git is None:
        def git(*args):
            r = subprocess.run(["git", "-C", repo_dir] + list(args),
                               capture_output=True, text=True)
            return r.returncode, (r.stdout + r.stderr).strip()
    rc, out = git("worktree", "add", "--detach", dest, commit_sha)
    if rc != 0:
        return None, f"worktree add failed: {out[-300:]}"
    return dest, None


def cleanup_worktree(repo_dir, dest, git=None):
    """Remove a worktree created by prepare_worktree. Best-effort."""
    if git is None:
        def git(*args):
            r = subprocess.run(["git", "-C", repo_dir] + list(args),
                               capture_output=True, text=True)
            return r.returncode, (r.stdout + r.stderr).strip()
    git("worktree", "remove", "--force", dest)
