"""run_tests.py — the agentops-core pilot's own test suite (Phase 3 dogfood).

Real tests for the framework's own modules: profile_runtime, registry,
generic_executor. Hand-rolled (matching the harness idiom), exit 0 = all
pass. `--fast` runs only the pure-function checks (no subprocess-executing
end-to-end); the full run adds generic_executor.execute() against a temp
workdir. This IS the command the .agentops/checks.json profiles invoke, so
the framework grades itself through its own generic executor.
"""
import os
import sys
import tempfile
import json
import shutil
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import profile_runtime as pr          # noqa: E402
import registry as reg                # noqa: E402
import generic_executor as ge         # noqa: E402
import budget_core as bc              # noqa: E402
import adapter as ad                  # noqa: E402

FAST = "--fast" in sys.argv[1:]
RESULTS = []


def check(section, name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {section}: {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


# --- profile_runtime -------------------------------------------------------
_a = {"b": 1, "a": [2, 3], "c": {"y": True, "x": None}}
_b = {"c": {"x": None, "y": True}, "a": [2, 3], "b": 1}
check("pr", "canonical_json key-order independent",
      pr.canonical_json(_a) == pr.canonical_json(_b))
_prof = {"commands": ["python -c \"pass\""], "grading": {"type": "exit-code"},
         "amendable_fields": {"operator_notes": {"type": "string"}}}
_h1 = pr.effective_profile_hash("p", "a" * 40, "fast", _prof)
_h2 = pr.effective_profile_hash("p", "a" * 40, "fast",
                                {"grading": {"type": "exit-code"},
                                 "amendable_fields": {"operator_notes":
                                                      {"type": "string"}},
                                 "commands": ["python -c \"pass\""]})
check("pr", "effective hash deterministic across key orders", _h1 == _h2)
check("pr", "hash shape sha256:<64hex>",
      __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", _h1) is not None)
_h3 = pr.effective_profile_hash("p", "a" * 40, "fast", _prof,
                                amendments=[{"tier": "append-step", "step": "S"}])
check("pr", "appended amendment changes the hash", _h3 != _h1)
check("pr", "append-step amendment always permitted",
      pr.validate_amendments(_prof, [{"tier": "append-step", "step": "S"}])[0])
check("pr", "declared field amendment accepted",
      pr.validate_amendments(_prof, [{"tier": "field", "field": "operator_notes",
                                      "value": "x"}])[0])
check("pr", "undeclared field amendment rejected",
      not pr.validate_amendments(_prof, [{"tier": "field", "field": "coords",
                                          "value": "1"}])[0])
check("pr", "commands replacement rejected (tier 3)",
      not pr.validate_amendments(_prof, [{"tier": "field", "field": "commands",
                                          "value": []}])[0])
check("pr", "good project.json validates",
      pr.validate_project_manifest({"schema_version": 1, "project_id": "proj",
                                    "adapter_id": "generic-command"})[0])
check("pr", "project.json with a 1-char id rejected (min 2)",
      not pr.validate_project_manifest({"schema_version": 1, "project_id": "p",
                                        "adapter_id": "generic-command"})[0])
check("pr", "good checks.json validates",
      pr.validate_checks({"schema_version": 1, "checks": {"fast": _prof}})[0])
check("pr", "checks.json declaring commands amendable rejected",
      not pr.validate_checks({"schema_version": 1, "checks": {"bad": dict(
          _prof, amendable_fields={"commands": {"type": "array"}})}})[0])


# --- registry --------------------------------------------------------------
_v2reg = {"schema_version": 2, "projects": {
    "agentops-core": {"repo": "_agent_process", "adapter_id": "generic-command"}}}
_e = reg.resolve(_v2reg, "agentops-core")
check("reg", "v2 resolve returns entry with defaults filled",
      _e and _e["repo"] == "_agent_process"
      and _e["manifest_path"] == ".agentops/project.json")
check("reg", "v2 resolve unknown project -> None",
      reg.resolve(_v2reg, "nope") is None)
check("reg", "legacy flat allowlist resolves to synthesized entry",
      (reg.resolve({"SomeRepo": "https://x/y.git"}, "SomeRepo") or {}).get("remote")
      == "https://x/y.git")
check("reg", "corresponds: v2 explicit repo mapping",
      reg.corresponds({"schema_version": 2, "projects": {
          "p": {"repo": "R"}}}, "p", "R"))
check("reg", "corresponds: mismatch rejected",
      not reg.corresponds(_v2reg, "agentops-core", "OtherRepo"))
_mp, _cp = reg.config_paths(_e)
check("reg", "config_paths returns manifest+checks",
      _mp == ".agentops/project.json" and _cp == ".agentops/checks.json")
# remote_for_repo / is_allowlisted: the auto-clone allowlist lookup that used to
# be a flat known-mod-repos.json read on the harness side (consolidated here
# 2026-07-23). Keyed by REPO, not project_id — the worker clones a directory
# named after the repo, and the two differ (agentops-core -> _agent_process).
_alreg = {"schema_version": 2, "projects": {
    "agentops-core": {"repo": "_agent_process", "remote": "https://x/fw.git"},
    "SomeMod_GN": {"repo": "SomeMod_GN", "remote": "https://x/mod.git"},
    "NoRemote_GN": {"repo": "NoRemote_GN"}}}
check("reg", "remote_for_repo: repo == project_id",
      reg.remote_for_repo(_alreg, "SomeMod_GN") == "https://x/mod.git")
check("reg", "remote_for_repo: repo differs from project_id",
      reg.remote_for_repo(_alreg, "_agent_process") == "https://x/fw.git")
check("reg", "remote_for_repo: project_id is NOT a repo name",
      reg.remote_for_repo(_alreg, "agentops-core") is None)
check("reg", "remote_for_repo: unlisted repo -> None (never auto-cloned)",
      reg.remote_for_repo(_alreg, "Unlisted_GN") is None)
check("reg", "remote_for_repo: entry without a remote is not clonable",
      reg.remote_for_repo(_alreg, "NoRemote_GN") is None)
check("reg", "remote_for_repo: legacy flat shape still resolves",
      reg.remote_for_repo({"SomeRepo": "https://x/y.git"}, "SomeRepo")
      == "https://x/y.git")
check("reg", "remote_for_repo: the flat _comment key is not a repo",
      reg.remote_for_repo({"_comment": "blah"}, "_comment") is None)
check("reg", "is_allowlisted mirrors remote_for_repo",
      reg.is_allowlisted(_alreg, "SomeMod_GN")
      and not reg.is_allowlisted(_alreg, "Unlisted_GN"))
# the ACTUAL registry file resolves the pilot AND carries the merged allowlist
_realreg = reg.load()
check("reg", "shipped registry.json resolves agentops-core",
      (reg.resolve(_realreg, "agentops-core") or {}).get("adapter_id")
      == "generic-command")
check("reg", "shipped registry.json is the merged allowlist (mod repos present)",
      sum(1 for e in _realreg["projects"].values()
          if e.get("adapter_id") == "stellaris-game") >= 25,
      str(len(_realreg["projects"])))
check("reg", "every shipped entry carries a clonable remote",
      all(reg.remote_for_repo(_realreg, e.get("repo") or p)
          for p, e in _realreg["projects"].items()),
      "an entry with no remote cannot be auto-cloned and silently refuses")


# --- generic_executor (pure) ----------------------------------------------
_env, _exposed = ge.sanitized_env({"PATH": "/x", "GITHUB_TOKEN": "s3cr3t",
                                   "MY_WEBHOOK_URL": "http://h",
                                   "CLAUDE_CODE_SESSION_ID": "sid",
                                   "HARMLESS": "ok"})
check("ge", "sanitized_env strips token/webhook/session",
      "GITHUB_TOKEN" not in _env and "MY_WEBHOOK_URL" not in _env
      and "CLAUDE_CODE_SESSION_ID" not in _env)
check("ge", "sanitized_env keeps harmless vars + PATH",
      _env.get("HARMLESS") == "ok" and _env.get("PATH") == "/x")
check("ge", "sanitized_env reports nothing exposed", _exposed == [])
check("ge", "sanitized_env stamps AGENTOPS_ISOLATED", _env.get("AGENTOPS_ISOLATED") == "1")

_pdoc = {"schema_version": 1, "project_id": "proj", "adapter_id": "generic-command"}
_cdoc = {"schema_version": 1, "checks": {
    "fast": {"commands": ["python -c \"pass\""], "grading": {"type": "exit-code"}},
    "full": {"commands": ["a", "b"], "grading": {"type": "all-commands-pass"}}}}
_plan, _probs = ge.resolve_plan(_pdoc, _cdoc, "fast", project_id="proj",
                                config_revision="a" * 40)
check("ge", "resolve_plan success returns commands + hash",
      _plan and _plan["commands"] == ["python -c \"pass\""]
      and _plan["effective_profile_hash"].startswith("sha256:"))
_plan2, _probs2 = ge.resolve_plan(_pdoc, _cdoc, "nonesuch", project_id="proj")
check("ge", "resolve_plan unknown profile -> problem",
      _plan2 is None and _probs2)
check("ge", "resolve_plan hash matches profile_runtime directly",
      _plan["effective_profile_hash"] == pr.effective_profile_hash(
          "proj", "a" * 40, "fast", _cdoc["checks"]["fast"]))
check("ge", "_grade exit-code PASS on 0",
      ge._grade({"type": "exit-code"}, [{"exit_code": 0}]) == ("PASS", "success"))
check("ge", "_grade exit-code FAIL on 1",
      ge._grade({"type": "exit-code"}, [{"exit_code": 1}]) == ("FAIL", "test-failure"))
check("ge", "_grade all-commands-pass needs every 0",
      ge._grade({"type": "all-commands-pass"},
                [{"exit_code": 0}, {"exit_code": 1}]) == ("FAIL", "test-failure"))
check("ge", "_grade unknown type -> adapter-failure (never silent pass)",
      ge._grade({"type": "bogus"}, [{"exit_code": 0}])[1] == "adapter-failure")


# --- generic_executor.execute() end-to-end (FULL only: spawns subprocesses)
def _exec_e2e():
    wd = tempfile.mkdtemp(prefix="agentops-e2e-")
    try:
        os.makedirs(os.path.join(wd, ".agentops"))
        proj = {"schema_version": 1, "project_id": "toy",
                "adapter_id": "generic-command"}
        checks = {"schema_version": 1, "checks": {
            "pass": {"commands": ["python -c \"import sys;sys.exit(0)\""],
                     "grading": {"type": "exit-code"}, "timeout_minutes": 1},
            "fail": {"commands": ["python -c \"import sys;sys.exit(3)\""],
                     "grading": {"type": "exit-code"}, "timeout_minutes": 1},
            "multi": {"commands": ["python -c \"pass\"",
                                   "python -c \"import sys;sys.exit(1)\""],
                      "grading": {"type": "all-commands-pass"},
                      "timeout_minutes": 1}}}
        for n, d in ((".agentops/project.json", proj),
                     (".agentops/checks.json", checks)):
            with open(os.path.join(wd, n), "w", encoding="utf-8") as f:
                json.dump(d, f)

        r = ge.execute({"project_id": "toy", "check_profile": "pass",
                        "execution_lane": "local", "commit_sha": "a" * 40,
                        "project_config_revision": "a" * 40}, wd, "TESTHOST")
        check("e2e", "passing profile -> verdict PASS / status success",
              r["verdict"] == "PASS" and r["status"] == "success", str(r)[:200])
        check("e2e", "result is schema v2 with adapter + machine",
              r["schema_version"] == 2 and r["adapter_id"] == "generic-command"
              and r["machine"] == "TESTHOST")
        check("e2e", "isolation_report: no creds exposed, sanitized",
              r["isolation_report"]["environment_sanitized"] is True
              and r["isolation_report"]["queue_credentials_exposed"] is False)
        check("e2e", "effective_profile_hash present on result",
              r["effective_profile_hash"].startswith("sha256:"))

        rf = ge.execute({"project_id": "toy", "check_profile": "fail",
                         "execution_lane": "local"}, wd, "TESTHOST")
        check("e2e", "failing profile -> verdict FAIL / test-failure",
              rf["verdict"] == "FAIL" and rf["status"] == "test-failure")
        check("e2e", "per_check records the non-zero exit",
              rf["per_check"][0]["exit_code"] == 3)

        rm = ge.execute({"project_id": "toy", "check_profile": "multi",
                         "execution_lane": "local"}, wd, "TESTHOST")
        check("e2e", "all-commands-pass fails if any command fails",
              rm["verdict"] == "FAIL" and rm["status"] == "test-failure"
              and len(rm["per_check"]) == 2)

        # hash agreement: submit-time hash mismatch -> profile-resolution-failure
        good = ge.resolve_plan(proj, checks, "pass", project_id="toy",
                               config_revision="a" * 40)[0]
        rok = ge.execute({"project_id": "toy", "check_profile": "pass",
                          "execution_lane": "farm", "commit_sha": "a" * 40,
                          "project_config_revision": "a" * 40,
                          "effective_profile_hash": good["effective_profile_hash"]},
                         wd, "TESTHOST")
        check("e2e", "matching submit-time hash runs and passes",
              rok["verdict"] == "PASS")
        rbad = ge.execute({"project_id": "toy", "check_profile": "pass",
                           "execution_lane": "farm",
                           "project_config_revision": "a" * 40,
                           "effective_profile_hash": "sha256:" + "0" * 64},
                          wd, "TESTHOST")
        check("e2e", "mismatched submit-time hash -> profile-resolution-failure",
              rbad["status"] == "profile-resolution-failure")

        # LANE EQUIVALENCE: same profile, local vs farm envelope -> same hash
        rl = ge.execute({"project_id": "toy", "check_profile": "pass",
                         "execution_lane": "local", "commit_sha": "a" * 40,
                         "project_config_revision": "a" * 40}, wd, "H1")
        rfarm = ge.execute({"project_id": "toy", "check_profile": "pass",
                            "execution_lane": "farm", "commit_sha": "a" * 40,
                            "project_config_revision": "a" * 40}, wd, "H2")
        check("e2e", "LANE EQUIVALENCE: local and farm hashes match",
              rl["effective_profile_hash"] == rfarm["effective_profile_hash"],
              f"{rl['effective_profile_hash']} vs {rfarm['effective_profile_hash']}")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


# --- budget_core (Phase 4 extraction, 2026-07-23) --------------------------
# The accounting engine behind the harness budget_guard hook. Pure functions
# only here; the hook's end-to-end behaviour is fixtured on the adapter side.
_bcfg = dict(bc.DEFAULT_CONFIG)
_bst = bc.new_state()
_line = json.dumps({"message": {"model": "claude-opus-4-8", "usage": {
    "input_tokens": 100, "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 100, "output_tokens": 100}}})
_btmp = tempfile.mkdtemp()
try:
    _tp = os.path.join(_btmp, "t.jsonl")
    with open(_tp, "w", encoding="utf-8") as _f:
        _f.write(_line + "\n")
    _off, _prob = bc.read_new_usage(_tp, 0, _bcfg, _bst)
    # 1.0*100 + 1.25*100 + 0.1*100 + 5.0*100 = 735
    check("budget", "weighted metric matches the documented formula",
          _prob is None and abs(_bst["weighted"] - 735.0) < 1e-6,
          str(_bst["weighted"]))
    check("budget", "offset advances past the consumed line",
          _off == os.path.getsize(_tp))
    # a partial tail is NOT consumed — the next event re-reads it whole
    with open(_tp, "a", encoding="utf-8") as _f:
        _f.write('{"message": {"usage": {"output_tokens": 1')
    _off2, _ = bc.read_new_usage(_tp, _off, _bcfg, dict(_bst))
    check("budget", "an incomplete trailing line is left for next time",
          _off2 == _off)

    # overrides: bounded and EXPIRING, or not an override at all
    _ovd = os.path.join(_btmp, "ovr")
    os.makedirs(_ovd)
    def _write_ovr(sid, obj):
        with open(os.path.join(_ovd, sid + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    _write_ovr("live", {"additional_weighted_tokens": 5_000_000,
                        "expires_at": time.time() + 3600})
    check("budget", "a live override grants its tokens",
          bc.load_override(_ovd, "live") == 5_000_000)
    _write_ovr("dead", {"additional_weighted_tokens": 5_000_000,
                        "expires_at": time.time() - 10})
    check("budget", "an expired override grants nothing AND is deleted",
          bc.load_override(_ovd, "dead") == 0
          and not os.path.exists(os.path.join(_ovd, "dead.json")))
    _write_ovr("forever", {"additional_weighted_tokens": 5_000_000})
    check("budget", "an override with NO expiry is refused (no off switch)",
          bc.load_override(_ovd, "forever") == 0)
    _write_ovr("junk", {"additional_weighted_tokens": 5_000_000,
                        "expires_at": "not-a-date"})
    check("budget", "an unparseable expiry is refused, not trusted",
          bc.load_override(_ovd, "junk") == 0)
    check("budget", "no override file -> zero, never an error",
          bc.load_override(_ovd, "absent") == 0)

    # role cap off the process baton
    _q = os.path.join(_btmp, "q", "feedback")
    os.makedirs(_q)
    def _baton(obj):
        with open(os.path.join(_q, "BATON.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    _qd = os.path.dirname(_q)
    _baton({"session": "HOSTX/abcd1234"})
    check("budget", "the named holder gets the role cap",
          bc.is_baton_holder(_qd, "abcd1234-rest", host="HOSTX"))
    check("budget", "another session does not",
          not bc.is_baton_holder(_qd, "99999999", host="HOSTX"))
    check("budget", "holder match is host-qualified",
          not bc.is_baton_holder(_qd, "abcd1234-rest", host="OTHERHOST"))
    _rel = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 600))
    _baton({"session": "", "released_by": "HOSTX/abcd1234", "released_at": _rel})
    check("budget", "a releasing holder keeps the cap inside the grace window",
          bc.is_baton_holder(_qd, "abcd1234-rest", host="HOSTX", cfg=_bcfg))
    _old = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 10 * 3600))
    _baton({"session": "", "released_by": "HOSTX/abcd1234", "released_at": _old})
    check("budget", "past the grace window it drops to the session cap",
          not bc.is_baton_holder(_qd, "abcd1234-rest", host="HOSTX", cfg=_bcfg))
    _baton({"session": "", "released_by": "HOSTX/abcd1234", "released_at": "junk"})
    check("budget", "an unreadable release stamp fails toward FINISHING the handoff",
          bc.is_baton_holder(_qd, "abcd1234-rest", host="HOSTX", cfg=_bcfg))
finally:
    shutil.rmtree(_btmp, ignore_errors=True)

_b, _w, _p = bc.resolve_limits(_bcfg, 1e6, 0, False)
check("budget", "session cap and its warn fraction",
      _b == 18e6 and _w == 0.75 and "master cap" not in _p)
_b, _w, _p = bc.resolve_limits(_bcfg, 1e6, 0, True)
check("budget", "holder cap is higher and warns EARLIER",
      _b == 40e6 and _w == 0.6 and "[master cap]" in _p)
_b, _, _ = bc.resolve_limits(_bcfg, 1e6, 5e6, False)
check("budget", "an override raises the effective budget", _b == 23e6)

_ws = {"warned": False}
check("budget", "below the warn fraction says nothing",
      bc.warn_message(_ws, 100, 1000, 0.75, _bcfg, "x") is None)
_m = bc.warn_message(_ws, 800, 1000, 0.75, _bcfg, "x")
check("budget", "the early warn fires once", _m and "75%" in _m[0]
      and bc.warn_message(_ws, 800, 1000, 0.75, _bcfg, "x") is None)
_m = bc.warn_message(_ws, 950, 1000, 0.75, _bcfg, "x")
check("budget", "the final warn still fires after the early one",
      _m and "90%" in _m[0] and "/handoff-master" in _m[1])
_ws2 = {}
_m = bc.warn_message(_ws2, 950, 1000, 0.75, _bcfg, "x")
check("budget", "jumping past both thresholds yields ONE message: the final",
      _m and "90%" in _m[0]
      and bc.warn_message(_ws2, 950, 1000, 0.75, _bcfg, "x") is None)

check("budget", "a large transcript with no usage blocks is a LOUD failure",
      bc.schema_problem({"usage_entries": 0, "offset": 500_000}))
check("budget", "mostly-unparseable usage lines are a LOUD failure",
      bc.schema_problem({"usage_entries": 1, "parse_failures": 50}))
check("budget", "healthy accounting reports no problem",
      bc.schema_problem({"usage_entries": 500, "offset": 500_000,
                         "parse_failures": 0}) is None)


# --- farm-lane cancellation (Phase 5, 2026-07-23; gap G4's other half) -----
# A generic job used to run to its timeout no matter what: gui dispatch has
# honored cancel markers since Phase 2, the farm lane never did. The executor
# stays project-agnostic — it asks a callable and never learns what
# cancellation IS.
_c_env, _ = ge.sanitized_env({"PATH": os.environ.get("PATH", "")})
_slow = (sys.executable.replace("\\", "/")
         + ' -c "import time; time.sleep(30)"')
_pc = ge._run_one(_slow, _HERE, _c_env, timeout_s=60,
                  should_cancel=lambda: True, poll_s=0.1)
check("cancel", "a cancelled command stops early and is marked cancelled",
      _pc.get("cancelled") and not _pc["timed_out"] and _pc["duration_s"] < 15,
      str(_pc.get("duration_s")))
check("cancel", "a cancelled command reports no exit code (it never finished)",
      _pc["exit_code"] is None and "[CANCELLED]" in _pc["output_tail"])
_quick = sys.executable.replace("\\", "/") + ' -c "print(42)"'
_pc2 = ge._run_one(_quick, _HERE, _c_env, timeout_s=60,
                   should_cancel=lambda: False, poll_s=0.1)
check("cancel", "an uncancelled command still runs normally under the poll path",
      _pc2["exit_code"] == 0 and "42" in _pc2["output_tail"]
      and not _pc2.get("cancelled"))
_pc3 = ge._run_one(_quick, _HERE, _c_env, timeout_s=60)
check("cancel", "with NO should_cancel the original blocking path is used",
      _pc3["exit_code"] == 0 and "cancelled" not in _pc3)
def _boom():
    raise RuntimeError("cancel probe is broken")
_pc4 = ge._run_one(_quick, _HERE, _c_env, timeout_s=60,
                   should_cancel=_boom, poll_s=0.1)
check("cancel", "a BROKEN cancel probe never kills a healthy run",
      _pc4["exit_code"] == 0 and not _pc4.get("cancelled"),
      "failing toward killing jobs would be far worse than missing a cancel")


# --- adapter boundary (Phase 5, 2026-07-23) --------------------------------
# Identity + routing only: the framework names the Stellaris adapter without
# importing it, which is the entire point of a boundary.
check("adapter", "both adapters are registered with id, version and repo",
      set(ad.ADAPTERS) == {"generic-command", "stellaris-game"}
      and all(a.version and a.in_repo for a in ad.ADAPTERS.values()))
check("adapter", "the generic executor's id matches the registry",
      ge.ADAPTER_ID == ad.GENERIC_ADAPTER_ID
      and ad.ADAPTERS[ad.GENERIC_ADAPTER_ID].version == ge.ADAPTER_VERSION)
check("adapter", "the legacy default matches the profile runtime",
      ad.LEGACY_ADAPTER_ID == pr.LEGACY_ADAPTER_ID)
check("adapter", "a job with no adapter_id is a legacy Stellaris job",
      ad.resolve({}).adapter_id == ad.LEGACY_ADAPTER_ID,
      "read-side normalization must never reinterpret archived jobs")
check("adapter", "a declared adapter wins",
      ad.resolve({"adapter_id": "generic-command"}).adapter_id
      == "generic-command")
check("adapter", "normalize is applied when given",
      ad.resolve({"tier": "gui"}, pr.normalize_job).adapter_id
      == ad.LEGACY_ADAPTER_ID)
_unk = ad.resolve({"adapter_id": "not-a-real-adapter"})
check("adapter", "an unknown adapter resolves rather than raising",
      _unk.adapter_id == "not-a-real-adapter" and not ad.is_known(_unk))
check("adapter", "an unknown adapter is assumed to own the machine",
      _unk.owns_machine, "unknown capabilities must not skip resource gates")
check("adapter", "owns_machine separates the two lanes",
      ad.owns_machine({"adapter_id": "stellaris-game"})
      and not ad.owns_machine({"adapter_id": "generic-command"}))
check("adapter", "the boundary names stellaris-game WITHOUT importing anything",
      not __import__("re").findall(
          r"^\s*(?:import|from)\s+\S", open(os.path.join(_ROOT, "adapter.py"),
                                            encoding="utf-8").read(), __import__("re").M),
      "identity must not require the adapter's implementation to be present")


if not FAST:
    _exec_e2e()

print(f"\n{sum(RESULTS)}/{len(RESULTS)} agentops-core tests pass"
      + (" (fast subset)" if FAST else ""))
sys.exit(0 if all(RESULTS) else 1)
