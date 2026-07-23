"""capabilities.py — capability matching for pre/post-claim eligibility
(design §7.1, §7.4; Phase 7 increment 2).

## Why this module exists

`required_capabilities` has been in `job.v2.schema.json`, in
`project.schema.json`, in every `.agentops/checks.json` profile, and copied
into the resolved plan by `generic_executor.resolve_plan()` since Phase 3 —
and **nothing ever compared it to a machine**. It was written, fixtured and
documented on the declaring side while the deciding side went on asking a
hostname instead. This module is the missing half: the comparison.

## The contract

A machine publishes what it CAN DO as a flat `{name: True|False|None}` map
(runner `_capabilities()`, republished on the heartbeat). A job declares what
it NEEDS as a list of names. A job is eligible on a host when every required
name is present there.

Three states, not two — and the third is the one that matters:

    True   present   -> met
    False  absent    -> unmet, and it will stay unmet until the machine changes
    None   unknown   -> unmet, but for a different reason (the probe could not
           /missing     tell). Reported distinctly so "this machine cannot" is
                        never confused with "this machine could not tell".

Unknown counts as UNMET on purpose (fail-safe, matching the guard's own
never-fail-open rule): a capability probe that could not answer must not
authorize a destructive start. The scheduling consequence of unmet is WAIT,
not REFUSE (§7.4: "unsupported jobs stay queued for an eligible machine ...
never claimed-then-discovered"), so a transient unknown costs a cycle, not a
job. A permanently unroutable job is a reporting problem, not a safety one.

## Names are normalized, deliberately

The design writes `claude-cli` and `interactive-desktop`; Python dict keys and
the existing heartbeat write `claude_cli` and `desktop_session`. A capability
that silently never matches because one side hyphenated is exactly the class of
defect this module was built to end, so `-`, `_` and case are all equivalent
here. Callers may write whichever reads better on their side.

## Boundaries (§4.3)

Generic. This file knows nothing about scheduling classes, screens, games or
machines-by-name — the ADAPTER supplies its own required names (see
`requirements(extra=...)`), and this file only matches sets. It deliberately
carries no example drawn from a specific project either: a fixture asserts
that this module's whole text is free of adapter vocabulary, because the way
generic code stops being generic is one clarifying example at a time. No I/O,
no imports.
"""


def canonical(name):
    """Fold a capability name to its comparison form. `claude-cli`,
    `claude_cli` and `Claude CLI` are one capability."""
    return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_map(capabilities):
    """A published capability map keyed by canonical name. Later duplicates
    lose to earlier ones only when the earlier is decisive: a True or False
    is never overwritten by an unknown, so `{"claude-cli": True,
    "claude_cli": None}` stays True rather than depending on dict order."""
    out = {}
    for k, v in (capabilities or {}).items():
        ck = canonical(k)
        if ck in out and out[ck] is not None:
            continue
        out[ck] = v
    return out


def declared(obj):
    """The `required_capabilities` a job / plan / profile declares, canonical
    and de-duplicated, order preserved. Tolerant of the field being absent,
    None, or a bare string (a single-name list is the shape people write by
    hand and the schema's array is easy to forget)."""
    req = (obj or {}).get("required_capabilities")
    if req is None:
        return []
    if isinstance(req, str):
        req = [req]
    seen, out = set(), []
    for name in req:
        c = canonical(name)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def evaluate(required, capabilities):
    """Split `required` against a published capability map.

    Returns (met, absent, unknown) — three lists of canonical names. `absent`
    is "this machine cannot"; `unknown` is "this machine could not tell".
    Keeping them apart is the whole point: the first is a routing fact worth
    reporting to a human, the second is a probe that should be retried.

    BOTH sides are folded here, not just the map. `requirements()` already
    returns canonical names, so it would be easy to assume every caller is
    canonical — and a caller passing `["claude-cli"]` straight through would
    then miss a `claude_cli: True` machine silently, which is precisely the
    defect this module was written to end. Folding at the comparison, not at
    one convenient producer, is what makes that unrepresentable."""
    caps = normalize_map(capabilities)
    met, absent, unknown = [], [], []
    for name in [canonical(n) for n in (required or [])]:
        if name not in caps or caps[name] is None:
            unknown.append(name)
        elif caps[name]:
            met.append(name)
        else:
            absent.append(name)
    return met, absent, unknown


def eligible(required, capabilities):
    """(ok, reason) for ONE host. `ok` is True only when every required
    capability is positively present. `reason` is '' when ok, else a single
    line naming what is missing and in which of the two senses — written for
    a Slack line and a queued-job explanation, which is where it lands."""
    met, absent, unknown = evaluate(required, capabilities)
    if not absent and not unknown:
        return True, ""
    parts = []
    if absent:
        parts.append("missing capability(ies): " + ", ".join(sorted(absent)))
    if unknown:
        parts.append("undetermined capability(ies): " + ", ".join(sorted(unknown)))
    return False, "; ".join(parts)


def requirements(job, extra=None):
    """The full pre-claim requirement set for a job: what the JOB declares,
    plus whatever its ADAPTER adds (`extra`).

    The split is the §4.3 boundary in one signature. The framework can read a
    job's own declaration from the schema it owns; it cannot know what an
    interactive run of some particular adapter needs, because it does not know
    what that adapter's job classes mean. The adapter passes those in."""
    seen, out = set(), []
    for name in declared(job) + [canonical(e) for e in (extra or [])]:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out
