# Compact Floating UX

> 状态：v0.9.0-rc.1 之后的桌面交互重构（`dev/compact-floating-ux`）。
> 本轮只改浮窗形态与默认态，不改 Forecast / Observability / Notifications 的数据语义与安全边界。

## 1. 为什么重构

重构前，桌面浮窗的默认打开形态是 560×480 的"中型仪表盘"（圆环 + 供应商条 + 完整模型列表），
展开后是 960×720 的完整分析面板。作为**常驻桌面**的悬浮窗，它存在四个问题：

```text
默认态过重：启动即进入接近大面板的中型视图
占屏面积过大：560×480 起步，常驻遮挡工作区
视觉存在感太强：大圆环 + 进度条 + 模型列表同时呈现
常驻形态错位：它更像"分析控制台"，而不是"桌面状态条"
```

本轮把浮窗改成 **默认轻量、按需展开、信息分层** 的三级形态。

## 2. 三态设计

| 状态 | 窗口尺寸 | 定位 | 内容 |
| --- | --- | --- | --- |
| **Mini**（默认） | 400×88 | 常驻状态条 | 严重度状态点（正常/注意/告警）· `本月 N%` · 本月消费 · `Key ✓/✗` |
| **Compact** | 560×420 | 快速查看卡片 | 三窗口圆环 + 5h/本周/本月进度条 + Top 3 模型 + 更新时间 + 「打开完整面板」 |
| **Expanded** | 960×720 | 完整分析面板 | 原 Large Dashboard 全量：Overview / Agents / Models / Sessions / Forecast / Timeline / 矩阵 / 热力图 |

### 2.1 Mini（默认常驻态）

- 单行状态条，一眼可扫，不显示圆环、模型列表、时间线、页脚。
- 状态点阈值与圆环配色一致：`≥95%` 告警（红）、`≥70%` 注意（橙）、其余正常（绿）。
- 核心数字取**本月官方计量**（与 Expanded 总用量口径一致），悬停显示 `已用 / 限额`。
- 不请求 Expanded 级数据（见 §6 数据策略）。

### 2.2 Compact（快看卡）

- 保留原有圆环 + 三窗口进度条 + 总览/5小时/本周/本月切换。
- 模型列表**最多 Top 3**（排序规则与完整列表一致，仅截断显示，计数标注 `Top 3`）；完整列表只在 Expanded。
- 底部提供更新时间与「打开完整面板」显式入口。
- 页脚隐藏（更新时间移入卡片内）。

### 2.3 Expanded（完整面板）

- 即原 Large Dashboard 的继承者，全部分析能力保留，未删除任何功能。
- 仅在用户主动展开、通知点击或托盘详情查看时出现，不再默认常驻。

## 3. 默认行为

- **冷启动恒为 Mini**。窗口出生即 400×88，渲染层以 `noResize` 启动，无启动闪烁/窗口跳动。
- 不恢复"上次展开态"：即使上次退出时处于 Expanded，下次启动也回到 Mini，保证常驻不碍事。
- 窗口位置仍由既有逻辑管理（拖动自由摆放；贴顶吸附逻辑不变，见 §7）。

## 4. 切换交互

| 动作 | 结果 |
| --- | --- |
| 单击 Mini 状态条（或其上 Enter / Space） | Mini → Compact |
| Mini 上按 `□`（btnExpand） | Mini → Expanded |
| Compact 上按 `─`（btnMin）或 `Esc` | Compact → Mini |
| Compact 上按「打开完整面板」或 `□` | Compact → Expanded |
| Expanded 上按 `─`（btnMin）或 `Esc` | Expanded → Mini |
| Expanded 上按 `□` | Expanded → Compact |
| 右键菜单「退出」/ `×` | 退出应用（语义不变） |

- `Esc` 在输入控件（可观测性搜索/筛选框）内不劫持；设置弹窗或右键菜单打开时不收缩窗口。
- 未新增 hover 自动展开/自动收起；本轮以**点击展开 / 点击收起**为唯一切换方式。

## 5. 托盘与通知

- **托盘 Show Widget**：恢复/聚焦浮窗，**保持当前可见状态**（不 resize、不切换 Mini/Compact/Expanded）。
- **托盘 Refresh / Notifications / Quit**：语义不变。
- **通知点击**：恢复窗口 → 进入 **Expanded + Forecast** 标签页（与本轮之前一致，未被 Mini/Compact 破坏）。

## 6. 数据与性能策略

- 渲染按模式门控：Mini 只渲染状态条；Compact 不渲染图表/热力图/Expanded 模型列表；Expanded 才渲染全部分析视图。
- 视图预取按模式收敛（`neededViewIds`）：Mini/Compact **不预取任何 view**（仅 `/api/state` + 公式）；
  逐日序列、模型曲线等 Expanded 级视图在**展开时**由 `ensureExpandedViews()` 按需补齐，命中缓存不重复请求。
- `/api/state`（60s 轮询）与公式同步语义不变；未新增任何前端 API。
- Observability 各标签页的懒加载/缓存（`OCW.dashCache`）保持原样。

## 7. 贴边 / 吸顶

既有"拖到屏幕顶边吸附 + 顶栏穿透"逻辑原样保留：

- 吸附时窗口落到 `SIZES.small`（现为更轻的 400×88 状态条，遮挡更小）并进入 Mini；
- 拖离超过阈值恢复吸附前状态；
- 手动在吸附态切换视图会先退出吸附并关闭穿透。

## 8. 与旧 small / mid / large 的映射

本轮**复用**既有三态状态机（`uiState: small|mid|large`、`setUiState()`、`SIZES`），未发明新模式名：

```text
small  ≈ Mini      （重新定义内容: 圆环仪表盘 → 单行状态条; 尺寸 540×260 → 400×88）
mid    ≈ Compact   （瘦身: 模型列表全量 → Top 3; 560×480 → 560×420; 新增显式展开入口）
large  ≈ Expanded  （保持 960×720 与全部能力; 不再默认常驻）
```

对应实现：`electron/main.js` 的 `SIZES`、`electron/app/app.js` 的 `setUiState()/renderMini()/fillModelList(…, limit)/neededViewIds()`、
`electron/app/index.html` 的 `#smallView/#compactView/#dashView`、`electron/app/app.css` 的 `body.mode-*` 规则。

## 9. 可访问性

- Mini 状态条：`role="button"`、`tabindex="0"`、`aria-label`，支持 Enter/Space 激活。
- 「打开完整面板」按钮带 `aria-label`。
- 键盘：`Esc` 收起；原有 `←→/↑↓/Tab/X` 快捷键语义未删除。

## 10. 已知限制

- Compact 模型列表固定 Top 3，暂不提供"在 Compact 内看更多"（完整列表请展开）。
- 无 hover 自动展开/收起（有意为之，避免干扰）。
- Mini 不显示配额重置倒计时（完整信息请展开查看）。
- 键盘 `↑↓` 选择模型在 Compact 下仅前 3 项可见，选中项超出 Top 3 时不可见（选择状态本身仍生效）。
- 截图文档（`docs/assets/*.png`）仍为重构前 UI，待下次发布统一更新。
