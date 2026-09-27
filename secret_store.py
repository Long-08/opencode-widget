#!/usr/bin/env python3
"""Phase 2B: encrypted secret storage abstraction.

Secrets (``api_key`` and ``server.auth_cookie``) are kept out of config.json in
a small encrypted file. On Windows the default backend is DPAPI
(``CryptProtectData`` / ``CryptUnprotectData``), which binds the ciphertext to
the current Windows user account. On other platforms no backend is available
and callers fall back to plaintext config for backward compatibility.

The file format is JSON::

    {"v": 1, "data": "<base64 of backend.encrypt(json payload)>"}

Nothing in this module ever logs or returns a secret other than through the
explicit ``load_secrets`` API.
"""
import base64
import ctypes
import json
import os
import tempfile

__all__ = [
    "SecretStoreError",
    "SecretBackend",
    "DPAPIBackend",
    "set_backend",
    "get_backend",
    "reset_backend",
    "load_secrets",
    "save_secrets",
    "store_available",
    "secret_file_path",
]

_VERSION = 1
_SECRET_FILE = "secrets.enc"
_ENV_VAR = "OPENCODE_WIDGET_SECRET_FILE"


class SecretStoreError(Exception):
    """Raised when the encrypted secret store cannot be read or written."""


class SecretBackend:
    """Interface for a symmetric secret backend."""

    def encrypt(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def decrypt(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError


if os.name == "nt":

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def _make_blob(data: bytes):
        """Build a DATA_BLOB + backing buffer (kept alive by the caller)."""
        buf = ctypes.create_string_buffer(bytes(data), len(data) + 1)
        blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    class DPAPIBackend(SecretBackend):
        """Windows DPAPI backend bound to the current user (no entropy)."""

        def __init__(self):
            if os.name != "nt":
                raise SecretStoreError("DPAPI is only available on Windows")
            self._crypt32 = ctypes.windll.crypt32
            self._kernel32 = ctypes.windll.kernel32
            self._crypt32.CryptProtectData.restype = ctypes.c_int
            self._crypt32.CryptProtectData.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ]
            self._crypt32.CryptUnprotectData.restype = ctypes.c_int
            self._crypt32.CryptUnprotectData.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ]
            self._kernel32.LocalFree.restype = ctypes.c_void_p
            self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        def encrypt(self, data: bytes) -> bytes:
            in_blob, _keep = _make_blob(data)
            out_blob = _DATA_BLOB()
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(in_blob), None, None, None, None,
                CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
            )
            if not ok:
                raise SecretStoreError(
                    "CryptProtectData failed (err %d)" % ctypes.get_last_error()
                )
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                self._kernel32.LocalFree(out_blob.pbData)

        def decrypt(self, data: bytes) -> bytes:
            in_blob, _keep = _make_blob(data)
            out_blob = _DATA_BLOB()
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None,
                CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
            )
            if not ok:
                raise SecretStoreError(
                    "CryptUnprotectData failed (err %d)" % ctypes.get_last_error()
                )
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                self._kernel32.LocalFree(out_blob.pbData)

else:

    class DPAPIBackend(SecretBackend):
        """Placeholder on non-Windows: no DPAPI support, encryption unavailable."""

        def __init__(self):
            raise SecretStoreError("DPAPI is only available on Windows")


# ---------------------------------------------------------------------------
# backend state
# ---------------------------------------------------------------------------
_backend = None
_initialized = False


def _default_backend():
    if os.name == "nt":
        try:
            return DPAPIBackend()
        except Exception:
            return None
    return None


def get_backend():
    """Return the active backend (lazily initialising the platform default)."""
    global _backend, _initialized
    if not _initialized:
        _backend = _default_backend()
        _initialized = True
    return _backend


def set_backend(backend):
    """Install an explicit backend (None disables storage)."""
    global _backend, _initialized
    _backend = backend
    _initialized = True


def reset_backend():
    """Drop the explicit backend so the platform default applies again."""
    global _backend, _initialized
    _backend = None
    _initialized = False


def store_available() -> bool:
    return get_backend() is not None


def secret_file_path() -> str:
    """Resolve the secret file path (env override, else alongside this module)."""
    override = os.environ.get(_ENV_VAR)
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _SECRET_FILE)


# ---------------------------------------------------------------------------
# storage API
# ---------------------------------------------------------------------------
def load_secrets() -> dict:
    """Return the stored secrets mapping.

    A missing file yields ``{}``. A corrupt/unreadable file or a decrypt
    failure raises :class:`SecretStoreError`; the message never contains
    secret material.
    """
    path = secret_file_path()
    if not os.path.exists(path):
        return {}
    backend = get_backend()
    if backend is None:
        raise SecretStoreError("no secret backend available")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            wrapper = json.load(fh)
        if not isinstance(wrapper, dict) or wrapper.get("v") != _VERSION:
            raise SecretStoreError("unsupported secret store format")
        raw = base64.b64decode(wrapper.get("data") or "")
        payload = backend.decrypt(raw)
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise SecretStoreError("secret store payload is not an object")
        return data
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreError(
            "secret store read failed (%s)" % type(exc).__name__
        ) from exc


def save_secrets(secrets: dict) -> None:
    """Atomically write ``secrets`` to the encrypted store."""
    if not isinstance(secrets, dict):
        raise SecretStoreError("secrets must be a dict")
    backend = get_backend()
    if backend is None:
        raise SecretStoreError("no secret backend available")
    path = secret_file_path()
    tmp = None
    try:
        payload = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
        wrapper = {
            "v": _VERSION,
            "data": base64.b64encode(backend.encrypt(payload)).decode("ascii"),
        }
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".secrets-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(wrapper, fh)
        os.replace(tmp, path)
        tmp = None
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except SecretStoreError:
        raise
    except Exception as exc:
        raise SecretStoreError(
            "secret store write failed (%s)" % type(exc).__name__
        ) from exc
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
