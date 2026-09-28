const { app, BrowserWindow, ipcMain, screen, session, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const runtimeEnv = require('./runtime_env');
const runtimeManager = require('./runtime_manager');
const notificationPolicy = require('./notification_policy');
const notificationManagerModule = require('./notification_manager');
const trayManager = require('./tray_manager');

// Phase 6A: diagnostics are opt-in (OPENCODE_WIDGET_DEBUG=1|true). In production
// devTools is off and snap logging is a no-op.
const DEBUG = runtimeManager.debugEnabled(process.env);

// 单实例锁：第二个实例直接退出，不创建窗口/不启定时器/不启动 server 所有权逻辑。
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  return;
}

// 第二实例被拉起：还原并聚焦已有主窗口，绝不动用户的尺寸/吸顶状态。
app.on('second-instance', () => {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
});

// 临时诊断：吸顶判定日志（排查后移除）。仅 debug 开启；超 ~1MB 轮转保留一份 .1
const SNAP_LOG = path.join(os.tmpdir(), 'widget_snap.log');
function debugSnap(msg) {
  if (!DEBUG) return;
  try {
    let size = 0;
    try { size = fs.statSync(SNAP_LOG).size; } catch (_) { size = 0; }
    if (runtimeManager.shouldRotateLog(size)) {
      try { fs.renameSync(SNAP_LOG, SNAP_LOG + '.1'); } catch (_) { /* ignore */ }
    }
    fs.appendFileSync(SNAP_LOG, new Date().toISOString() + ' ' + msg + '\n');
  } catch (_) { /* ignore */ }
}

// 登录态分区：持久化，登录一次后续免登录复用
const LOGIN_PARTITION = 'persist:opencode-auth';
let loginWin = null;

function opencodeAuthUrl() {
  return 'https://opencode.ai/auth';
}

async function readAuthCookieFromSession(ses) {
  try {
    const cookies = await ses.cookies.get({ url: 'https://opencode.ai' });
    const auth = cookies.find(c => c.name === 'auth');
    return auth ? auth.value : '';
  } catch (_) { return ''; }
}

// server 写入的 runtime.json：目录 = OPENCODE_WIDGET_RUNTIME_DIR 或 os.tmpdir()/opencode-widget
// 内容 {token, port, pid, created}。启动后稍晚才出现，故带重试。
// 解析逻辑在 runtime_env.js（纯 Node，可单测）。
const RUNTIME_TIMEOUT_MS = 10000;
const RUNTIME_INTERVAL_MS = 250;
function readRuntimeEnv() {
  const attempt = () => {
    const r = runtimeEnv.readRuntimeOnce(process.env);
    if (!r.ok && r.reason === 'stale') {
      // pid 已退出的残留文件：尽力删除后继续等待新 server 写入
      try { fs.unlinkSync(runtimeEnv.runtimeFilePath(process.env)); } catch (_) { /* ignore */ }
    }
    return r;
  };
  const now = attempt();
  if (now.ok) return Promise.resolve(now);
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      const r = attempt();
      if (r.ok) { clearInterval(timer); resolve(r); return; }
      if (Date.now() - started >= RUNTIME_TIMEOUT_MS) {
        clearInterval(timer);
        resolve({ ok: false, reason: 'timeout' });
      }
    }, RUNTIME_INTERVAL_MS);
  });
}

const SIZES = {
  small: [540, 260],
  mid: [560, 480],
  large: [960, 720],
};

// 吸顶迟滞：靠近顶部 SNAP_NEAR 内触发吸顶；已吸顶时须拖离超过 SNAP_OUT 才解除。
// 否则吸顶状态下任何 12px 内的拖动都会被强制拽回顶部，窗口永远拖不下来。
const SNAP_NEAR = 12;
const SNAP_OUT = 40;

let win = null;
let snapped = false;
let curUiState = "mid";     // 前端最近一次上报的 UI 状态 (save-ui-state)
let preSnapUiState = "mid"; // 吸顶前的 UI 状态, 拖离时恢复
let pendingSnap = null;     // 拖动结束后要落定的状态: "snap" | "restore" | null
let programmaticMove = false; // 程序化移动(非用户拖拽)期间跳过吸顶判定，避免误吸顶

// 程序化 setBounds/setPosition：设置标记排除其引发的 move/moved 被当作用户拖拽
function progMove(fn) {
  programmaticMove = true;
  try { fn(); } finally { setTimeout(() => { programmaticMove = false; }, 150); }
}

// 吸顶态点击穿透：光标在顶栏内 → 窗口可拖可点；顶栏之下 → 点击穿透到背后桌面
let clickThroughMode = false;      // 是否处于吸顶穿透模式
let clickThroughHeaderH = 44;      // 顶栏高度（渲染层每次进吸顶时上报）
let clickThroughPoll = null;       // 光标轮询定时器
let clickThroughCur = false;       // 当前 ignoreMouseEvents 状态
let lastUserMoveAt = 0;            // 最近一次用户拖动时间戳（用于拖动中不切换穿透）
const CLICK_THROUGH_INTERVAL = 50;

function stopClickThroughPoll() {
  if (clickThroughPoll) { clearInterval(clickThroughPoll); clickThroughPoll = null; }
}

function setClickThrough(enabled, headerH) {
  clickThroughMode = !!enabled;
  if (typeof headerH === 'number' && headerH > 0) clickThroughHeaderH = headerH;
  if (clickThroughMode) {
    clickThroughCur = false;
    if (win) win.setIgnoreMouseEvents(false);
    if (!clickThroughPoll) clickThroughPoll = setInterval(pollClickThrough, CLICK_THROUGH_INTERVAL);
  } else {
    stopClickThroughPoll();
    clickThroughCur = false;
    if (win) win.setIgnoreMouseEvents(false);
  }
}

function pollClickThrough() {
  if (!clickThroughMode || !win || win.isDestroyed()) return;
  // 拖动进行中不切换穿透（避免打断原生拖拽）
  if (Date.now() - lastUserMoveAt < 150) return;
  try {
    const b = win.getBounds();
    const cp = screen.getCursorScreenPoint();
    const overHeader = cp.x >= b.x && cp.x <= b.x + b.width && cp.y >= b.y && cp.y <= b.y + clickThroughHeaderH;
    const want = !overHeader;
    if (want !== clickThroughCur) {
      clickThroughCur = want;
      win.setIgnoreMouseEvents(want, { forward: true });
    }
  } catch (_) { /* ignore */ }
}

function restoreFromSnap() {
  if (!win) return;
  // 恢复目标: 吸顶期间用户若手动改过状态(如重新展开大屏)则尊重之, 否则恢复吸顶前状态
  const target = curUiState !== "small" ? curUiState : preSnapUiState;
  const size = SIZES[target] || SIZES.mid;
  const b = win.getBounds();
  progMove(() => win.setBounds({ x: b.x, y: b.y, width: size[0], height: size[1] }));
  win.webContents.send('snap-restore', target);
}

// 拖动过程中绝不 setBounds/setPosition（Windows 下会中断原生拖拽，导致要拖两次）。
// 这里只记录"拖动结束后要做什么"，实际落定在 win.on('moved')。
function updateSnap(x, y) {
  if (!win) return;
  try {
    const wa2 = screen.getDisplayNearestPoint({ x, y }).workArea;
    const dy = y - wa2.y;
    if (snapped) {
      // 已吸顶：窗口自由跟随鼠标；拖离超过 SNAP_OUT 才解除（落定在 moved）
      if (dy > SNAP_OUT) { snapped = false; pendingSnap = "restore"; debugSnap('arm-restore dy=' + dy); }
      else pendingSnap = null; // 未拖出阈值，松手仍保持吸顶
      return;
    }
    // 本次拖动已决定解除吸顶后，不再因回顶部而翻盘吸顶
    if (pendingSnap === "restore") return;
    if (Math.abs(dy) <= SNAP_NEAR) { pendingSnap = "snap"; preSnapUiState = curUiState; debugSnap('arm-snap dy=' + dy); }
    else if (pendingSnap === "snap") { pendingSnap = null; debugSnap('disarm-snap dy=' + dy); }
  } catch (_) { /* ignore */ }
}

// 拖拽结束(moved)时落定，此刻 setBounds 安全，不会中断拖拽
function applyPendingSnap() {
  if (!win || !pendingSnap) return;
  const p = pendingSnap;
  pendingSnap = null;
  try {
    const [x, y] = win.getPosition();
    const wa = screen.getDisplayNearestPoint({ x, y }).workArea;
    const dy = y - wa.y;
    if (p === "snap") {
      // 落定时复验：moved 若在拖动中途触发，窗口可能已不在吸顶带内，此时放弃吸顶
      if (Math.abs(dy) > SNAP_NEAR) { debugSnap('snap-cancel dy=' + dy); return; }
      progMove(() => win.setBounds({ x, y: wa.y, width: SIZES.small[0], height: SIZES.small[1] }));
      snapped = true;
      debugSnap('snap dy=' + dy);
      win.webContents.send('snap-small');
    } else if (p === "restore") {
      debugSnap('restore dy=' + dy);
      restoreFromSnap();
    }
  } catch (_) { /* ignore */ }
}

// Phase 6A: authenticated heartbeat keeps a living widget's server alive; the
// server self-exits only after authenticated traffic stops for its idle window.
// Failures are benign (no log spam, no retry storm, no extra server spawn).
const HEARTBEAT_INTERVAL_MS = 60000;
const QUIT_PROBE_TIMEOUT_MS = 1500;
let heartbeatTimer = null;

async function sendHeartbeat() {
  const r = runtimeEnv.readRuntimeOnce(process.env);
  if (!r.ok) return; // server not up (or already gone): nothing to do
  try {
    await fetch(r.base + runtimeManager.heartbeatPath(), {
      headers: { 'Authorization': 'Bearer ' + r.token },
    });
  } catch (_) { /* silent/benign */ }
}

function startHeartbeat() {
  if (heartbeatTimer) return;
  heartbeatTimer = setInterval(() => { sendHeartbeat(); }, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

// Phase 6B: opt-in, forecast-aware notifications. Default OFF. This layer only
// consumes the server's /api/forecast; it never recomputes burn rate or
// time-to-limit. The manager is created after window creation and stopped first
// on quit. The renderer only ever sees validated settings over a narrow IPC.
const NOTIFICATION_DEFAULT_INTERVAL_MS = 900 * 1000; // 15 minutes
const NOTIFICATION_MIN_INTERVAL_MS = 15 * 60 * 1000;
let notificationManager = null;

// State file: env override (debug/tests) else Electron userData.
function notificationStatePath() {
  const override = process.env.OPENCODE_WIDGET_NOTIFY_STATE;
  if (typeof override === 'string' && override.trim()) return override;
  try {
    return path.join(app.getPath('userData'), 'notification_state.json');
  } catch (_) {
    return path.join(os.tmpdir(), 'opencode-widget-notification_state.json');
  }
}

function notificationIntervalMs() {
  const raw = Number(process.env.OPENCODE_WIDGET_NOTIFY_INTERVAL_S);
  // Production floor is >= 15 min; the fixture/test seam may go lower so the
  // smoke can step evaluations deterministically.
  const floor = process.env.OPENCODE_WIDGET_NOTIFY_FIXTURE ? 1000 : NOTIFICATION_MIN_INTERVAL_MS;
  if (Number.isFinite(raw) && raw > 0) {
    return Math.max(floor, Math.round(raw * 1000));
  }
  return NOTIFICATION_DEFAULT_INTERVAL_MS;
}

// Main-only authenticated GET /api/forecast: the runtime token never leaves the
// main process. Debug/test hook OPENCODE_WIDGET_NOTIFY_FIXTURE (a path to a
// forecast JSON) overrides the fetch entirely; it is never exposed to the
// renderer and is only read from the environment.
async function fetchForecastMain() {
  const fixture = process.env.OPENCODE_WIDGET_NOTIFY_FIXTURE;
  if (typeof fixture === 'string' && fixture.trim()) {
    try { return JSON.parse(fs.readFileSync(fixture, 'utf8')); } catch (_) { return null; }
  }
  const env = await readRuntimeEnv();
  if (!env.ok) return null;
  try {
    const resp = await fetch(env.base + '/api/forecast', {
      headers: { 'Authorization': 'Bearer ' + env.token },
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (_) {
    return null; // server errors are skipped silently
  }
}

// Debug-only, non-sensitive diagnostics (never logs tokens/cookies/ids).
function notifyLog(msg) {
  if (!DEBUG) return;
  try { console.log('[notify] ' + msg); } catch (_) { /* ignore */ }
}

// Notification click target: restore only if minimized, then show + focus.
// Never resizes, never changes the snap state, never opens an external URL.
function focusWidget() {
  if (!win || win.isDestroyed()) return false;
  try {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  } catch (_) { return false; }
  return true;
}

function openForecast() {
  if (!win || win.isDestroyed()) return false;
  try { win.webContents.send('open-forecast'); } catch (_) { return false; }
  return true;
}

function showWidgetNotification(event) {
  const { Notification } = require('electron');
  if (!Notification || typeof Notification.isSupported !== 'function' || !Notification.isSupported()) {
    return 'unsupported';
  }
  const built = notificationPolicy.buildNotification(event, {
    ttlHours: event.time_to_limit_hours,
    resetAt: event.reset_at,
    nowMs: Date.now(),
  });
  const notification = new Notification({ title: built.title, body: built.body });
  notification.on('click', () => { focusWidget(); openForecast(); });
  notification.show();
  return true;
}

function startNotificationManager() {
  if (notificationManager) return;
  notificationManager = notificationManagerModule.createNotificationManager({
    statePath: notificationStatePath(),
    fetchForecast: fetchForecastMain,
    showNotification: showWidgetNotification,
    focusWidget: focusWidget,
    openForecast: openForecast,
    log: notifyLog,
    intervalMs: notificationIntervalMs(),
    // Test/fixture mode only: allow a short evaluation interval so the smoke can
    // step baseline/notify/cooldown/quiet-hours deterministically. Production
    // (no fixture) always uses MIN_INTERVAL_MS (>= 15 min).
    minIntervalMs: process.env.OPENCODE_WIDGET_NOTIFY_FIXTURE ? 1000 : undefined,
    now: () => Date.now(),
  });
  notificationManager.start().catch(() => { /* best effort */ });
}

function stopNotificationManager() {
  if (!notificationManager) return;
  try { notificationManager.stop(); } catch (_) { /* best effort */ }
}

// Phase 6B: minimal system tray. Created once, strictly after the
// single-instance lock and window creation. It is a thin surface over existing
// actions: show (focusWidget), refresh (renderer reuses its own refresh path,
// no dashboard fan-out), toggle the policy-validated notification setting, and
// quit (which must run the Phase 6A will-quit cleanup, never an os-level exit).
let widgetTray = null;

// Debug-only, non-sensitive diagnostics.
function trayLog(msg) {
  if (!DEBUG) return;
  try { console.log('[tray] ' + msg); } catch (_) { /* ignore */ }
}

function trayNotificationsEnabled() {
  if (!notificationManager) return false;
  try {
    const settings = notificationManager.getSettings();
    return !!(settings && settings.enabled === true);
  } catch (_) {
    return false;
  }
}

function trayHandlers() {
  return {
    show: () => { focusWidget(); },
    refresh: () => {
      if (!win || win.isDestroyed()) return;
      try { win.webContents.send('tray-refresh'); } catch (_) { /* best effort */ }
    },
    toggle: () => {
      if (!notificationManager) return;
      const enabled = trayNotificationsEnabled();
      notificationManager.setSettings({ enabled: !enabled });
      rebuildTrayMenu();
    },
    quit: () => { app.quit(); },
  };
}

function rebuildTrayMenu() {
  if (!widgetTray || typeof widgetTray.setContextMenu !== 'function') return;
  try {
    const template = trayManager.buildMenuTemplate(
      { notificationsEnabled: trayNotificationsEnabled() },
      trayHandlers()
    );
    widgetTray.setContextMenu(Menu.buildFromTemplate(template));
  } catch (_) { /* best effort */ }
}

function createWidgetTray() {
  if (widgetTray) return widgetTray;
  const tray = trayManager.createTray({
    Tray: Tray,
    Menu: Menu,
    nativeImage: nativeImage,
    iconPath: path.join(__dirname, 'assets', 'tray.png'),
    notificationsEnabled: trayNotificationsEnabled(),
    handlers: trayHandlers(),
  });
  if (!tray) return null;
  widgetTray = tray;
  trayLog('tray created');
  return tray;
}

function destroyWidgetTray() {
  if (!widgetTray) return;
  try { widgetTray.destroy(); } catch (_) { /* best effort */ }
  widgetTray = null;
}

// Quit path: only terminate a server we can positively identify as ours.
// Read runtime.json; if it has pid+token, probe the authenticated heartbeat
// endpoint (short timeout). Probe success -> kill pid (best effort) and unlink
// runtime.json only if it still names the same pid. Probe failure -> leave the
// stale file untouched for startup handling; never kill an innocent process.
async function terminateOwnedServer() {
  const info = runtimeManager.readRuntimeInfo();
  if (!info || typeof info.token !== 'string' || !info.token) return;
  if (typeof info.pid !== 'number' || !Number.isInteger(info.pid) || info.pid <= 0) return;
  if (typeof info.port !== 'number' || !Number.isInteger(info.port) || info.port <= 0) return;

  let probeOk = false;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), QUIT_PROBE_TIMEOUT_MS);
    try {
      const resp = await fetch(
        'http://127.0.0.1:' + info.port + runtimeManager.heartbeatPath(),
        {
          headers: { 'Authorization': 'Bearer ' + info.token },
          signal: controller.signal,
        }
      );
      probeOk = !!resp.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch (_) { probeOk = false; }

  if (!runtimeManager.shouldTerminateOwnedServer(info, probeOk)) return;
  try { process.kill(info.pid); } catch (_) { /* best effort */ }
  try {
    const again = runtimeManager.readRuntimeInfo();
    if (runtimeManager.ownedServerMatches(again, info.pid)) {
      fs.unlinkSync(runtimeManager.runtimeFilePath());
    }
  } catch (_) { /* best effort */ }
}

function createWindow() {
  const wa = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    width: SIZES.mid[0],
    height: SIZES.mid[1],
    x: Math.round((wa.width - SIZES.mid[0]) / 2),
    y: Math.round((wa.height - SIZES.mid[1]) / 2),
    frame: false,
    transparent: true,
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    fullscreenable: false,
    maximizable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: DEBUG,
    },
  });

  win.loadFile(path.join(__dirname, 'app', 'index.html'));

  // 安全加固：悬浮窗不允许任何导航或弹窗（内容全部来自本地文件）
  win.webContents.on('will-navigate', (event) => { event.preventDefault(); });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  // 吸顶：窗口顶边靠近屏幕工作区顶部时贴齐并通知前端切小屏
  const born = Date.now();
  win.on('move', () => {
    if (Date.now() - born < 2000) return;  // 启动 2 秒内不吸顶，避免初始化跳动触发
    if (programmaticMove) return;          // 程序化移动不做吸顶判定
    lastUserMoveAt = Date.now();
    const [x, y] = win.getPosition();
    updateSnap(x, y);
  });

  // 拖拽结束（Windows 上 move 事件密集，moved 在真正停手后触发一次）才落定吸顶/解除，
  // 避免拖动中途 setBounds 中断原生拖拽（否则一次拖不完、要拖两次）。
  win.on('moved', () => {
    debugSnap('moved fired, pendingSnap=' + pendingSnap + ' pos=' + JSON.stringify(win.getPosition()));
    applyPendingSnap();
  });

  win.on('closed', () => {
    stopClickThroughPoll();
    win = null;
  });

  win.setIgnoreMouseEvents(false);
}

app.whenReady().then(() => {
  createWindow();
  // Phase 6B: started only after the single-instance lock and window creation.
  startNotificationManager();
  // Tray last: it reflects the (now existing) notification settings.
  createWidgetTray();
  startHeartbeat();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});

// 正常退出：停掉所有定时器、关闭登录窗口，然后回收自己拥有的 Python server
// 并删除 runtime.json。guard + preventDefault 保证只跑一次清理，再真正 quit。
let quitCleanupStarted = false;
app.on('will-quit', (event) => {
  if (quitCleanupStarted) return;
  quitCleanupStarted = true;
  event.preventDefault();
  // Every step is individually guarded and a hard fallback guarantees the app
  // always quits (a throwing/hanging cleanup step must never wedge shutdown).
  let finished = false;
  const finish = () => { if (finished) return; finished = true; try { app.quit(); } catch (_) { /* ignore */ } };
  setTimeout(finish, 3000);
  try { stopNotificationManager(); } catch (_) { /* ignore */ }
  try { destroyWidgetTray(); } catch (_) { /* ignore */ }
  try { stopHeartbeat(); } catch (_) { /* ignore */ }
  try { stopClickThroughPoll(); } catch (_) { /* ignore */ }
  try {
    if (loginWin && !loginWin.isDestroyed()) loginWin.close();
  } catch (_) { /* ignore */ }
  Promise.resolve()
    .then(() => terminateOwnedServer())
    .catch(() => { /* best effort */ })
    .finally(finish);
});

ipcMain.handle('resize', (e, uiState) => {
  const size = SIZES[uiState] || SIZES.mid;
  if (!win) return true;
  const bounds = win.getBounds();
  const wa = screen.getDisplayNearestPoint({ x: bounds.x, y: bounds.y }).workArea;
  let nx = bounds.x + Math.floor((bounds.width - size[0]) / 2);
  let ny = bounds.y + Math.floor((bounds.height - size[1]) / 2);
  // 吸顶保持：窗口顶边原本贴在工作区顶部时，缩放后仍贴顶（避免掉下来）
  if (Math.abs(bounds.y - wa.y) <= 12) ny = wa.y;
  // 防超出屏幕：窗口完整保持在工作区内（否则顶部被遮住无法拖动）
  nx = Math.max(wa.x, Math.min(nx, wa.x + wa.width - size[0]));
  ny = Math.max(wa.y, Math.min(ny, wa.y + wa.height - size[1]));
  progMove(() => win.setBounds({ x: nx, y: ny, width: size[0], height: size[1] }));
  return true;
});

ipcMain.handle('quit', () => {
  app.quit();
  return true;
});

// Phase 6B: notification settings. Payloads are strictly validated by
// notification_policy (unknown fields / wrong types / non-HH:MM rejected); this
// is never a generic settings writer and never touches the runtime token.
ipcMain.handle('notification-settings-get', () => {
  if (!notificationManager) {
    return notificationPolicy.normalizeSettings(notificationPolicy.DEFAULT_SETTINGS, {});
  }
  return notificationManager.getSettings();
});

ipcMain.handle('notification-settings-set', (_event, payload) => {
  if (!notificationManager) return { ok: false, error: 'unavailable' };
  return notificationManager.setSettings(payload);
});

ipcMain.handle('open-login', async () => {
  try {
    const { shell } = require('electron');
    await shell.openExternal('https://opencode.ai/auth');
    return { opened: true };
  } catch (e) {
    return { opened: false, error: String(e) };
  }
});

// Phase 2.1: 渲染层不再持有 runtime token/base。所有本地 API 调用经 'api-request' 代理：
// 只放行固定路由（严格校验 method/path/payload），由主进程附带 Bearer 后发起请求，
// 返回 {ok, status, data} —— token 永不出现在返回值里。
const API_VIEW_ID_RE = /^[A-Za-z0-9_:|.\-]{1,200}$/;
const API_AGENT_RANGES = new Set(['today', '7d', '30d', 'all']);

function resolveApiTarget(req) {
  if (!req || typeof req !== 'object') return null;
  const method = req.method;
  const p = typeof req.path === 'string' ? req.path : '';
  if (method === 'GET') {
    if (p === '/api/state' || p === '/api/config' || p === '/api/views') return { method, path: p, body: null };
    // Forecast: bare path only (range-independent, no query accepted).
    if (p === '/api/forecast') return { method, path: p, body: null };
    if (p === '/api/formula' || p === '/api/formula?refresh=1') return { method, path: p, body: null };
    if (p === '/api/agents' || p === '/api/models' || p === '/api/providers' || p === '/api/sessions' || p === '/api/timeline') {
      return { method, path: p, body: null };
    }
    const ar = /^\/api\/(?:agents|models|providers|sessions|timeline)\?range=([a-z0-9]+)$/.exec(p);
    if (ar && API_AGENT_RANGES.has(ar[1])) return { method, path: p, body: null };
    const vm = /^\/api\/view\/([^/?#]+)$/.exec(p);
    if (vm && API_VIEW_ID_RE.test(vm[1])) return { method, path: '/api/view/' + encodeURIComponent(vm[1]), body: null };
    return null;
  }
  if (method === 'POST') {
    const payload = req.payload;
    if (p === '/api/key') {
      if (!payload || typeof payload.key !== 'string' || payload.key.length > 4096) return null;
      return { method, path: p, body: { key: payload.key } };
    }
    if (p === '/api/server') {
      if (!payload || typeof payload.auth_cookie !== 'string' || payload.auth_cookie.length > 8192) return null;
      if (typeof payload.workspace_id !== 'string' || payload.workspace_id.length > 8192) return null;
      return { method, path: p, body: { auth_cookie: payload.auth_cookie, workspace_id: payload.workspace_id } };
    }
    if (p === '/api/calibrate') {
      if (!payload) return null;
      for (const k of ['session', 'weekly', 'monthly']) {
        if (typeof payload[k] !== 'number' || !Number.isFinite(payload[k])) return null;
      }
      return { method, path: p, body: { session: payload.session, weekly: payload.weekly, monthly: payload.monthly } };
    }
    if (p === '/api/sync') {
      if (payload != null) return null;
      return { method, path: p, body: null };
    }
    return null;
  }
  return null;
}

ipcMain.handle('api-request', async (_event, req) => {
  const target = resolveApiTarget(req);
  if (!target) return { ok: false, error: 'forbidden' };
  const env = await readRuntimeEnv();
  if (!env.ok) return { ok: false, error: 'unavailable' };
  try {
    const headers = { 'Authorization': 'Bearer ' + env.token };
    const init = { method: target.method, headers };
    if (target.body != null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(target.body);
    }
    const resp = await fetch(env.base + target.path, init);
    const text = await resp.text();
    let data;
    try { data = text === '' ? null : JSON.parse(text); } catch (_) { data = text; }
    if (!resp.ok) return { ok: false, status: resp.status, error: 'http_' + resp.status, data };
    return { ok: true, status: resp.status, data };
  } catch (_) {
    return { ok: false, error: 'request_failed' };
  }
});

// 应用内登录窗口：自动抓取 auth cookie + workspace_id（替代 F12 手动复制）
// 抓取后由主进程直接 POST 到本地 server（携带 runtime token），渲染层只拿到配置状态。
ipcMain.handle('grab-auth', async () => {
  // 若已有有效登录态（分区 cookie + 自动重定向）则直接捕获，无需用户操作；
  // 否则停留在登录页等用户登录后捕获。同一分区持久化，下次免登录。
  if (loginWin) { loginWin.focus(); return { ok: false, error: '登录窗口已打开' }; }
  const ses = session.fromPartition(LOGIN_PARTITION);
  return new Promise((resolve) => {
    let settled = false;
    let capturing = false;
    let poll = null;
    const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
    const settle = (r) => { if (!settled) { settled = true; stopPoll(); resolve(r); } };
    loginWin = new BrowserWindow({
      width: 980, height: 700,
      parent: win,
      modal: true,
      resizable: true,
      autoHideMenuBar: true,
      backgroundColor: '#0b0e14',
      title: 'opencode.ai 登录',
      webPreferences: {
        session: ses,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        devTools: DEBUG,
      },
    });
    loginWin.loadURL(opencodeAuthUrl());
    // 安全加固：登录窗口只允许在 opencode.ai 内导航，新窗口一律拒绝
    loginWin.webContents.on('will-navigate', (event, url) => {
      if (!/^https:\/\/opencode\.ai(\/|$)/.test(url)) event.preventDefault();
    });
    loginWin.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    loginWin.on('closed', () => {
      stopPoll();
      loginWin = null;
      settle({ ok: false, error: '登录窗口已关闭' });
    });

    const tryCapture = async () => {
      if (settled || capturing || !loginWin || loginWin.isDestroyed()) return;
      const url = loginWin.webContents.getURL();
      const m = /\/workspace\/(wrk_[A-Za-z0-9]+)/.exec(url);
      if (!m) return;
      capturing = true;
      let cookie = '';
      try { cookie = await readAuthCookieFromSession(ses); } catch (_) { cookie = ''; }
      if (!cookie) { capturing = false; return; }
      // 交给主进程提交，cookie/workspace 绝不经 IPC 返回渲染层
      try {
        const env = await readRuntimeEnv();
        if (!env.ok) {
          settle({ ok: false, error: '本地服务未就绪（runtime.json 未找到）' });
        } else {
          const resp = await fetch(env.base + '/api/server', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer ' + env.token,
            },
            body: JSON.stringify({ auth_cookie: cookie, workspace_id: m[1] }),
          });
          const data = await resp.json().catch(() => ({}));
          if (data && data.ok) {
            settle({
              ok: true,
              auth_configured: !!data.auth_configured,
              workspace_configured: !!data.workspace_configured,
            });
          } else {
            settle({ ok: false, error: '保存登录态失败' });
          }
        }
      } catch (_) {
        settle({ ok: false, error: '保存登录态失败' });
      }
      setTimeout(() => { if (loginWin && !loginWin.isDestroyed()) loginWin.close(); }, 300);
    };
    loginWin.webContents.on('did-navigate', tryCapture);
    loginWin.webContents.on('did-redirect-navigation', tryCapture);
    loginWin.webContents.on('did-finish-load', tryCapture);
    // 兜底：SPA 或延迟重定向时轮询
    poll = setInterval(tryCapture, 800);
  });
});

ipcMain.handle('save-ui-state', (e, s) => { curUiState = s; return s; });

// 吸顶态点击穿透：enabled=true 时启用光标轮询（顶栏内可交互，顶栏下穿透到桌面）
ipcMain.handle('set-click-through', (_e, enabled, headerH) => {
  setClickThrough(enabled, headerH);
  return true;
});

// 前端手动退出吸顶（如吸顶态点击 _ / □ 展开）：清空吸顶态并关闭穿透
ipcMain.handle('exit-snap', () => {
  snapped = false;
  pendingSnap = null;
  setClickThrough(false);
  return true;
});
