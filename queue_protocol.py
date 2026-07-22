"""queue_protocol.py — THE authoritative git queue protocol (Phase 2).

DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md §3.2/§13 Phase 2, closing gaps
G1 and G3 (MIGRATION-GAPS.md). Every queue-git SEQUENCE lives here; the
runner and the clerk verbs both call these functions and neither implements
its own push/claim/yield/publication choreography anymore.

CANONICAL HOME: this file (Phase 4c, 2026-07-22). The harness
queue_protocol.py is now a SHIM that imports+re-exports this module via the
harness _agentops_bootstrap (auto-provisioning the public _agent_process peer).
Edit HERE; there is no second copy.

winos coupling (temporary): git_run() uses winos.run_text for the harness-wide
subprocess disciplines (hidden window under pythonw, utf-8 decoding). winos is
a generic, stdlib-only Windows util that still lives in the harness, so this
module is loaded ONLY in a harness context today (via the shim), where
`import winos` resolves. No current _agent_process consumer imports
queue_protocol, so the winos import never fires standalone. Relocating winos
into _agent_process (it has 7 harness importers) is its own follow-up
increment; until then do not import queue_protocol from pure-framework code.

The load-bearing distinction (G1) — TWO publication semantics, never to be
folded into each other:

  publish_retry   "retry-until-published". For results, feedback, learnings,
                  requeues: content that MUST reach origin, where replaying
                  our commit on top of a concurrent one is correct.
                  push -> pull --rebase (ABORTED if it cannot complete) ->
                  push -> optional sshorigin fallback.

  race_push       "first-writer-wins; the loser rolls back and STAYS lost".
                  For job claims and baton claims: two actors committing the
                  SAME transition, where rebasing the loser's commit on top
                  of the winner's would double-claim. A push rejection here
                  is the protocol working, not an error to retry.

  push_or_rollback  the gui-yield variant: try once, replay once, and if
                  origin still refuses, roll back and let the caller keep
                  its claim (a yield that cannot be recorded must not
                  strand the job in claimed/ with no runner).

Three pull disciplines (G2/§2.3), distinct on purpose:

  pull_session    fetch + ff-merge of the remote-TRACKING ref — immune to
                  the worker's concurrent fetch rewriting FETCH_HEAD.
  pull_worker     ff-only + narrow self-heal of provably-lossless
                  machine-local junk (.runner.pid, health/) only.
  pull_adapter    ff-only, NO auto-discard, returns the evidence — a dirty
                  adapter/harness tree may be a deliberate hotfix; a human
                  resolves it, loudly.

Every function takes `git`: a bound callable git(*args) -> (rc, out)
operating on the repo in question (the clerk passes a _git closure so
fixture stubs keep working; the runner passes q). `git=None` uses the
module's fail-soft default bound to `repo`.

No Stellaris knowledge here (§4.3 boundary) and no queue-file CONTENT
interpretation — envelopes belong to profile_runtime/schemas.
"""

import os
import subprocess

import winos


def git_run(repo, *args, timeout=120):
    """Fail-soft one-shot git (worker lesson 2026-07-17: an uncaught
    TimeoutExpired under pythonw killed whole cycles invisibly)."""
    try:
        r = winos.run_text(["git", "-C", repo] + list(args), timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s: git {' '.join(map(str, args[:3]))}"
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _bind(repo, git):
    return git if git is not None else (lambda *a, **k: git_run(repo, *a, **k))


# --- publication semantics (G1) ---------------------------------------------

def publish_retry(repo, git=None, sshorigin_fallback=False, log=print):
    """Retry-until-published (the former stella_queue.push_repo, verbatim
    semantics — protocol consolidation 2026-07-20 / Phase 2 move):
    push -> on failure pull --rebase (ABORTED immediately if it cannot
    complete: a mid-rebase clone can neither pull nor push, silently,
    forever) -> push again -> optionally fall back to the `sshorigin`
    remote (worker clones on the rig: expired GCM credentials otherwise
    look exactly like a dead worker; HTTPS `origin` stays the default)."""
    git = _bind(repo, git)
    rc, out = git("push")
    if rc == 0:
        return True
    rc_r, _ = git("pull", "--rebase")
    if rc_r != 0:
        git("rebase", "--abort")
        log("pull --rebase failed - aborted it rather than leaving this "
            "clone mid-rebase (that wedge is invisible and stops everything)")
    else:
        rc, out = git("push")
        if rc == 0:
            return True
    if sshorigin_fallback:
        rc_rem, remotes = git("remote")
        if rc_rem == 0 and "sshorigin" in remotes.split():
            rc_s, out_s = git("push", "sshorigin", "HEAD:main")
            if rc_s == 0:
                log("HTTPS push failed but sshorigin succeeded - published; "
                    "the HTTPS credential on this machine needs attention")
                return True
            log(f"sshorigin fallback also failed: {out_s[:200]}")
    log(f"push failed:\n{out[-400:]}")
    return False


def race_push(repo, git=None, rollback_ref="origin/main", fetch_first=False):
    """First-writer-wins. Push the local commit; if origin rejects it,
    someone else won the race — roll THIS clone back hard and report the
    loss. A lost race MUST stay lost (double-claim protection); callers
    never retry-into-publication here.

    rollback_ref: the claim path resets to origin/main as last fetched
    (pre-race state); the baton path fetches first and resets to @{u} so
    the loser immediately SEES the winner's claim."""
    git = _bind(repo, git)
    rc, out = git("push")
    if rc == 0:
        return True, out
    if fetch_first:
        git("fetch")
    git("reset", "--hard", rollback_ref)
    return False, out


def push_or_rollback(repo, git=None, log=print):
    """Try-once-replay-once, else roll back (the gui-yield shape): push;
    on rejection pull --rebase and push again; if origin STILL refuses,
    reset --hard origin/main so the caller can carry on with its previous
    state instead of stranding a half-recorded transition.

    Phase 2 hardening: a pull --rebase that cannot complete is ABORTED
    before the rollback (same invisible-wedge lesson as publish_retry —
    the pre-protocol yield code lacked this and could leave the worker
    clone mid-rebase)."""
    git = _bind(repo, git)
    rc, _ = git("push")
    if rc == 0:
        return True
    rc_r, _ = git("pull", "--rebase")
    if rc_r != 0:
        git("rebase", "--abort")
        log("yield replay: pull --rebase failed - aborted it rather than "
            "leaving this clone mid-rebase")
    else:
        rc, _ = git("push")
        if rc == 0:
            return True
    git("reset", "--hard", "origin/main")
    return False


# --- pull disciplines (§2.3: three, distinct on purpose) --------------------

def pull_session(repo, git=None):
    """Session discipline: fetch + ff-merge of the remote-TRACKING ref.
    `git pull --ff-only` merges FETCH_HEAD, a FILE any concurrent git
    process rewrites (the worker fetches every cycle) — seen dying twice on
    2026-07-20 with 'Cannot fast-forward to multiple branches'. origin/<b>
    is a real ref this process owns the read of. Detached/odd checkouts
    fall back to plain --ff-only. Returns (ok, out)."""
    git = _bind(repo, git)
    rc, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = (branch or "").strip()
    if rc != 0 or not branch or branch == "HEAD":
        rc, out = git("pull", "--ff-only")
        return rc == 0, out
    rc, out = git("fetch", "origin", branch)
    if rc == 0:
        rc, out = git("merge", "--ff-only", f"origin/{branch}")
    return rc == 0, out


def pull_worker(repo, git=None, junk_paths=(".runner.pid", "health")):
    """Worker discipline: ff-only with narrow self-heal of the known
    machine-local-junk wedges (worker-written, once tracked, later deleted
    upstream: .runner.pid, health/<HOST>.json). The discard is provably
    lossless (beats republish every cycle; the pidfile is machine-local).
    Narrow by design — never a broad reset. True when current."""
    git = _bind(repo, git)
    rc_pull, _ = git("pull", "--ff-only")
    if rc_pull != 0 and junk_paths:
        for p in junk_paths:
            git("checkout", "--", p)
        rc_pull, _ = git("pull", "--ff-only")
    return rc_pull == 0


def pull_adapter(repo, git=None):
    """Adapter/harness discipline: ff-only, NO auto-discard — a dirty
    adapter file may be a deliberate per-machine hotfix; a human (or the
    design master over ssh) resolves it. Returns (rc, dirty_files, head)
    so the caller can make the failure LOUD (health beat + log)."""
    git = _bind(repo, git)
    rc_pull, _ = git("pull", "--ff-only")
    if rc_pull == 0:
        return 0, [], ""
    _, porc = git("status", "--porcelain", timeout=30)
    dirty = [ln[3:] for ln in (porc or "").splitlines()
             if ln[:2].strip() and not ln.startswith("??")]
    _, head = git("log", "-1", "--format=%h", timeout=30)
    return rc_pull, dirty, (head or "?").strip()


# --- queue lifecycle transitions (G3: regression-locked here) ---------------

def claim_job(queue, name, host, git=None):
    """The claim transition: capture queued_at (the job's add-commit time,
    the queue-latency metric), git mv jobs/ -> claimed/, commit, race_push.
    Returns (claimed, queued_at). A push rejection is a LOST RACE: this
    clone hard-resets to origin/main as last fetched and the caller ends
    its cycle — the winner runs the job, we retry others next cycle."""
    git = _bind(queue, git)
    rc_q, qts = git("log", "-1", "--format=%ct", "--", f"jobs/{name}")
    try:
        queued_at = int(qts.strip().splitlines()[-1]) if rc_q == 0 else None
    except (ValueError, IndexError):
        queued_at = None
    git("mv", f"jobs/{name}", f"claimed/{name}")
    git("commit", "-m", f"claim {name} ({host})")
    won, _ = race_push(queue, git)
    return won, queued_at


def yield_job(queue, name, rid, yield_to, host, git=None, log=print):
    """The gui-yield transition (Tier-1-always-first, §7.5): git mv
    claimed/ -> jobs/, commit `yield <rid>: ...`, push_or_rollback. True =
    the yield is recorded on origin (caller ends the cycle; next cycle
    claims the Tier-1). False = origin unreachable/refusing: the rollback
    restored the CLAIM, and the caller proceeds to run the gui job rather
    than leaving it stranded in claimed/ with no runner."""
    git = _bind(queue, git)
    git("mv", f"claimed/{name}", f"jobs/{name}")
    git("commit", "-m", f"yield {rid}: runnable tier-1 {yield_to} goes first ({host})")
    return push_or_rollback(queue, git, log=log)


def requeue_stale_claim(queue, name, host, git=None):
    """The stale-claim recovery transition: git mv claimed/ -> jobs/ with
    the stale-claim stamp. Deliberately commit-only: the reaper batches
    into the cycle's later publication (results/heartbeat pushes carry
    it); a reaped claim must never be silently dropped, and the git mv in
    one commit is race-safe against the original claimer's result push."""
    git = _bind(queue, git)
    git("mv", f"claimed/{name}", f"jobs/{name}")
    git("commit", "-m", f"requeue stale claim {name} ({host})")


def prepare_result_path(queue, rid, result, log=print):
    """RESULT IMMUTABILITY: never overwrite an existing results/<rid>.json.
    A duplicate run of the same id (stale-reaper race, 2026-07-20: a
    CANCELLED record overwrote a $26.55 PASS) lands beside the original as
    <rid>-dupN.json with duplicate_of, loudly. Returns the path to write;
    mutates `result` only in the duplicate case."""
    rpath = os.path.join(queue, "results", rid + ".json")
    if os.path.exists(rpath):
        n = 2
        while os.path.exists(os.path.join(queue, "results",
                                          f"{rid}-dup{n}.json")):
            n += 1
        log(f"RESULT COLLISION: results/{rid}.json already exists - "
            f"writing {rid}-dup{n}.json instead; a duplicate run of this "
            "id happened (reaper race?) and both verdicts are preserved")
        result["duplicate_of"] = f"results/{rid}.json"
        rpath = os.path.join(queue, "results", f"{rid}-dup{n}.json")
    return rpath


def archive_job(queue, name, git=None):
    """Finished-job transition: git mv claimed/ -> archive/ (the requeue
    verb's rebuild source). Content commits ride commit_result."""
    git = _bind(queue, git)
    git("mv", f"claimed/{name}", f"archive/{name}")


def commit_result(queue, rid, verdict, host, retry_id=None, git=None):
    """The publication commit: add -A (result JSON + sidecars + metrics +
    auto-filed feedback + the archive mv + any auto-retry job, one commit)
    with the canonical result message. Push rides publish_retry via the
    caller's push (the worker keeps its sshorigin fallback)."""
    git = _bind(queue, git)
    git("add", "-A")
    git("commit", "-m", f"result {rid}: {verdict} ({host})"
        + (f" + auto-retry {retry_id}" if retry_id else ""))


def consume_cancel_marker(cancel_dir, rid, read_json):
    """Cancellation-marker consumption: read reason/requested_by, remove
    the marker (the result commit's add -A records the removal). Returns
    (reason, requested_by); missing/bad marker returns empty strings and
    still attempts removal."""
    marker = os.path.join(cancel_dir, rid + ".json")
    reason, by = "", ""
    try:
        m = read_json(marker)
        reason, by = m.get("reason", ""), m.get("requested_by", "")
    except (OSError, ValueError):
        pass
    try:
        os.remove(marker)
    except OSError:
        pass
    return reason, by
