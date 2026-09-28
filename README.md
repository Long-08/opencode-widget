# OpenCode Widget

A transparent Windows desktop widget for OpenCode usage: Go quota, local
observability, usage forecasting and opt-in notifications.

**Version:** `0.9.0-rc.1` — a release candidate. This RC is **unsigned** (no
Authenticode certificate). See the SmartScreen note below.

> [!NOTE]
> This project is a substantially modified fork of
> [ikunops/opencode-widget](https://github.com/ikunops/opencode-widget).
> Thanks to [@ikunops](https://github.com/ikunops) for the original project and codebase.
> See [UPSTREAM.md](UPSTREAM.md) for attribution and licensing notes.
>
> 本项目基于 [ikunops/opencode-widget](https://github.com/ikunops/opencode-widget)
> 进行大幅二次开发，感谢 [@ikunops](https://github.com/ikunops) 提供原始项目与代码基础。
> 上游来源及许可证说明见 [UPSTREAM.md](UPSTREAM.md)。
>
> This is an independent community project and is not affiliated with or endorsed by the OpenCode
> team. 本项目为独立社区项目，与 OpenCode 官方团队无隶属、赞助或背书关系。

![OpenCode-widget screenshot](screenshot.png)

![OpenCode-widget large dashboard](screenshot-large.png)

---

## English

### What it does

- **Transparent floating panel** on the Windows desktop: Go quota (rolling 5h /
  weekly / monthly percentage + reset countdown), model details, history curves
  and a GitHub-style heatmap.
- **Local observability**: per-agent, per-model, per-provider and per-session
  usage, token and cost statistics computed from your local OpenCode data.
- **Explainable forecasting**: deterministic, reset-aware burn rates and
  time-to-limit estimates. Every forecast output is labelled `estimate` and is
  never presented as official.
- **Opt-in notifications**: forecast-driven alerts plus a minimal tray menu.
  Off by default.
- **Cloud formula**: metering coefficients, supplier rules and view definitions
  are fetched periodically from a remote formula URL, with last-known-good
  fallback.
- **Three window sizes**: compact (snapped Dock) / mid / full dashboard, with
  drag-to-top snapping.

### What this fork adds

This is a substantially modified fork. On top of the upstream usage-widget codebase it adds:

- localhost API security hardening (runtime bearer token, Host/Origin policy, no wildcard CORS)
- Windows DPAPI secret storage with fail-closed writes and fail-safe migration
- current OpenCode database-schema support
- Agent / Model / Provider / Session observability
- usage timeline and Agent × Model visualization
- deterministic, reset-aware usage forecasting
- forecast-aware notifications and a minimal tray
- desktop lifecycle management and portable Windows packaging

See [UPSTREAM.md](UPSTREAM.md) for the full attribution and licensing note.

### Architecture

- A Python **standard-library-only** backend (`data_server.py`) serves a
  localhost JSON API on `127.0.0.1:8765`.
- An **Electron shell** (`electron/`) renders the transparent window and talks to
  the backend through a narrow preload bridge.
- The Python backend is started by the launcher; quitting the app through the
  normal path terminates the backend. A single instance is enforced.

### Requirements

- **Windows 10 / 11** (x64).
- **Python 3.11+**, standard library only — no `pip install` is required at
  runtime.
- **Node.js / Electron only for development**. The packaged RC embeds Electron;
  end users do not install Node.

### Install (portable)

1. Unzip the release folder anywhere (for example under your user profile).
2. Run the launcher: `启动Go用量悬浮窗.vbs` (recommended — no console flash) or
   the compatibility entry `启动Go用量悬浮窗.cmd`.
3. The launcher starts the Python backend and then the Electron shell. There is
   no installer and no admin requirement.

### Run / first launch

- On first launch the widget reads your local OpenCode database if present. If
  the database is missing or uses an unsupported schema, the widget shows a
  reader/empty state instead of failing.
- The backend listens on `127.0.0.1:8765` only (localhost, never a public
  interface).
- To sync official quota values, use the right-click menu to log in /
  grab the auth cookie.
- Optional auto-start: place the launcher in the Startup folder
  (`Win+R` → `shell:startup`).
- Quitting normally (tray → Quit, or closing the window) terminates the backend
  process.

### Notifications

- **Off by default.** No notification state is written and nothing is created
  until you explicitly opt in (tray toggle or the Forecast-tab checkbox).
- Notifications consume the forecast endpoint only; they never recompute usage
  or burn rate.
- Dedupe, cooldown, escalation and quiet hours are applied; notifications are not
  an audit log.

### Security model

- The OpenCode database (`~/.local/share/opencode/opencode.db`) is opened
  **read-only** and is never modified.
- Secrets (API key, auth cookie) live in a **DPAPI-encrypted `secrets.enc`**,
  bound to the current Windows user. New secret writes **fail closed**; a failed
  protection never downgrades to plaintext.
- The localhost API requires a **per-run random runtime token**
  (`Authorization: Bearer`) plus **Host and Origin policy**. **CORS is never
  `*`** — origins are allowlisted or omitted.
- The **renderer never receives the runtime token or the base URL**, and exposes
  no generic fetch / settings / notification capability.
- **Notifications are OFF by default** and are never created without the user
  opting in.
- Production defaults: **DevTools off**, **debug logging off**.

#### Remote formula trust model

- The cloud formula is **pure data — no code is executed**. The client enforces
  **HTTPS**, a **strict schema + version compatibility check**, a **size cap**, a
  **fetch timeout**, **last-known-good fallback** and **atomic adoption**.
- **No cryptographic signature is verified.** The formula is reported as
  `integrity_status: unsigned`. HTTPS protects transport, **not** content
  authorship — do not read HTTPS as content signing.

#### Scope note

This project does not defend against malicious processes already running as the
same Windows user; such a process can read the runtime token and decrypt the
per-user secret store. The controls above harden the normal path, they are not a
same-user sandbox.

### Data paths

Mutable data lives in `%APPDATA%\opencode-widget\`:

| File | Contents |
|------|----------|
| `config.json` | non-secret configuration |
| `secrets.enc` | DPAPI-encrypted secrets (Windows user-bound) |
| `usage_remote.db` | official sync ledger mirror |
| `server_usage.db` | legacy official-data ledger mirror |
| `formula_cache.json` | last-known-good cloud formula |

- Legacy in-repo copies of these files migrate into `%APPDATA%\opencode-widget\`
  automatically **once**; the originals are kept.
- The data directory can be overridden with `OPENCODE_WIDGET_DATA_DIR` (portable
  or embedded deployments).
- **Runtime token file:** `%TEMP%\opencode-widget\runtime.json` (per-run token,
  port, pid, instance id).
- **Notification state:** Electron `userData` (`notification_state.json`).
- **Debug log:** `%TEMP%\widget_snap.log` — **debug builds only**.

### Privacy

- Only **local usage metadata** is collected, plus **official quota sync** when
  you opt in.
- **Prompt / response content is never exposed** by the observability APIs.

### Upgrade

- Replace the binaries (unzip over the folder). Keep
  `%APPDATA%\opencode-widget` — your config, secrets and history are preserved.
- Migrations are **versioned and fail-safe**: a failed migration never destroys
  existing data.
- There is **no auto-updater**; upgrades are manual.

### Uninstall

1. Delete the application folder.
2. Your user data in `%APPDATA%\opencode-widget\` is **kept** unless you remove
   it. To remove it manually, delete `%APPDATA%\opencode-widget\` (this removes
   `config.json`, `secrets.enc`, `usage_remote.db`, `server_usage.db` and
   `formula_cache.json`).
3. Optionally delete `%TEMP%\opencode-widget\runtime.json` and, in debug builds,
   `%TEMP%\widget_snap.log`.

### Troubleshooting

- **Missing OpenCode database:** the widget shows an empty/reader state; make
  sure OpenCode has been used at least once and the database exists.
- **Unsupported database schema:** the reader reports the schema as unsupported
  rather than guessing; update OpenCode or the widget.
- **Port occupied (`8765`):** startup fails clearly and **never kills the other
  process**. If the occupant is this widget's own backend, the existing instance
  is reused; otherwise choose a different port via the port environment
  override.
- **Official sync unavailable:** quota values fall back to the last local state;
  retry from the menu.
- **Notifications unsupported / not firing:** notifications are off by default —
  opt in first, and check quiet hours.
- **Tray icon missing:** Windows may hide new tray icons in the overflow area;
  check there before assuming failure.
- **Formula fallback:** if the remote formula is unreachable, the last-known-good
  copy stays active (`integrity_status: unsigned`).
- **Local server unavailable:** the window shows a disconnected state; relaunch
  the launcher to restart the backend.

### Upstream & attribution

This project is a substantially modified fork of
[ikunops/opencode-widget](https://github.com/ikunops/opencode-widget); the upstream Git history is
preserved. See [UPSTREAM.md](UPSTREAM.md) for the development base commit and full attribution.

This is an independent community project and is not affiliated with or endorsed by the OpenCode team.

### License status

The upstream repository did not include an explicit software license at the time of this fork, so no
new blanket license is asserted over upstream-derived code and no `LICENSE` file is added. Compiled
binary redistribution therefore waits until upstream licensing/permission is clarified.

### Release status

`v0.9.0-rc.1` is published as a **source** release candidate (Git tag only). No compiled binary is
distributed publicly: the upstream repository does not yet establish explicit redistribution
licensing (see **License status** above). A portable Windows build can be produced locally from the
tagged source; the locally built artifact checksum is recorded outside the tagged source tree so it
does not become self-referential release metadata.

### Development

- Run the test suite:

  ```bat
  python -m pytest -o addopts="" -q
  ```

  Expected: all tests pass (461 passed when this RC was cut).
- Node/Electron is only needed to work on `electron/`; run `npm install` inside
  `electron/` first.
- If pytest cannot access its temp base directory, point `TEMP`/`TMP` at a
  writable folder before running, for example:

  ```bat
  set TEMP=%LOCALAPPDATA%\Temp\opencode
  set TMP=%TEMP%
  python -m pytest -o addopts="" -q
  ```

### SmartScreen note

`0.9.0-rc.1` is an **unsigned** release candidate — it carries no Authenticode
signature, so **Windows SmartScreen may warn** when you run it for the first
time. This is expected for an unsigned RC. **Never use a script or workaround to
bypass OS security warnings**; if you do not trust the build, do not run it.

---

## 中文

### 功能简介

一个透明的 Windows 桌面悬浮窗，展示 OpenCode 用量：Go 配额、本地可观测性、
用量预测与可选通知。

**版本：** `0.9.0-rc.1`（发布候选版，**未签名**，无 Authenticode 证书）。

- **透明悬浮窗**：Go 配额（滚动 5h / 每周 / 每月百分比 + 重置倒计时）、模型
  明细、历史曲线与 GitHub 风格热力图。
- **本地可观测性**：按 Agent / 模型 / 供应商 / 会话统计用量、Token 与费用。
- **可解释预测**：确定性的、感知重置的速率与“距耗尽时间”估算，所有预测结果均
  标记为 `estimate`，绝不当作官方值。
- **可选通知**：基于预测的提醒 + 最小托盘菜单，**默认关闭**。
- **云端公式**：计量系数、供应商规则与视图定义定期从远程公式 URL 拉取，失败时
  保留上一版（last-known-good）。
- **三档窗口**：最小屏（吸顶 Dock）/ 中屏 / 大屏仪表盘，支持拖到顶部吸附。

### 架构

- Python **仅用标准库**的后端（`data_server.py`）在本机 `127.0.0.1:8765`
  提供 JSON API。
- **Electron 外壳**（`electron/`）负责透明窗口，并经最小化的 preload 桥访问
  后端。
- 后端由启动器拉起；正常退出会终止后端进程；强制单实例。

### 环境要求

- **Windows 10 / 11**（x64）。
- **Python 3.11+**，仅标准库，运行时无需 `pip install`。
- **Node.js / Electron 仅用于开发**；打包后的 RC 已内置 Electron，最终用户无需
  安装 Node。

### 安装（绿色版）

1. 将发布目录解压到任意位置。
2. 运行启动器：`启动Go用量悬浮窗.vbs`（推荐，无黑框）或兼容入口
   `启动Go用量悬浮窗.cmd`。
3. 启动器先启动 Python 后端，再启动 Electron 外壳。无需安装、无需管理员权限。

### 运行 / 首次启动

- 首次启动会读取本地 OpenCode 数据库；若数据库缺失或 schema 不受支持，会显示
  空状态/读取状态，而不是直接失败。
- 后端仅监听 `127.0.0.1:8765`（本机，不对外）。
- 如需同步官方配额，使用右键菜单登录 / 抓取 Cookie。
- 可选开机自启：把启动器放入启动文件夹（`Win+R` → `shell:startup`）。
- 正常退出（托盘 → 退出，或关闭窗口）会终止后端进程。

### 通知

- **默认关闭**：在你明确开启（托盘开关或 Forecast 标签页复选框）之前，不会写入
  任何通知状态，也不会创建通知。
- 通知只消费预测接口，不重算用量或速率。
- 应用去重、冷却、升级与免打扰；通知不是审计日志。

### 安全模型

- OpenCode 数据库（`~/.local/share/opencode/opencode.db`）以**只读**方式打开，
  **永不修改**。
- 机密（API Key、auth cookie）保存在 **DPAPI 加密的 `secrets.enc`** 中，绑定当前
  Windows 用户；新的机密写入**失败即关闭（fail closed）**，不会降级为明文。
- 本机 API 需要**每次运行随机生成的运行时 token**（`Authorization: Bearer`），
  并配合 **Host 与 Origin 策略**；**CORS 从不为 `*`**。
- **渲染进程永远拿不到运行时 token 和 base URL**，也没有通用 fetch / 设置 /
  通知能力。
- **通知默认关闭**，未获用户明确开启绝不创建。
- 生产默认：**DevTools 关闭**、**调试日志关闭**。

#### 远程公式信任模型

- 云端公式是**纯数据，不执行任何代码**。客户端强制 **HTTPS**、**严格的 schema
  与版本兼容校验**、**大小上限**、**拉取超时**、**last-known-good 回退**与
  **原子替换**。
- **不校验任何密码学签名**，`integrity_status` 为 `unsigned`。HTTPS 只保护传输，
  **不等于对内容来源的签名**。

#### 范围说明

本项目不防御“已经以同一 Windows 用户身份运行的恶意进程”；这类进程可以读取运行时
token 并以该用户身份解密机密库。上述措施加固的是正常路径，而非同用户沙箱。

### 数据路径

可变数据位于 `%APPDATA%\opencode-widget\`：

| 文件 | 内容 |
|------|------|
| `config.json` | 非机密配置 |
| `secrets.enc` | DPAPI 加密机密（绑定 Windows 用户） |
| `usage_remote.db` | 官方同步账本镜像 |
| `server_usage.db` | 旧版官方数据账本镜像 |
| `formula_cache.json` | 云端公式 last-known-good |

- 仓库内旧位置的这些文件会**自动迁移一次**到 `%APPDATA%\opencode-widget\`，
  原文件保留。
- 数据目录可用 `OPENCODE_WIDGET_DATA_DIR` 覆盖（便携 / 嵌入式场景）。
- **运行时 token 文件：** `%TEMP%\opencode-widget\runtime.json`（本次运行的
  token / 端口 / pid / 实例 id）。
- **通知状态：** Electron `userData`（`notification_state.json`）。
- **调试日志：** `%TEMP%\widget_snap.log`（仅调试构建）。

### 隐私

- 只采集**本地用量元数据**；在你开启后，另加**官方配额同步**。
- 可观测性 API **绝不暴露提示词 / 响应内容**。

### 升级

- 用新版本覆盖解压即可，保留 `%APPDATA%\opencode-widget`，配置、机密与历史都会
  保留。
- 迁移是**分版本、失败安全**的：迁移失败不会破坏已有数据。
- **没有自动更新器**，升级需手动。

### 卸载

1. 删除应用目录。
2. `%APPDATA%\opencode-widget\` 中的用户数据默认**保留**；如需彻底清除，请手动
   删除该目录（会一并删除 `config.json`、`secrets.enc`、`usage_remote.db`、
   `server_usage.db`、`formula_cache.json`）。
3. 可选：删除 `%TEMP%\opencode-widget\runtime.json`，以及调试构建下的
   `%TEMP%\widget_snap.log`。

### 疑难排查

- **找不到 OpenCode 数据库：** 显示空状态；请确认 OpenCode 至少使用过一次且
  数据库存在。
- **数据库 schema 不受支持：** 读取器会明确报告不支持，而不是猜测。
- **端口被占用（`8765`）：** 启动会明确失败，**绝不结束其它进程**；若占用者就是本
  悬浮窗后端，则复用现有实例，否则可通过端口环境变量改用其它端口。
- **官方同步不可用：** 回退到最近一次本地状态，可稍后重试。
- **通知未触发：** 通知默认关闭，请先开启，并检查免打扰设置。
- **托盘图标不见了：** Windows 可能把新图标收进溢出区，先检查那里。
- **公式回退：** 远程公式不可达时，保留 last-known-good（`integrity_status:
  unsigned`）。
- **本机服务不可用：** 窗口显示断开状态，重新运行启动器以重启后端。

### 开发

- 运行测试：

  ```bat
  python -m pytest -o addopts="" -q
  ```

  预期：全部通过（本 RC 截定时为 461 passed）。
- 只有在开发 `electron/` 时才需要 Node/Electron；先在 `electron/` 内执行
  `npm install`。
- 若 pytest 无法访问其临时目录，先把 `TEMP`/`TMP` 指向可写目录，例如：

  ```bat
  set TEMP=%LOCALAPPDATA%\Temp\opencode
  set TMP=%TEMP%
  python -m pytest -o addopts="" -q
  ```

### SmartScreen 提示

`0.9.0-rc.1` 是**未签名**的发布候选版，没有 Authenticode 签名，首次运行时
**Windows SmartScreen 可能弹出警告**——这对未签名 RC 是正常现象。
**不要使用任何脚本或手段绕过操作系统的安全警告**；若你不信任该构建，请不要运行。
