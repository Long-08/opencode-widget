#!/usr/bin/env python3
"""Phase 7: resolve mutable data locations outside the (read-only) install dir.

The application is expected to run from a read-only install directory: nothing
under the directory that contains this module may be written at runtime. All
mutable state (config, encrypted secrets, local databases, formula cache) lives
in a per-user data directory instead.

This module is intentionally stdlib-only and side-effect-light: importing it
performs no I/O. The helper functions resolve paths on demand so tests can
redirect everything through ``OPENCODE_WIDGET_DATA_DIR``.

Public API
----------
``app_dir()``               directory that contains this module (the install dir)
``data_dir()``              per-user mutable-data directory (created on demand)
``config_path()``           ``<data_dir>/config.json``
``secrets_path()``          ``<data_dir>/secrets.enc``
``usage_remote_db_path()``  ``<data_dir>/usage_remote.db``
``server_usage_db_path()``  ``<data_dir>/server_usage.db``
``formula_cache_path()``    ``<data_dir>/formula_cache.json``
``app_version()``           version from ``electron/package.json`` (or "0.0.0")
``migrate_legacy_data()``   best-effort, idempotent copy of legacy in-repo data
"""
import json
import os

__all__ = [
    "app_dir",
    "data_dir",
    "config_path",
    "secrets_path",
    "usage_remote_db_path",
    "server_usage_db_path",
    "formula_cache_path",
    "app_version",
    "migrate_legacy_data",
]

# Environment variable that overrides the user data directory (used by tests and
# by portable / embedded deployments).
DATA_DIR_ENV = "OPENCODE_WIDGET_DATA_DIR"

# Per-user fallback directory name here and (implicitly) in %APPDATA%.
APP_DIRNAME = "opencode-widget"

# Legacy files that used to live next to the code and are copied into the data
# directory on first run. Order is stable so summaries are deterministic.
LEGACY_FILES = ("config.json", "secrets.enc", "usage_remote.db", "server_usage.db")

_CONFIG_NAME = "config.json"
_SECRETS_NAME = "secrets.enc"
_USAGE_REMOTE_DB_NAME = "usage_remote.db"
_SERVER_USAGE_DB_NAME = "server_usage.db"
_FORMULA_CACHE_NAME = "formula_cache.json"


def app_dir():
    """Return the directory containing this module (the install directory)."""
    return os.path.dirname(os.path.abspath(__file__))


def _default_data_dir():
    """Platform default for the per-user data directory (no env override)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_DIRNAME)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, APP_DIRNAME)
    return os.path.join(os.path.expanduser("~"), ".local", "share", APP_DIRNAME)


def data_dir():
    """Return the mutable-data directory, creating it if necessary.

    ``OPENCODE_WIDGET_DATA_DIR`` wins when set; otherwise the platform default
    (``%APPDATA%\\opencode-widget`` on Windows, ``$XDG_DATA_HOME`` / ``~/.local/
    share`` elsewhere) is used.
    """
    override = os.environ.get(DATA_DIR_ENV)
    directory = os.path.abspath(override) if override else _default_data_dir()
    os.makedirs(directory, exist_ok=True)
    return directory


def config_path():
    """Path of the JSON config file inside :func:`data_dir`."""
    return os.path.join(data_dir(), _CONFIG_NAME)


def secrets_path():
    """Path of the encrypted secret store inside :func:`data_dir`."""
    return os.path.join(data_dir(), _SECRETS_NAME)


def usage_remote_db_path():
    """Path of the scraped remote-usage SQLite DB inside :func:`data_dir`."""
    return os.path.join(data_dir(), _USAGE_REMOTE_DB_NAME)


def server_usage_db_path():
    """Path of the local server-usage SQLite DB inside :func:`data_dir`."""
    return os.path.join(data_dir(), _SERVER_USAGE_DB_NAME)


def formula_cache_path():
    """Path of the formula cache inside :func:`data_dir`."""
    return os.path.join(data_dir(), _FORMULA_CACHE_NAME)


def app_version():
    """Return the Electron app version, or ``"0.0.0"`` on any failure.

    Reads ``electron/package.json`` relative to :func:`app_dir`. Never raises.
    """
    try:
        package = os.path.join(app_dir(), "electron", "package.json")
        with open(package, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except Exception:
        pass
    return "0.0.0"


def migrate_legacy_data():
    """Copy legacy in-repo data files into :func:`data_dir` (best effort).

    For every file in :data:`LEGACY_FILES` that exists in :func:`app_dir` but
    not in :func:`data_dir`, a copy is made, read back and byte-compared before
    being committed. Existing data-directory files are never overwritten and the
    source files are always left untouched.

    Returns a non-sensitive summary ``{"migrated": [names], "skipped": [names]}``.
    Any failure is swallowed: this function never raises.
    """
    summary = {"migrated": [], "skipped": []}
    try:
        source_dir = app_dir()
        target_dir = data_dir()
    except Exception:
        return summary

    for name in LEGACY_FILES:
        tmp = None
        try:
            source = os.path.join(source_dir, name)
            target = os.path.join(target_dir, name)
            if not os.path.isfile(source):
                continue
            if os.path.exists(target):
                # Never overwrite data the app already owns.
                summary["skipped"].append(name)
                continue
            with open(source, "rb") as fh:
                data = fh.read()
            tmp = target + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            # Verify the copy is readable and byte-for-byte identical before
            # committing it into place.
            with open(tmp, "rb") as fh:
                copied = fh.read()
            if len(copied) != len(data) or copied != data:
                raise OSError("legacy migration verification failed")
            os.replace(tmp, target)
            tmp = None
            summary["migrated"].append(name)
        except Exception:
            summary["skipped"].append(name)
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    return summary
