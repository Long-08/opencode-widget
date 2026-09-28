// Phase 2.1: runtime.json access for the main process.
// Plain Node module (no electron import) so it can be unit-tested with node:test.
//
// The local API server writes {token, port, pid, created} to
// <runtimeDir>/runtime.json after startup. The renderer never sees it: the main
// process reads it here, keeps the token, and proxies all /api/* calls.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const RUNTIME_FILE = 'runtime.json';

function runtimeDir(env) {
  const e = env || process.env;
  return e.OPENCODE_WIDGET_RUNTIME_DIR || path.join(os.tmpdir(), 'opencode-widget');
}

function runtimeFilePath(env) {
  return path.join(runtimeDir(env), RUNTIME_FILE);
}

// process.kill(pid, 0) probes liveness without sending a signal.
// EPERM means the process exists but belongs to another user -> alive.
function isPidAlive(pid) {
  if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return !!(e && e.code === 'EPERM');
  }
}

// Read runtime.json once.
// Returns {ok:true, base, token, pid, created} or
// {ok:false, reason:'missing'|'invalid'|'stale'} (stale = pid no longer alive).
// opts.isAlive is injectable for tests.
function readRuntimeOnce(env, opts) {
  const isAlive = (opts && opts.isAlive) || isPidAlive;
  let raw;
  try {
    raw = fs.readFileSync(runtimeFilePath(env), 'utf8');
  } catch (_) {
    return { ok: false, reason: 'missing' };
  }
  let j;
  try {
    j = JSON.parse(raw);
  } catch (_) {
    return { ok: false, reason: 'invalid' };
  }
  if (!j || typeof j !== 'object') return { ok: false, reason: 'invalid' };
  const token = typeof j.token === 'string' ? j.token : '';
  const port = j.port;
  const pid = j.pid;
  const validPort = Number.isInteger(port) && port > 0 && port <= 65535;
  const validPid = Number.isInteger(pid) && pid > 0;
  if (!token || !validPort || !validPid) return { ok: false, reason: 'invalid' };
  if (!isAlive(pid)) return { ok: false, reason: 'stale' };
  return {
    ok: true,
    base: 'http://127.0.0.1:' + port,
    token,
    pid,
    created: typeof j.created === 'number' ? j.created : 0,
  };
}

module.exports = { runtimeDir, runtimeFilePath, isPidAlive, readRuntimeOnce };
