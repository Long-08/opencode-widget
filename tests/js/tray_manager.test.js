// Phase 6B: unit tests for electron/tray_manager.js.
// Every Electron touch-point (Tray / Menu / nativeImage) is an injected fake, so
// no Electron is required:
//   node --test tests/js/tray_manager.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const tm = require(path.resolve(__dirname, '..', '..', 'electron', 'tray_manager.js'));

function makeTrayClass(instances) {
  return class FakeTray {
    constructor(icon) {
      this.icon = icon;
      this.tooltip = null;
      this.menu = null;
      this.destroyed = false;
      instances.push(this);
    }
    setToolTip(t) { this.tooltip = t; }
    setContextMenu(m) { this.menu = m; }
    destroy() { this.destroyed = true; }
  };
}

const menu = { buildFromTemplate: (template) => template };

function imageOk() {
  return { isEmpty: () => false };
}

function imageEmpty() {
  return { isEmpty: () => true };
}

function deps(overrides) {
  const instances = [];
  const base = {
    Tray: makeTrayClass(instances),
    Menu: menu,
    nativeImage: { createFromPath: () => imageOk() },
    iconPath: 'electron/assets/tray.png',
    notificationsEnabled: false,
    handlers: {},
    _instances: instances,
  };
  return Object.assign(base, overrides || {});
}

// ---- menu template ---------------------------------------------------------

test('buildMenuTemplate has exactly the five ordered items', () => {
  const template = tm.buildMenuTemplate({ notificationsEnabled: false }, {});
  assert.equal(template.length, 5);
  assert.deepEqual(
    template.map((item) => item.label),
    ['Show Widget', 'Refresh', 'Notifications:', undefined, 'Quit']
  );
  assert.deepEqual(
    template.map((item) => item.type || null),
    [null, null, 'checkbox', 'separator', null]
  );
});

test('the notifications item is a checkbox reflecting notificationsEnabled', () => {
  const on = tm.buildMenuTemplate({ notificationsEnabled: true }, {});
  const off = tm.buildMenuTemplate({ notificationsEnabled: false }, {});
  assert.equal(on[2].type, 'checkbox');
  assert.equal(on[2].checked, true);
  assert.equal(off[2].type, 'checkbox');
  assert.equal(off[2].checked, false);
  // Missing opts default to off.
  assert.equal(tm.buildMenuTemplate(undefined, {})[2].checked, false);
});

test('each menu item invokes its matching handler', () => {
  const calls = [];
  const handlers = {
    show: () => calls.push('show'),
    refresh: () => calls.push('refresh'),
    toggle: () => calls.push('toggle'),
    quit: () => calls.push('quit'),
  };
  const template = tm.buildMenuTemplate({ notificationsEnabled: true }, handlers);
  template[0].click();
  template[1].click();
  template[2].click();
  template[4].click();
  assert.deepEqual(calls, ['show', 'refresh', 'toggle', 'quit']);
});

test('a missing handler is a no-op (never throws)', () => {
  const template = tm.buildMenuTemplate({ notificationsEnabled: false }, {});
  for (const item of template) {
    if (typeof item.click === 'function') item.click();
  }
  // also a handler that throws must not escape
  const throwing = tm.buildMenuTemplate({ notificationsEnabled: false }, {
    show: () => { throw new Error('boom'); },
  });
  assert.doesNotThrow(() => throwing[0].click());
});

// ---- createTray ------------------------------------------------------------

test('createTray sets the tooltip and builds the menu', (t) => {
  t.after(() => tm._resetTrayForTest());
  tm._resetTrayForTest();
  const d = deps({ notificationsEnabled: true, handlers: {} });
  const tray = tm.createTray(d);
  assert.ok(tray);
  assert.equal(d._instances.length, 1);
  assert.equal(tray.tooltip, 'OpenCode Widget');
  assert.ok(Array.isArray(tray.menu), 'context menu was not built from the template');
  assert.equal(tray.menu[2].checked, true);
});

test('createTray creates at most one tray (second call returns null)', (t) => {
  t.after(() => tm._resetTrayForTest());
  tm._resetTrayForTest();
  const first = deps({});
  const second = deps({});
  const tray = tm.createTray(first);
  assert.ok(tray);
  assert.equal(tm.createTray(second), null);
  assert.equal(first._instances.length, 1);
  assert.equal(second._instances.length, 0, 'a second tray must never be constructed');
});

test('createTray returns null when the icon is missing or empty', (t) => {
  t.after(() => tm._resetTrayForTest());
  tm._resetTrayForTest();
  const empty = deps({ nativeImage: { createFromPath: () => imageEmpty() } });
  assert.equal(tm.createTray(empty), null);
  assert.equal(empty._instances.length, 0);

  tm._resetTrayForTest();
  const noPath = deps({ iconPath: '' });
  assert.equal(tm.createTray(noPath), null);
  assert.equal(noPath._instances.length, 0);

  tm._resetTrayForTest();
  const throwing = deps({ nativeImage: { createFromPath: () => { throw new Error('nope'); } } });
  assert.equal(tm.createTray(throwing), null);
});

test('createTray menu handlers are wired through to the injected handlers', (t) => {
  t.after(() => tm._resetTrayForTest());
  tm._resetTrayForTest();
  const calls = [];
  const d = deps({ handlers: { quit: () => calls.push('quit') } });
  const tray = tm.createTray(d);
  assert.ok(tray);
  tray.menu[4].click();
  assert.deepEqual(calls, ['quit']);
});
