// Phase 6A: unit tests for electron/runtime_manager.js (main-process lifecycle helpers).
// Plain node:test + node:assert, no dependencies. Run from the project root:
//   node --test tests/js/runtime_manager.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const rm = require(path.resolve(__dirname, '..', '..', 'electron', 'runtime_manager.js'));

function makeTempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'widget-runtime-manager-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('runtimeFilePath honours the explicit dir argument', () => {
  const dir = path.join('C:', 'explicit-runtime');
  assert.equal(rm.runtimeFilePath(dir), path.join(dir, 'runtime.json'));
});

test('runtimeFilePath honours OPENCODE_WIDGET_RUNTIME_DIR then falls back to os.tmpdir()', () => {
  const previous = process.env.OPENCODE_WIDGET_RUNTIME_DIR;
  try {
    process.env.OPENCODE_WIDGET_RUNTIME_DIR = path.join('C:', 'env-runtime');
    assert.equal(rm.runtimeFilePath(), path.join('C:', 'env-runtime', 'runtime.json'));
    delete process.env.OPENCODE_WIDGET_RUNTIME_DIR;
    assert.equal(
      rm.runtimeFilePath(),
      path.join(os.tmpdir(), 'opencode-widget', 'runtime.json')
    );
  } finally {
    if (previous === undefined) delete process.env.OPENCODE_WIDGET_RUNTIME_DIR;
    else process.env.OPENCODE_WIDGET_RUNTIME_DIR = previous;
  }
});

test('readRuntimeInfo returns the parsed object for a fresh file', (t) => {
  const dir = makeTempDir(t);
  fs.writeFileSync(
    path.join(dir, 'runtime.json'),
    JSON.stringify({ token: 'tok', port: 43210, pid: 1234, instance_id: 'ab' }),
    'utf8'
  );
  const info = rm.readRuntimeInfo(dir);
  assert.deepEqual(info, { token: 'tok', port: 43210, pid: 1234, instance_id: 'ab' });
});

test('readRuntimeInfo returns null for a missing file', (t) => {
  const dir = makeTempDir(t);
  assert.equal(rm.readRuntimeInfo(dir), null);
});

test('readRuntimeInfo returns null for corrupt JSON', (t) => {
  const dir = makeTempDir(t);
  fs.writeFileSync(path.join(dir, 'runtime.json'), '{not json', 'utf8');
  assert.equal(rm.readRuntimeInfo(dir), null);
});

test('readRuntimeInfo returns null for non-object JSON values', (t) => {
  const dir = makeTempDir(t);
  assert.equal(rm.readRuntimeInfo(dir), null); // missing
  fs.writeFileSync(path.join(dir, 'runtime.json'), '[]', 'utf8');
  assert.equal(rm.readRuntimeInfo(dir), null);
  fs.writeFileSync(path.join(dir, 'runtime.json'), '"a string"', 'utf8');
  assert.equal(rm.readRuntimeInfo(dir), null);
});

test('debugEnabled parses 1/true case-insensitively and nothing else', () => {
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: '1' }), true);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: 'true' }), true);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: 'TRUE' }), true);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: '  True  ' }), true);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: '0' }), false);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: 'yes' }), false);
  assert.equal(rm.debugEnabled({ OPENCODE_WIDGET_DEBUG: '' }), false);
  assert.equal(rm.debugEnabled({}), false);
  assert.equal(rm.debugEnabled(), false); // falls back to process.env (unset here)
});

test('shouldRotateLog uses a ~1MB default threshold', () => {
  assert.equal(rm.DEFAULT_MAX_LOG_BYTES, 1024 * 1024);
  assert.equal(rm.shouldRotateLog(rm.DEFAULT_MAX_LOG_BYTES - 1), false);
  assert.equal(rm.shouldRotateLog(rm.DEFAULT_MAX_LOG_BYTES), false); // does not exceed
  assert.equal(rm.shouldRotateLog(rm.DEFAULT_MAX_LOG_BYTES + 1), true);
});

test('shouldRotateLog honours a custom max and ignores bad input', () => {
  assert.equal(rm.shouldRotateLog(101, 100), true);
  assert.equal(rm.shouldRotateLog(100, 100), false);
  assert.equal(rm.shouldRotateLog(50, 100), false);
  assert.equal(rm.shouldRotateLog(1000, 0), false); // 0 falls back to default
  assert.equal(rm.shouldRotateLog(Number.NaN, 100), false);
  assert.equal(rm.shouldRotateLog('100', 100), false);
});

test('ownedServerMatches requires token + pid and an exact pid match', () => {
  assert.equal(rm.ownedServerMatches({ token: 't', pid: 42 }, 42), true);
  assert.equal(rm.ownedServerMatches({ token: 't', pid: 42 }, 43), false);
  assert.equal(rm.ownedServerMatches({ pid: 42 }, 42), false); // no token
  assert.equal(rm.ownedServerMatches({ token: 't' }, 42), false); // no pid
  assert.equal(rm.ownedServerMatches({ token: '', pid: 42 }, 42), false);
  assert.equal(rm.ownedServerMatches(null, 42), false);
});

test('shouldTerminateOwnedServer requires token + pid and a successful probe', () => {
  assert.equal(rm.shouldTerminateOwnedServer({ token: 't', pid: 42 }, true), true);
  assert.equal(rm.shouldTerminateOwnedServer({ token: 't', pid: 42 }, false), false);
  assert.equal(rm.shouldTerminateOwnedServer({ token: 't' }, true), false); // no pid
  assert.equal(rm.shouldTerminateOwnedServer({ pid: 42 }, true), false); // no token
  assert.equal(rm.shouldTerminateOwnedServer({ token: '', pid: 42 }, true), false);
  assert.equal(rm.shouldTerminateOwnedServer(null, true), false);
});

test('heartbeatPath is the authenticated heartbeat route', () => {
  assert.equal(rm.heartbeatPath(), '/api/state?heartbeat=1');
});
