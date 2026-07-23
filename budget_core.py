"""budget_core.py — project-agnostic session-budget accounting and attribution.

DESIGN-MULTIPROJECT-AGENTOPS-2026-07-21.md §13 Phase 4 ("budget attribution").
Extracted 2026-07-23 from the harness hook `hooks/budget_guard.py`, which is
now a thin adapter: it owns the hook-event wiring, the paths, and the
recovery-command exemption (`stella restore` — adapter vocabulary), while
everything here is the part any agent-ops installation needs.

What lives here: the weighted metric, the incremental transcript accounting
(main transcript + subagent sidechains), the bounded expiring override lease,
role-based caps keyed on the process baton, and the warn/stop DECISION. What
does not: anything that knows what a "stella" or a "playset" is (§4.3).

Two doctrines are preserved verbatim because they are the reason this guard is
trusted:
  * Fail open, fail loud. Broken accounting NEVER blocks, and never reports a
    silent zero — it reports "unavailable" on every event until fixed.
  * Every escape is bounded and expiring. There is no global off switch, and
    an override without a parseable expiry is not an override.
"""

import calendar
import json
import os
import time

DEFAULT_CONFIG = {
    "session_budget_weighted": 18_000_000,
    # Role cap: the process-baton holder (the design master) legitimately does
    # more per session than a working session — feedback consumption, process
    # changes, job watching — and the ordinary cap throttled three masters in
    # two days. 2026-07-20 (user decision): raised 30M -> 40M after four
    # masters in a row hit ~23-25M and each needed a manual override to write
    # its own handoff. A cap that reliably needs overriding is not a cap.
    "master_budget_weighted": 40_000_000,
    "warn_fraction": 0.75,
    # The holder warns EARLIER in relative terms, because what the warning has
    # to buy time for is expensive: the handoff doc, the baton release, and
    # anything still open. At 0.75 the warning landed at 22.5M — past the point
    # where observed masters were already winding down. 0.6 of 40M fires at 24M.
    "master_warn_fraction": 0.6,
    # Between the early warning and the 100% hard stop there used to be
    # SILENCE — five masters in a row worked through the single warn and hit
    # the stop with no handoff written, each needing a second session's
    # override to finish its own handoff. One more warning, once, at 0.9.
    "final_warn_fraction": 0.9,
    # A releasing holder keeps the role cap this many hours after release (or
    # until a successor claims, whichever first). Without it the guard dropped
    # the releaser to the ordinary cap the INSTANT the baton went null — and
    # the handoff ceremony releases BEFORE its last steps.
    "master_release_grace_h": 2,
    "weights": {"input": 1.0, "cache_creation": 1.25, "cache_read": 0.1,
                "output": 5.0},
    "model_multipliers": {"default": 1.0},
}


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


# ----------------------------------------------------------------- config ----

def load_config(config_path):
    """(config, problem). A missing file is CREATED with the defaults; an
    unreadable one degrades to the defaults and reports why. Never raises —
    a config problem must not be the thing that blocks a session."""
    try:
        cfg = _read_json(config_path)
        base = dict(DEFAULT_CONFIG)
        base.update(cfg or {})
        return base, None
    except FileNotFoundError:
        try:
            _atomic_write(config_path, DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG), None
        except OSError as e:
            return dict(DEFAULT_CONFIG), f"cannot create budget.json: {e}"
    except (OSError, ValueError) as e:
        return dict(DEFAULT_CONFIG), f"budget.json unreadable ({e}) — using defaults"


# ------------------------------------------------------------------ state ----

def new_state():
    return {"offset": 0, "weighted": 0.0, "usage_entries": 0,
            "parse_failures": 0, "warned": False}


def load_state(state_dir, sid):
    try:
        return _read_json(os.path.join(state_dir, sid + ".json"))
    except (OSError, ValueError):
        return new_state()


def save_state(state_dir, sid, st):
    try:
        _atomic_write(os.path.join(state_dir, sid + ".json"), st)
    except OSError:
        pass  # accounting best-effort; the next event recomputes the delta


# -------------------------------------------------------------- accounting ---

def model_multiplier(cfg, model):
    mm = cfg.get("model_multipliers") or {}
    model = (model or "").lower()
    for key, v in mm.items():
        if key != "default" and key.lower() in model:
            return float(v)
    return float(mm.get("default", 1.0))


def schema_problem(st):
    """Loud-failure detection — evaluated on CUMULATIVE state so the warning
    repeats on every event until the parser is fixed, not only when new bytes
    happen to arrive. The transcript format is not a stable API."""
    if st.get("usage_entries", 0) == 0 and st.get("offset", 0) > 200_000:
        return "no usage blocks recognized in a large transcript (schema change?)"
    if st.get("parse_failures", 0) > 20 and \
            st.get("parse_failures", 0) > st.get("usage_entries", 1):
        return f"{st['parse_failures']} unparseable usage lines (schema change?)"
    return None


def read_new_usage(path, offset, cfg, st):
    """Parse complete new lines of ONE transcript file from `offset`, adding
    weighted usage into st. Returns (new_offset, problem_or_None). The partial
    tail is deliberately left unread for next time."""
    w = cfg["weights"]
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError as e:
        return offset, f"transcript unreadable: {e}"
    end = chunk.rfind(b"\n")
    if end < 0:
        return offset, None
    for raw in chunk[:end].split(b"\n"):
        if b'"usage"' not in raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            st["parse_failures"] = st.get("parse_failures", 0) + 1
            continue
        msg = obj.get("message")
        u = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(u, dict):
            continue
        weighted = (w["input"] * (u.get("input_tokens") or 0)
                    + w["cache_creation"] * (u.get("cache_creation_input_tokens") or 0)
                    + w["cache_read"] * (u.get("cache_read_input_tokens") or 0)
                    + w["output"] * (u.get("output_tokens") or 0))
        st["weighted"] = st.get("weighted", 0.0) + weighted * model_multiplier(
            cfg, msg.get("model") if isinstance(msg, dict) else "")
        st["usage_entries"] = st.get("usage_entries", 0) + 1
    return offset + end + 1, None


def sidechain_files(transcript_path):
    """Subagent transcripts for this session: a sibling directory named after
    the main transcript's basename, i.e. <dir>/<sid>/subagents/*.jsonl.

    ATTRIBUTION RULE (decision 2026-07-14): subagent work bills to the PARENT
    session. An agent-initiated test session must be visible in the spend of
    the session that started it, or a session can hide arbitrary cost behind a
    fan-out."""
    d = os.path.join(os.path.splitext(transcript_path)[0], "subagents")
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d)
                      if n.endswith(".jsonl"))
    except OSError:
        return []


def ingest(state_dir, transcript_path, sid, cfg):
    """Incrementally sum new usage from the main transcript plus its subagent
    sidechains. Returns (state, problem). Nothing is ever re-read whole: a byte
    offset per file plus a cumulative weighted total is the entire mechanism."""
    st = load_state(state_dir, sid)
    if not transcript_path:
        return st, "no transcript_path on stdin"
    try:
        size = os.path.getsize(transcript_path)
    except OSError as e:
        return st, f"transcript unreadable: {e}"
    if size < st.get("offset", 0):
        # rotated/truncated — restart accounting (loud, not a silent zero);
        # dropping the agents map recounts sidechains into the fresh total
        st = dict(new_state(), warned=st.get("warned", False))
    st["offset"], problem = read_new_usage(transcript_path, st.get("offset", 0),
                                           cfg, st)
    if problem:
        return st, problem
    agents = st.setdefault("agents", {})
    for p in sidechain_files(transcript_path):
        key = os.path.basename(p)
        off = agents.get(key, 0)
        try:
            if os.path.getsize(p) < off:
                off = 0   # shrunk sidechain: recount (over-counting is safe)
        except OSError:
            continue
        agents[key], _ = read_new_usage(p, off, cfg, st)
    return st, schema_problem(st)


# --------------------------------------------------------------- overrides ---

def load_override(override_dir, sid):
    """Additional weighted tokens granted to this session, or 0.

    Every property here is load-bearing: the grant is per-session, bounded, and
    EXPIRING. An override with no expiry, or an unparseable one, is not a valid
    override — it is deleted, not honoured. There is no global off switch."""
    path = os.path.join(override_dir, sid + ".json")
    try:
        ov = _read_json(path)
    except (OSError, ValueError):
        return 0
    exp = ov.get("expires_at")
    if isinstance(exp, (int, float)):
        expired = time.time() > exp
    elif isinstance(exp, str):
        try:
            expired = time.strptime(exp[:16], "%Y-%m-%dT%H:%M") < time.localtime()
        except ValueError:
            expired = True
    else:
        expired = True
    if expired:
        try:
            os.remove(path)
        except OSError:
            pass
        return 0
    return float(ov.get("additional_weighted_tokens", 0) or 0)


# -------------------------------------------------------------------- role ---

def is_baton_holder(queue_dir, sid, host=None, cfg=None):
    """Does this session hold the process baton (and therefore the role cap)?

    BATON.json is the one source of truth for "who is master" — the same file
    the guard's process gate reads, so budget and guards can never disagree.

    Released-baton grace: the handoff flow releases the baton BEFORE its final
    steps, and this reads the LIVE file — so without the grace the releasing
    master fell to the ordinary cap mid-handoff and was hard-blocked while
    still under its role cap. The releaser keeps the cap until a successor
    claims (the claim overwrites released_by) or the window expires. An
    unreadable stamp fails toward FINISHING the handoff, not blocking it."""
    try:
        baton = _read_json(os.path.join(queue_dir, "feedback", "BATON.json"))
    except (OSError, ValueError):
        return False
    if host is None:
        host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or ""
    me = f"{host}/{(sid or '')[:8]}"
    holder = baton.get("session") or ""
    if holder:
        return holder.casefold() == me.casefold()
    if (baton.get("released_by") or "").casefold() != me.casefold():
        return False
    grace_h = float((cfg or DEFAULT_CONFIG).get(
        "master_release_grace_h", DEFAULT_CONFIG["master_release_grace_h"]))
    try:
        released = calendar.timegm(
            time.strptime((baton.get("released_at") or "")[:16],
                          "%Y-%m-%dT%H:%M"))
    except ValueError:
        return True
    return time.time() - released <= grace_h * 3600.0


def resolve_limits(cfg, used, override, is_holder):
    """(budget, warn_at, pretty) for this session. Pure — the caller decides
    what to do with them, which is what keeps every hook event consistent."""
    base = cfg["master_budget_weighted"] if is_holder \
        else cfg["session_budget_weighted"]
    budget = float(base) + float(override or 0)
    warn_at = float(cfg.get("master_warn_fraction", cfg["warn_fraction"])
                    if is_holder else cfg["warn_fraction"])
    pretty = (f"{used / 1e6:.1f}M/{budget / 1e6:.1f}M weighted"
              + (" [master cap]" if is_holder else ""))
    return budget, warn_at, pretty


# ---------------------------------------------------------------- warnings ---

def warn_message(st, used, budget, warn_at, cfg, pretty):
    """The one-time escalating warnings, as (systemMessage, additionalContext)
    — or None. MUTATES st's latch flags; the caller persists them.

    The FINAL warning outranks the early one and sets BOTH flags, so a session
    that jumps straight past both thresholds in one batch gets one message, not
    two. That ordering is the fixture that keeps the escalation honest."""
    final_at = float(cfg.get("final_warn_fraction",
                             DEFAULT_CONFIG["final_warn_fraction"]))
    if used >= final_at * budget and not st.get("warned_final"):
        st["warned_final"] = st["warned"] = True
        return (f"budget guard: {pretty} — over {int(final_at * 100)}% of the "
                "cap. The 100% hard stop blocks everything except recovery.",
                "FINAL budget warning: stop the current thread. If this session "
                "holds the baton, run /handoff-master NOW — the handoff doc "
                "must be written while budget remains; past the hard stop it "
                "takes a second session's override to escape.")
    if used >= warn_at * budget and not st.get("warned"):
        st["warned"] = True
        return (f"budget guard: {pretty} — over {int(warn_at * 100)}% of the "
                "session budget.",
                "Budget warning: finish the current bounded task, write a "
                "handoff, and start nothing new in this session.")
    return None
