const { contextBridge, ipcRenderer } = require('electron');

// Phase 2.1: 渲染层不再接触 runtime token/base。所有本地 API 调用经主进程代理：
// 主进程返回 {ok, status, data}，这里只在 ok 时把 data 交给渲染层；
// 失败抛错，让各调用点原有的 .catch(...) 默认值兜底继续生效。
function apiCall(method, path, payload) {
  return ipcRenderer.invoke('api-request', { method, path, payload }).then((r) => {
    if (r && r.ok) return r.data;
    throw new Error((r && r.error) || 'api error');
  });
}

contextBridge.exposeInMainWorld('widgetAPI', {
  resize: (uiState) => ipcRenderer.invoke('resize', uiState),
  openLogin: () => ipcRenderer.invoke('open-login'),
  grabAuth: () => ipcRenderer.invoke('grab-auth'),
  saveUiState: (s) => ipcRenderer.invoke('save-ui-state', s),
  apiGetState: () => apiCall('GET', '/api/state'),
  apiGetConfig: () => apiCall('GET', '/api/config'),
  apiGetFormula: (force) => apiCall('GET', force ? '/api/formula?refresh=1' : '/api/formula'),
  apiGetViews: () => apiCall('GET', '/api/views'),
  apiGetView: (id) => apiCall('GET', '/api/view/' + id),
  apiGetAgents: (range) => apiCall('GET', range ? '/api/agents?range=' + range : '/api/agents'),
  apiGetModels: (range) => apiCall('GET', range ? '/api/models?range=' + range : '/api/models'),
  apiGetProviders: (range) => apiCall('GET', range ? '/api/providers?range=' + range : '/api/providers'),
  apiGetSessions: (range) => apiCall('GET', range ? '/api/sessions?range=' + range : '/api/sessions'),
  apiGetForecast: () => apiCall('GET', '/api/forecast'),
  apiPostKey: (key) => apiCall('POST', '/api/key', { key }),
  apiPostServer: (authCookie, workspaceId) => apiCall('POST', '/api/server', { auth_cookie: authCookie, workspace_id: workspaceId }),
  apiPostCalibrate: (cal) => apiCall('POST', '/api/calibrate', cal),
  apiPostSync: () => apiCall('POST', '/api/sync'),
  onSnapSmall: (cb) => ipcRenderer.on('snap-small', () => cb()),
  onSnapRestore: (cb) => ipcRenderer.on('snap-restore', (_e, s) => cb(s)),
  setClickThrough: (enabled, headerH) => ipcRenderer.invoke('set-click-through', enabled, headerH),
  exitSnap: () => ipcRenderer.invoke('exit-snap'),
  quit: () => ipcRenderer.invoke('quit'),
});
