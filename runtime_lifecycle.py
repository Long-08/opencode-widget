#!/usr/bin/env python3
"""Phase 6A: small, pure lifecycle helpers.

Shared by ``data_server`` and ``browser_cookie``. Everything here is
side-effect free except ``stale_files`` (and the filesystem helpers it powers),
which are best-effort and never raise.

Security notes:
  * ``pid_alive`` never calls ``os.kill`` on Windows; it uses
    ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``CloseHandle`` and
    treats any failure as "not alive".
  * The ownership marker helpers are intentionally narrow so stale-file cleanup
    can never touch unrelated user files.
"""
import ctypes
import os

# Win32: open a handle just to query liveness (never to terminate/read memory).
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Filesystem ownership markers for our own temp copies. Keep in sync with
# browser_cookie's naming; cleanup must match these exactly.
COOKIE_TEMP_PREFIX = "opencode-widget-cookie-"


def new_instance_id():
    """Return a fresh, non-secret 128-bit process instance id (hex)."""
    return os.urandom(16).hex()


def pid_alive(pid, _impl=None):
    """Return True if ``pid`` currently names a live process.

    Invalid/None/non-positive pid -> False. ``_impl`` is an injectable
    callable ``_impl(pid) -> bool`` used by tests; when omitted the Win32
    ``OpenProcess``/``CloseHandle`` path is used. Never raises.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if _impl is not None:
        try:
            return bool(_impl(pid))
        except Exception:
            return False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            return True
        finally:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
    except Exception:
        return False


def should_exit_idle(now_s, last_auth_s, timeout_s):
    """True when the server has seen auth traffic and has since gone idle.

    ``last_auth_s is None`` (no authenticated request yet) or a non-positive
    ``timeout_s`` (feature disabled) always yields False.
    """
    if last_auth_s is None:
        return False
    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        return False
    if timeout <= 0:
        return False
    try:
        return (float(now_s) - float(last_auth_s)) > timeout
    except (TypeError, ValueError):
        return False


def classify_port_conflict(runtime_info, probe_ok):
    """Classify who owns an occupied port.

    ``"existing_widget"`` only when the health probe succeeded AND the runtime
    file carries both a token and a pid (i.e. it really is our server).
    Anything else (missing/corrupt info, failed probe) is ``"foreign"`` — we
    must never kill another process.
    """
    if not probe_ok or not isinstance(runtime_info, dict):
        return "foreign"
    if runtime_info.get("token") and runtime_info.get("pid"):
        return "existing_widget"
    return "foreign"


def is_owned_cookie_temp_name(name):
    """True when ``name`` carries our cookie-temp ownership marker."""
    return isinstance(name, str) and name.startswith(COOKIE_TEMP_PREFIX)


def stale_files(dir_path, matcher, max_age_s, now_s, _listdir=None):
    """Best-effort list of matching files older than ``max_age_s``.

    ``matcher(name) -> bool`` selects candidates; ``_listdir`` is an injectable
    listing function used by tests. A missing/unreadable directory yields ``[]``.
    Any per-entry failure is skipped; this function never raises.
    """
    if _listdir is not None:
        try:
            names = _listdir(dir_path)
        except Exception:
            return []
    else:
        try:
            names = os.listdir(dir_path)
        except Exception:
            return []
    out = []
    for name in names or []:
        try:
            if not matcher(name):
                continue
            path = os.path.join(dir_path, name)
            age = now_s - os.path.getmtime(path)
            if age > max_age_s:
                out.append(path)
        except Exception:
            continue
    return out
