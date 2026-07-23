"""adapter.py — THE adapter boundary (design doc §3.4, §4.3; Phase 5).

An ADAPTER is what turns a queue job into a graded result for one KIND of
project. `generic-command` runs approved commands in a sanitized worktree;
`stellaris-game` drives an external mutable application with a desktop, a
recovery journal and exclusive machine resources. The framework must not know
which is which — it must only know how to ASK.

## What an adapter owns (the seven translations, design §13 Phase 5)

    envelope        queue job -> the adapter's own run request
    profile         which checks to run, and their identity
    resources       what the run needs exclusively (screen, lock, none)
    cancellation    how an in-flight run is stopped
    timeout         how long a run may take, and what a timeout means
    result format   the adapter's outcome -> the v2 result envelope
    profile identity the effective_profile_hash contract (§3.7)

Phase 5 is a BOUNDARY, not a rewrite: the Stellaris transaction (journal, lock,
restoration on every exit path) is not restructured, it is named. This module
is the naming — one place that says which adapters exist, what version each is,
and which one owns a given job. Dispatch asks here instead of comparing against
a literal in a branch condition.

## Why a registry rather than subclasses

The two adapters do not share an implementation, only a contract, and they live
in different repos on purpose (§4.3: nothing here may know about Stellaris).
A descriptor keyed by adapter_id lets the framework name the Stellaris adapter
without importing it — the harness supplies the implementation, this file
supplies the identity and the routing rule.
"""

# The legacy default. A job with no schema_version, or a v2 job that does not
# declare an adapter, is a Stellaris job — that is what every archived job in
# the queue means, and read-side normalization must never reinterpret history
# (schemas/README.md binding rule 2/3).
LEGACY_ADAPTER_ID = "stellaris-game"
GENERIC_ADAPTER_ID = "generic-command"


class Adapter(object):
    """Identity + routing for one adapter. Deliberately NOT an implementation
    base class: the two adapters share a contract, not code, and one of them
    lives in another repo."""

    def __init__(self, adapter_id, version, summary, in_repo, owns_machine):
        self.adapter_id = adapter_id
        self.version = version
        self.summary = summary
        # which repo carries the implementation — documentation for a human
        # reading a heartbeat or a result, never an import path
        self.in_repo = in_repo
        # does a run of this adapter take exclusive machine resources? The
        # scheduler's Stellaris gates (screen, shared user dir, user-play)
        # apply to owns_machine adapters only; a farm-lane job runs alongside
        # a person at the keyboard. This is the ONE behavioural bit the
        # framework needs, and it is a property, not a branch.
        self.owns_machine = owns_machine

    def __repr__(self):
        return f"<Adapter {self.adapter_id} {self.version}>"


ADAPTERS = {
    GENERIC_ADAPTER_ID: Adapter(
        GENERIC_ADAPTER_ID, "generic-command/0.1",
        "approved commands in a sanitized, pinned worktree",
        in_repo="_agent_process", owns_machine=False),
    LEGACY_ADAPTER_ID: Adapter(
        LEGACY_ADAPTER_ID, "stellaris-game/1",
        "external mutable application: launch, journal, restore, GUI",
        in_repo="_stellaris_test_harness", owns_machine=True),
}


def resolve(job, normalize=None):
    """The adapter that owns this job. Never raises and never returns None.

    An UNKNOWN adapter_id resolves to a descriptor rather than an error on
    purpose: the caller that dispatches is not the caller that validates, and a
    job carrying a future adapter must reach a refusal with a verdict, not a
    crash on a claimed job (the worker's cardinal rule — a claimed job always
    produces a result). `known` is False on such a descriptor, which is what a
    dispatcher checks."""
    if normalize is not None:
        job = normalize(job)
    aid = (job or {}).get("adapter_id") or LEGACY_ADAPTER_ID
    known = ADAPTERS.get(aid)
    if known:
        return known
    unknown = Adapter(aid, f"{aid}/?", "unknown adapter", in_repo="?",
                      owns_machine=True)   # unknown = assume it needs the world
    unknown.known = False
    return unknown


def is_known(adapter):
    return getattr(adapter, "known", True)


def owns_machine(job, normalize=None):
    """Does running this job take exclusive machine resources? The farm lane's
    whole point is that it does not — it runs on any git+python worker, beside
    a user at the keyboard, skipping every Stellaris resource gate."""
    return resolve(job, normalize).owns_machine
