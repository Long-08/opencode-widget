// Phase 6B: minimal system tray for the widget.
//
// Plain Node module with every Electron touch-point injected (Tray / Menu /
// nativeImage / iconPath / handlers), so the menu template and the create-once
// behaviour are unit-testable without launching Electron
// (see tests/js/tray_manager.test.js).
//
// Scope is deliberately tiny: show / refresh / toggle notifications / quit.
// The tray never resizes the window, never opens an external URL and never
// touches the runtime token.
'use strict';

const TRAY_TOOLTIP = 'OpenCode Widget';

// Exact, ordered menu labels. Kept as named constants so the static guard can
// assert them without executing the module.
const MENU_LABELS = {
  show: 'Show Widget',
  refresh: 'Refresh',
  notifications: 'Notifications:',
  quit: 'Quit',
};

// Module-level handle so a tray is constructed at most once for the lifetime of
// the main process. main.js also keeps its own reference for teardown.
let currentTray = null;

function safeInvoke(fn) {
  if (typeof fn !== 'function') return;
  try {
    fn();
  } catch (_) {
    // A failing menu action must never take down the main process.
  }
}

function buildMenuTemplate(opts, handlers) {
  const notificationsEnabled = !!(opts && opts.notificationsEnabled);
  const h = handlers || {};
  return [
    { label: MENU_LABELS.show, click: function () { safeInvoke(h.show); } },
    { label: MENU_LABELS.refresh, click: function () { safeInvoke(h.refresh); } },
    {
      label: MENU_LABELS.notifications,
      type: 'checkbox',
      checked: notificationsEnabled,
      click: function () { safeInvoke(h.toggle); },
    },
    { type: 'separator' },
    { label: MENU_LABELS.quit, click: function () { safeInvoke(h.quit); } },
  ];
}

// Resolve the icon; an empty / unreadable image yields null so the caller simply
// skips creating a tray instead of crashing on a missing asset.
function loadTrayIcon(deps) {
  const iconPath = deps.iconPath;
  if (typeof iconPath !== 'string' || iconPath === '') return null;
  const nativeImage = deps.nativeImage;
  if (!nativeImage || typeof nativeImage.createFromPath !== 'function') return null;
  let image = null;
  try {
    image = nativeImage.createFromPath(iconPath);
  } catch (_) {
    return null;
  }
  if (!image) return null;
  if (typeof image.isEmpty === 'function' && image.isEmpty()) return null;
  return image;
}

function createTray(deps) {
  const d = deps || {};
  if (currentTray) return null; // already constructed: never create a second
  const Tray = d.Tray;
  if (typeof Tray !== 'function') return null;
  const icon = loadTrayIcon(d);
  if (!icon) return null;

  let tray = null;
  try {
    tray = new Tray(icon);
  } catch (_) {
    return null;
  }
  if (!tray) return null;

  try {
    if (typeof tray.setToolTip === 'function') tray.setToolTip(TRAY_TOOLTIP);
  } catch (_) { /* best effort */ }

  try {
    const Menu = d.Menu;
    if (Menu && typeof Menu.buildFromTemplate === 'function' && typeof tray.setContextMenu === 'function') {
      const template = buildMenuTemplate(
        { notificationsEnabled: d.notificationsEnabled },
        d.handlers
      );
      tray.setContextMenu(Menu.buildFromTemplate(template));
    }
  } catch (_) { /* best effort */ }

  currentTray = tray;
  return tray;
}

// Test-only seam: forget the module-level tray so create-once can be exercised
// in isolation. Production code never calls this.
function _resetTrayForTest() {
  currentTray = null;
}

module.exports = {
  TRAY_TOOLTIP,
  MENU_LABELS,
  buildMenuTemplate,
  createTray,
  _resetTrayForTest,
};
