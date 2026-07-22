r"""winos.py — the ONE home for machine-facing Win32/process primitives.

Born 2026-07-20 (design note item 1). Every major bug fixed that day was DRIFT
between duplicated copies of these primitives, not a logic error:
  - the Tier-1 claim gate probed `stellaris.exe` only while stella also refuses
    on the Paradox Launcher — two process scanners, one burned claim per cycle;
  - stella's subprocess calls lacked CREATE_NO_WINDOW while runner.sh had it —
    a console window flashed on the user's desktop every second of a game load;
  - the elevated-foreground probe inferred elevation from OpenProcessToken
    FAILING; Windows' default policy is NO_WRITE_UP (reads upward SUCCEED), so
    it never detected a real elevated window in its life.
Rules: stdlib only, no harness imports, no policy — callers decide what a
result MEANS (refuse/warn/log); this module only observes. Consumers keep
their old names as module-level aliases so fixtures that monkeypatch them
keep working.

CANONICAL HOME: this file (Phase 4 winos increment, 2026-07-22). The harness
winos.py is a shim that loads THIS module AS sys.modules['winos'] via
_agentops_bootstrap.load_peer_module — the whole module is replaced, not
names-copied, precisely so `winos.integrity_rid = stub` in a fixture is seen by
`winos.pid_is_higher_integrity`. stdlib-only keeps it a clean framework peer.
"""
import ctypes
import os
import subprocess

RIG_HOST = "DESKTOP-AI160B2"    # the one shared constant that was mirrored


def run_hidden(*args, **kw):
    """subprocess.run that NEVER allocates a console window.

    Harness processes run under pythonw.exe (windowless), and on Windows a
    console child of a windowless parent allocates its OWN visible window.
    stella polls `tasklist` ~once a second while a game loads, so one missing
    flag flashed a console on the user's desktop every few seconds
    (2026-07-20). Callers may still override creationflags explicitly."""
    kw.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(*args, **kw)


def run_text(*args, **kw):
    """run_hidden in TEXT mode with the ONE output-decoding discipline.

    Bare `text=True` decodes with the locale codepage (cp1252), and that
    cost three production bugs in one week (2026-07-20): a consume-verify
    that refused a CORRECT commit because the note's em-dash decoded
    differently on each side, and two BOM/utf-8-sig lessons before it.
    Harness text (git blobs, notes, JSON) is UTF-8; decode it as such and
    degrade to U+FFFD rather than crash or mismatch.

    LIMITATION — stdin is NOT covered: Python's subprocess translates \n to
    \r\n when writing text-mode `input=`, which silently corrupted a
    `git mktree` entry into "<name>.json\r" (2026-07-20). For byte-exact
    stdin use a NUL-terminated mode (`mktree -z`) or pass bytes without
    text mode."""
    kw.setdefault("capture_output", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return run_hidden(*args, **kw)


def tasklist_csv(image=None, timeout=30):
    """Lower-cased `tasklist /FO CSV` output, optionally filtered to one image
    name; None on failure (callers fail OPEN or CLOSED per their own policy —
    tasklist itself can flake under a heavy modded load, 2026-07-18)."""
    cmd = ["tasklist", "/FO", "CSV"]
    if image:
        cmd[1:1] = ["/FI", f"IMAGENAME eq {image}"]
    try:
        r = run_text(cmd, timeout=timeout)
        if r.returncode != 0:
            return None
        return (r.stdout or "").lower()
    except (OSError, subprocess.SubprocessError):
        return None


def scan_processes(names):
    """Which of `names` (substring match, case-insensitive) have a live
    process? Empty list on scanner failure — the caller's own hard gate is the
    backstop (the claim-gate lesson: this list must MATCH stella's CONTENDERS
    scan, which is why there is now exactly one implementation)."""
    out = tasklist_csv()
    if out is None:
        return []
    return [n for n in names if n.lower() in out]


def image_running(image, timeout=30):
    """Is a process with this exact image name alive? False on unknowable."""
    out = tasklist_csv(image=image, timeout=timeout)
    return bool(out and image.lower() in out)


def _win_prototypes():
    """Explicit restype/argtypes are NOT optional: ctypes defaults every
    restype to c_int, which TRUNCATES 64-bit pointers and segfaults the
    interpreter on first dereference (hit live while writing the integrity
    probe, 2026-07-20)."""
    k, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                     ctypes.POINTER(ctypes.c_void_p)]
    adv.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.POINTER(ctypes.c_ulong)]
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    adv.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_ulong)
    adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    return k, adv


def integrity_rid(pid=None):
    """The mandatory INTEGRITY LEVEL rid of a process (0x2000 medium, 0x3000
    high/elevated, 0x4000 system), or None if it cannot be read. pid=None =
    this process."""
    TOKEN_QUERY = 0x0008
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TokenIntegrityLevel = 25
    opened = None
    try:
        k, adv = _win_prototypes()
        if pid is None:
            h = k.GetCurrentProcess()
        else:
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None                 # cannot open: unknown
            opened = h
        tok = ctypes.c_void_p()
        if not adv.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            need = ctypes.c_ulong()
            adv.GetTokenInformation(tok, TokenIntegrityLevel, None, 0,
                                    ctypes.byref(need))
            if not need.value:
                return None
            bufr = ctypes.create_string_buffer(need.value)
            if not adv.GetTokenInformation(tok, TokenIntegrityLevel, bufr,
                                           need.value, ctypes.byref(need)):
                return None
            # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label; } — Label.Sid
            # is the first pointer-sized field
            sid = ctypes.c_void_p.from_buffer(bufr).value
            if not sid:
                return None
            cnt = adv.GetSidSubAuthorityCount(sid).contents.value
            return adv.GetSidSubAuthority(sid, cnt - 1).contents.value
        finally:
            k.CloseHandle(tok)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        return None
    finally:
        if opened:
            ctypes.windll.kernel32.CloseHandle(opened)


def pid_is_higher_integrity(pid):
    """Does `pid` run at a HIGHER mandatory integrity level than we do?

    Rewritten 2026-07-20 after end-to-end validation proved the previous
    implementation could not detect elevation AT ALL: it inferred "elevated"
    from OpenProcessToken FAILING, but Windows' default mandatory policy is
    NO_WRITE_UP — READ access upward is permitted, so the call SUCCEEDS
    against an elevated target. It looked verified because it was only tried
    against SYSTEM processes (which fail OpenProcess for unrelated reasons)
    and because unit fixtures stub the caller. `stella selftest` now
    exercises the real path.

    Unknown target (unopenable) reports True — a protected/SYSTEM process is
    genuinely above us. Unknown SELF reports False: this gates refusals, and
    guessing "elevated" on missing data would block healthy runs."""
    mine = integrity_rid(None)
    theirs = integrity_rid(pid)
    if theirs is None:
        return True
    if mine is None:
        return False
    return theirs > mine


FOCUS_HELPER_TASK = "stella-focus-helper"


def run_focus_helper(wait_s=4.0):
    """Fire the registered elevated focus helper (scripts\\focus_stellaris.py
    via the on-demand task above; option 2, user decision 2026-07-20) and give
    it wait_s to act. Returns True if the task was TRIGGERED - the caller
    re-checks its own condition afterwards; the helper's outcome is in
    harness focus-helper.log. False when the task is not registered on this
    machine (graceful: behavior without the helper is exactly the old
    behavior)."""
    try:
        rc, _ = run_text(["schtasks", "/run", "/tn", FOCUS_HELPER_TASK],
                         timeout=15)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        return False
    if rc != 0:
        return False
    import time
    time.sleep(wait_s)
    return True


def pid_exe(pid):
    """Executable path for a pid, or "" if unknowable. Works across integrity
    levels: PROCESS_QUERY_LIMITED_INFORMATION (0x1000) is granted on an
    ELEVATED target from medium integrity (NO_WRITE_UP blocks writes, not
    reads), so the foreground-stolen refusal can NAME the offender — an
    empty-title elevated window left two esa runs saying only \"''\"
    (2026-07-20), and no remote session can enumerate another session's
    windows to identify it after the fact."""
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.c_ulong(1024)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
            return ""
        finally:
            k.CloseHandle(h)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        return ""


def foreground_pid():
    """PID owning the foreground window, or 0 if unknowable."""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        return 0


def foreground_window():
    """(title, elevated) for whatever owns the foreground; (None, False) if
    unknowable. `elevated` = the owner runs at HIGHER integrity than us, the
    case that matters: UIPI forbids raising the game above it, so the harness
    cannot recover and input is refused at the first click. NOTE: an ssh
    session has a non-interactive window station and sees NO foreground at
    all — validate desktop behaviour via an Interactive scheduled task
    (validate_elevated_refusal.py --via-task)."""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None, False
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return buf.value, False
        return buf.value, pid_is_higher_integrity(pid.value)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        return None, False


def own_process_tree():
    """PIDs of this process and its ancestors, via a toolhelp snapshot — NOT
    wmic, which is absent on current Windows 11 builds. Used to allowlist our
    own console/app from foreground-stealer logic: on an interactive laptop
    the harness's own parent (the Claude app) is the usual persistent
    foreground window, and treating it as a stealer would refuse every run.
    Best-effort: an empty set only weakens an allowlist, never refuses."""
    pids = set()
    try:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_ulong),
                        ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", ctypes.c_ulong),
                        ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_char * 260)]

        k = ctypes.windll.kernel32
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return pids
        parent = {}
        try:
            e = PROCESSENTRY32()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32)
            ok = k.Process32First(snap, ctypes.byref(e))
            while ok:
                parent[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
                ok = k.Process32Next(snap, ctypes.byref(e))
        finally:
            k.CloseHandle(snap)
        pid, seen = os.getpid(), 0
        while pid and pid not in pids and seen < 12:
            pids.add(pid)
            seen += 1
            pid = parent.get(pid, 0)
    except Exception:                       # noqa: BLE001 - probe, never fatal
        pass
    return pids
