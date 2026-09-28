// Phase 6A: lifecycle helpers for the Electron main process.
//
// Plain Node module: it must NOT require('electron') and must not depend on the
// DOM so it can be unit-tested with node:test (see tests/js/runtime_manager.test.js).
// main.js uses these helpers to keep the shutdown/heartbeat/debug logic small and
// to make the "owned server" decision explicit and testable.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const RUNTIME_DIR_NAME = 'opencode-widget';
const RUNTIME_FILE = 'runtime.json';
const DEFAULT_MAX_LOG_BYTES = 1024 * 1024; // ~1 MB, one backup only
const HEARTBEAT_PATH = '/api/state?heartbeat=1';

// env OPENCODE_WIDGET_RUNTIME_DIR wins; otherwise os.tmpdir()/opencode-widget.
// An explicit dir argument (tests) overrides both.
function runtimeFilePath(dir) {
  if (dir) return path.join(dir, RUNTIME_FILE);
  const override = process.env.OPENCODE_WIDGET_RUNTIME_DIR;
  const base = override || path.join(os.tmpdir(), RUNTIME_DIR_NAME);
  return path.join(base, RUNTIME_FILE);
}

// Read + parse runtime.json once. Returns the parsed object or null on
// missing / unreadable / corrupt / non-object content (never throws).
function readRuntimeInfo(dir) {
  let raw;
  try {
    raw = fs.readFileSync(runtimeFilePath(dir), 'utf8');
  } catch (_) {
    return null;
  }
  let info;
  try {
    info = JSON.parse(raw);
  } catch (_) {
    return null;
  }
  if (!info || typeof info !== 'object' || Array.isArray(info)) return null;
  return info;
}

// OPENCODE_WIDGET_DEBUG=1|true (case-insensitive, trimmed) enables diagnostics.
function debugEnabled(env) {
  const e = env || process.env;
  const value = e && e.OPENCODE_WIDGET_DEBUG;
  if (typeof value !== 'string') return false;
  const normalized = value.trim().toLowerCase();
  return normalized === '1' || normalized === 'true';
}

// Rotate the snap log when it exceeds maxBytes (default ~1 MB).
function shouldRotateLog(sizeBytes, maxBytes) {
  const max =
    typeof maxBytes === 'number' && Number.isFinite(maxBytes) && maxBytes > 0
      ? maxBytes
      : DEFAULT_MAX_LOG_BYTES;
  return typeof sizeBytes === 'number' && Number.isFinite(sizeBytes) && sizeBytes > max;
}

// True when info identifies a server (non-secret token + numeric pid) and its
// pid equals the given pid. Used to decide if runtime.json still points at the
// server we probed before unlinking it.
function ownedServerMatches(info, pid) {
  if (!info || typeof info !== 'object') return false;
  if (typeof info.token !== 'string' || info.token === '') return false;
  if (typeof info.pid !== 'number' || !Number.isInteger(info.pid) || info.pid <= 0) return false;
  return info.pid === pid;
}

// True only when runtime.json carries a token + pid AND the authenticated probe
// succeeded. Never kill a process we cannot positively identify as ours.
function shouldTerminateOwnedServer(info, probeOk) {
  if (!info || typeof info !== 'object') return false;
  if (typeof info.token !== 'string' || info.token === '') return false;
  if (typeof info.pid !== 'number' || !Number.isInteger(info.pid) || info.pid <= 0) return false;
  return !!probeOk;
}

function heartbeatPath() {
  return HEARTBEAT_PATH;
}

module.exports = {
  runtimeFilePath,
  readRuntimeInfo,
  debugEnabled,
  shouldRotateLog,
  ownedServerMatches,
  shouldTerminateOwnedServer,
  heartbeatPath,
  DEFAULT_MAX_LOG_BYTES,
};
