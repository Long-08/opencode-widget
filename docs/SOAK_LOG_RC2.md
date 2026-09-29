# RC.2 Soak Log — Real-world Soak & Readiness

> 目的：以真实日常使用验证最新 main（Mini/Compact/Expanded 三态、桌面生命周期、通知、资源占用、状态恢复），
> 并据此判定 `v0.9.0-rc.2` 发布条件。规则：**OBSERVE / RECORD / DO NOT OPTIMIZE**——
> 仅 Blocker / Important 进入修复（TDD + 独立 review），Minor 记录并 defer。
> `v0.9.0-rc.1`（0727c6b）保持不可变；本轮不创建任何新 tag、不发布 binary。

## Environment

- commit（soak 起点）: ded4edc（Day-0 修复后为 812145a，见 Day 0 / F1）
- Windows: Microsoft Windows 11, build 10.0.26100 (24H2)
- display scaling: 175%（devicePixelRatio 1.75）
- monitor count: 1（**multi-monitor not tested** —— 单屏环境，不做模拟）
- Python: 3.12.10
- Electron: 43.3.0
- baseline tests @ded4edc: pytest 638 passed / node 208 passed（13 suites）, 0 failed

## 观察清单（每日按需，勿做仪器化）

- Compact UX：Mini 是否碍事 / 是否频繁手动移动 / Mini→Compact 频率（几乎不用|偶尔|经常）/ 是否直接跳 Expanded / 误触展开
- Mini：内容是否足够 / 过宽过高 / 文字溢出 / 状态颜色可读性 / Key 状态是否有价值
- 误触：拖动 Mini 误开 Compact / 单击区过大 / 抢焦点 / Compact 意外收起
- 窗口位置：拖动、贴顶、拖离、Compact/Expanded 展开、收回 Mini —— 是否跳屏/漂移/出屏
- 生命周期：启动、sleep/wake、lock/unlock、正常退出、单实例（重复启动 → focus existing）
- 通知（若开启）：重复提醒 / cooldown 刷屏 / quiet hours / 点击 → Expanded+Forecast
- 资源：空闲 CPU（Electron/Python）、内存趋势（启动 vs 数小时 vs 一天）、%TEMP%\opencode-widget 增长、widget_snap.log（DEBUG 关闭时应保持不存在）
- 数据：Timeline 断层、Agent/Model 自动出现、Matrix 溢出、Forecast disclaimer/估计语义、reader schema 状态
- 网络：Mini 不应维持 Expanded 级请求模式；离线时 local 数据仍可用、公式 fallback、无通知刷屏

## Day 0 — 2026-09-29（soak 启动日；自动化 + 半自动化基线，由开发代理执行）

### Baseline
- pytest 638 passed / node 208 passed，0 failed（@ded4edc）

### Lifecycle（真实进程，非 stub）
- 单实例（Electron 层）：PASS —— 第二次启动立即静默退出（gotLock=false），无第二窗口/托盘/进程
- 正常退出清理：PASS —— 优雅退出后 Electron 0、data_server 0、runtime.json 已删、temp 目录 0 文件
- Debug log 门控：PASS —— 无 OPENCODE_WIDGET_DEBUG 时 widget_snap.log 保持不存在
- Temp 卫生：PASS —— %TEMP%\opencode-widget 起止均 0 文件，无积累
- **Hard-kill recovery：PASS** —— 强杀 Electron 后，server 在最后认证流量后 ~285s 由空闲看门狗自退出，且自清理 runtime.json（0 进程残留）

### Issues

- **F1（Important → 已修复，commit 812145a）**：Windows 下 `data_server` 可被重复启动并**静默双绑 127.0.0.1:8766**
  （`ThreadingHTTPServer` 继承 `allow_reuse_address`；SO_REUSEADDR 使 OSError 守卫永不触发，Phase 6A 守卫在 Windows 上为死代码）。
  实证：两个 LISTENING 共存于 8766；第二实例覆写 runtime.json，第一实例从此收不到认证流量 → 空闲看门狗永不退出 →
  **永久孤儿 server**（复现路径：双击启动器两次——第二次 electron 被单实例挡掉，但其启动的 server 已双绑）。
  修复：bind 之前决策所有权——runtime.json 指向另一存活且健康（/api/health 探测）的实例时直接退出（"widget already running; exiting"）；
  陈旧 runtime（死 pid/探活失败/信息畸形）照常放行并由 startup hygiene 清理；OSError 路径保留为 POSIX/竞态兜底。
  活体验证：第二实例让位退出、仅剩 1 监听者、hard-kill 看门狗行为不变。
  测试：tests/test_server_single_instance.py（7 用例，先 RED 后 GREEN）；全套件 645 passed。
  - 时间：Day 0 15:07–15:30；模式：n/a（生命周期测试）；触发：手动双启动 server；可复现：是（修复前）

- 观察（不修）：bash `python` 解析到 WindowsApps 别名包装器（父进程持有真解释器子进程），测试脚本需用
  Python312 绝对路径 + 按 PID 追踪进程，否则进程检测不可靠。属于测试环境观察，非产品缺陷。

### Resource observations
- 启动基线：Electron ~3 进程常态（main+gpu+renderer）；空闲 CPU 无持续占用（任务管理器目测，无仪器化）
- temp/log：0 增长

## Day 1 —（待真实使用后填写）

### Normal usage
- Mini:
- Compact:
- Expanded:

### Lifecycle
- start:
- sleep/wake:
- lock/unlock:
- quit:

### Notifications
- enabled:
- events:

### Resource observations
- CPU:
- memory:
- temp/log:

### Issues
- none

## Day 2 —（待真实使用后填写）

（同 Day 1 模板）

## Day 3 —（待真实使用后填写）

（同 Day 1 模板）

## Day 4–7（可选延伸）

（同 Day 1 模板；5–7 天为优选）

## Soak Gate（§41）

- [ ] ≥3 个真实工作日（5–7 优选）
- [ ] 反复正常 start/quit
- [ ] sleep/wake 实测
- [ ] lock/unlock 自然发生时记录
- [ ] Mini 作为实际默认态使用
- [ ] Compact/Expanded 自然使用
- [ ] 无未决 Blocker
- [ ] 无未决 Important
- [ ] soak 结束 regression：pytest + node 全绿（用真实 TEMP 环境跑：`$env:TEMP="$env:LOCALAPPDATA\Temp\opencode"` 后 `python -m pytest -o addopts="" -q`）
- [ ] 独立 readiness review 给出 APPROVE FOR RC.2 / NOT READY

## Findings Ledger

| # | 日期 | 级别 | 摘要 | 状态 |
| --- | --- | --- | --- | --- |
| F1 | Day 0 | Important | Windows 重复启动 data_server 静默双绑 → 永久孤儿 server | FIXED (812145a) |
| — | 此前轮次 | Cosmetic(延期) | X/timeRange 无效果；app.js 死尺寸常量；两语句同行 | deferred（不因 soak 顺手修） |
