// Phase 2.1: unit tests for electron/runtime_env.js (main-process runtime.json reader).
// Plain node:test + node:assert, no dependencies. Run from the project root:
//   node --test tests/js/runtime_env.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const runtimeEnv = require(path.resolve(__dirname, '..', '..', 'electron', 'runtime_env.js'));

function makeTempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'widget-runtime-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function writeRuntime(dir, obj) {
  fs.writeFileSync(path.join(dir, 'runtime.json'), JSON.stringify(obj), 'utf8');
}

function envFor(dir) {
  return { OPENCODE_WIDGET_RUNTIME_DIR: dir };
}

test('runtimeDir/runtimeFilePath honour the env override and default to os.tmpdir()', () => {
  const override = { OPENCODE_WIDGET_RUNTIME_DIR: path.join('C:', 'custom-runtime') };
  assert.equal(runtimeEnv.runtimeDir(override), override.OPENCODE_WIDGET_RUNTIME_DIR);
  assert.equal(
    runtimeEnv.runtimeFilePath(override),
    path.join(override.OPENCODE_WIDGET_RUNTIME_DIR, 'runtime.json')
  );

  const fallback = runtimeEnv.runtimeDir({});
  assert.equal(fallback, path.join(os.tmpdir(), 'opencode-widget'));
  assert.equal(runtimeEnv.runtimeFilePath({}), path.join(fallback, 'runtime.json'));
});

test('fresh runtime file -> ok with base/token/pid/created', (t) => {
  const dir = makeTempDir(t);
  writeRuntime(dir, { token: 'tok-fresh', port: 43210, pid: process.pid, created: 123456 });

  const r = runtimeEnv.readRuntimeOnce(envFor(dir));
  assert.equal(r.ok, true);
  assert.equal(r.base, 'http://127.0.0.1:43210');
  assert.equal(r.token, 'tok-fresh');
  assert.equal(r.pid, process.pid);
  assert.equal(r.created, 123456);
});

test('dead pid -> stale (injected isAlive=false)', (t) => {
  const dir = makeTempDir(t);
  writeRuntime(dir, { token: 'tok-stale', port: 43211, pid: 424242, created: 1 });

  const r = runtimeEnv.readRuntimeOnce(envFor(dir), { isAlive: () => false });
  assert.deepEqual(r, { ok: false, reason: 'stale' });
});

test('missing file -> missing', (t) => {
  const dir = makeTempDir(t);
  const r = runtimeEnv.readRuntimeOnce(envFor(dir));
  assert.deepEqual(r, { ok: false, reason: 'missing' });
});

test('corrupt JSON -> invalid', (t) => {
  const dir = makeTempDir(t);
  fs.writeFileSync(path.join(dir, 'runtime.json'), '{not json', 'utf8');
  const r = runtimeEnv.readRuntimeOnce(envFor(dir));
  assert.deepEqual(r, { ok: false, reason: 'invalid' });
});

test('missing/empty token -> invalid', (t) => {
  const dir = makeTempDir(t);
  writeRuntime(dir, { token: '', port: 43212, pid: process.pid, created: 1 });
  assert.deepEqual(runtimeEnv.readRuntimeOnce(envFor(dir)), { ok: false, reason: 'invalid' });

  writeRuntime(dir, { port: 43212, pid: process.pid, created: 1 });
  assert.deepEqual(runtimeEnv.readRuntimeOnce(envFor(dir)), { ok: false, reason: 'invalid' });
});

test('missing/invalid port -> invalid', (t) => {
  const dir = makeTempDir(t);
  writeRuntime(dir, { token: 'tok', pid: process.pid, created: 1 });
  assert.deepEqual(runtimeEnv.readRuntimeOnce(envFor(dir)), { ok: false, reason: 'invalid' });

  writeRuntime(dir, { token: 'tok', port: 0, pid: process.pid, created: 1 });
  assert.deepEqual(runtimeEnv.readRuntimeOnce(envFor(dir)), { ok: false, reason: 'invalid' });

  writeRuntime(dir, { token: 'tok', port: 70000, pid: process.pid, created: 1 });
  assert.deepEqual(runtimeEnv.readRuntimeOnce(envFor(dir)), { ok: false, reason: 'invalid' });
});

test('isPidAlive: the current process is alive', () => {
  assert.equal(runtimeEnv.isPidAlive(process.pid), true);
  assert.equal(runtimeEnv.isPidAlive(0), false);
  assert.equal(runtimeEnv.isPidAlive('1234'), false);
});

test('isPidAlive: EPERM counts as alive, other errors count as dead', () => {
  const original = process.kill;
  try {
    process.kill = () => {
      const err = new Error('operation not permitted');
      err.code = 'EPERM';
      throw err;
    };
    assert.equal(runtimeEnv.isPidAlive(1234), true);

    process.kill = () => {
      const err = new Error('no such process');
      err.code = 'ESRCH';
      throw err;
    };
    assert.equal(runtimeEnv.isPidAlive(1234), false);
  } finally {
    process.kill = original;
  }
});
