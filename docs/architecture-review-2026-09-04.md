# Wallpace 架构评审 + 待优化项分级清单

- **评审人**：高见远（架构师）
- **日期**：2026-09-04
- **基线**：`1382ad9`，`110 passed / 1 skipped`（已复跑确认）
- **范围**：`src/` 全量 19 个 .py / 4100 行；本次只评审，未改动任何源码
- **验证方式**：所有 P0 结论均在 offscreen 环境下实际执行代码验证，非静态推断

---

## 0. 结论速览

| # | 结论 | 级别 | 依据 |
|---|------|------|------|
| 1 | `_GalleryThumbWidget.cleanup()` 在生产环境抛 `AttributeError`，导致**第二次扫描起**缩略图条/信息卡/底栏全部静默失效 | **P0** | 实跑复现 |
| 2 | `switch_mode=interval_minutes` 且 `interval_minutes=null` 时 `main.py:140` **启动崩溃** | **P0** | 实跑复现 |
| 3 | `GalleryPage.refresh()` 无虚拟化，800 张图 **7.8 秒主线程假死**，且每次切到图片库页都触发 | **P0** | 独立进程实测 |
| 4 | 两个本地配置文件被 git 跟踪，`.gitignore` 对其无效 | **P0** | `git ls-files` 确认 |
| 5 | 暂停状态下切换模式 → 调度器被**静默恢复**，托盘/顶栏显示与实际状态背离 | P1 | 实跑复现 |
| 6 | **`_setup_ui`(120) / `_build_preview_page`(122) 不该拆**；真正该做的是搬走 3 组职责 | — | 见第 2 节 |

> **最重要的判断**：本项目当前的架构问题**不是"文件太大"，而是"一处隐藏的运行时异常 + 一处无虚拟化的列表"**。继续切分 `window.py` 对这两个问题零帮助，反而会稀释注意力。

---

## 1. 架构现状评估

### 1.1 分层与依赖方向：干净（正面结论）

```
main.py
  ├── src/core/     settings, image_library, scheduler, wallpaper_manager, autostart_registry
  └── src/app/      window, sidebar, tray, theme, icon, image_loader
        ├── pages/  gallery_page, settings_page
        └── widgets/ preview_card
```

**实测结论**：`src/core/` 下**没有任何**对 `src/app/` 的运行时导入（仅有 `scheduler.py` 中 `TYPE_CHECKING` 下的类型引用）。依赖严格单向 `app → core`，**无循环依赖、无反向越界**。`core/` 不依赖 PySide6（除 scheduler 用 QTimer，延迟导入），具备可测性基础。这是一处值得肯定的设计。

### 1.2 但 `pages/` 内部存在两种互相矛盾的拆分风格

上一轮拆分同时引入了两个 Page，却采用了**截然不同**的职责划分：

| | `SettingsPage` | `GalleryPage` |
|---|---|---|
| 持有业务对象 | ❌ 只持有 `settings` + 回调字典 | ✅ 直接持有 `ImageLibrary` 实例 |
| 业务逻辑位置 | MainWindow（回调委托） | 页面内部（`_Tile._toggle_fav` 直接改 library） |
| 持久化 | 由 MainWindow `_persist_skip_favorites` | 通过 `on_persist` 回调（但 `_Tile` 自己调） |
| 可单测性 | 好（纯视图） | 差（需要真实 library + 真实 widget） |

**问题**：`GalleryPage` 不是"视图层"，而是一个**自治子系统**。它绕过了 MainWindow 直接读写 `ImageLibrary`（`gallery_page.py:108-119`），MainWindow 无法感知收藏/跳过状态变化，只能靠 `on_persist` 回调做事后同步。这导致状态变更有两个入口（预览页的 `_handle_favorite` 和画廊页的 `_Tile._toggle_fav`），逻辑重复且易失步。

**建议**：统一为 `SettingsPage` 的"视图 + 回调委托"风格——`GalleryPage` 只负责渲染传入的 path 列表 + 发出意图信号，library 的读写收敛回 MainWindow（或后续的 GalleryController）。

### 1.3 MainWindow 职责盘点

`MainWindow` 现有 57 个方法，但"方法多"不是问题，**职责杂**才是。实际混合了 6 类职责：

| 职责 | 方法数 | 行数(约) | 是否属于 MainWindow |
|---|---|---|---|
| ① 窗口/页面装配 | 6 | ~300 | ✅ 是 |
| ② 设置业务逻辑（注册表、调度器、目录增删） | 6 | ~120 | ❌ 应为 SettingsController |
| ③ 图片库缩略图条 + 懒加载状态机 | 6 | ~110 | ❌ 应为 GalleryStrip 组件 |
| ④ 异步扫描编排（_scan_running/_scan_pending 状态机） | 5 | ~60 | ❌ 应为 ScanController |
| ⑤ 状态展示（顶栏/底栏/信息卡/时钟） | 8 | ~130 | ⚠️ 可拆 StatusBarController |
| ⑥ 壁纸切换业务编排 | 6 | ~70 | ✅ 勉强是 |

**结论**：MainWindow 过重，但病灶是 ②③④ 三组"有自己状态的子系统"寄生在主窗口里，而不是 `_setup_ui` 那 120 行布局代码。

### 1.4 封装越界（实测 13 处）

UI 层直接读写其它对象的私有成员：

```
window.py:712,713,742   self._scheduler._interval_minutes      ← 最严重，见 P0-2 / P1-2
window.py:1091         self._library._directories
window.py:821,823,824  self._preview_card._current_path
window.py:838,840,842,845,846  self._preview_card._current_path
window.py:1025         self._sidebar._action_buttons
window.py:1086         widget._image_path
```

其中 `self._scheduler._interval_minutes` 是**写操作**，等于让 UI 层直接改写 core 对象的内部状态，绕过了 `Scheduler` 唯一能保持定时器一致性的入口。这是 P0-2 和 P1-1 两个缺陷的直接成因。

---

## 2. window.py 剩余拆分价值判断

### 2.1 明确结论

> **`_setup_ui`（120 行）和 `_build_preview_page`（122 行）都不应该拆。**
>
> **唯一值得做的提取是 `_GalleryThumbWidget` 及其懒加载状态机 → 独立 `GalleryStrip` 组件（约 110 行）。**

### 2.2 为什么这两个方法不该拆

**理由一：它们是声明式装配代码，不是逻辑。**

通读 `_setup_ui`（245-361）与 `_build_preview_page`（365-485）：两者几乎不含分支与循环，本质是"创建控件 → 设属性 → 加进布局"的线性脚本，圈复杂度≈1。这类代码的认知负担来自**控件的数量**，而不是代码的组织方式。把它切成 `_build_top_bar()` / `_build_bottom_bar()` / `_build_preview_info_grid()`，只是把一段从头到尾能读完的脚本分卷成需要跳转 5 次才能看全的碎片——**跳转成本上升，理解成本不变**。

**理由二：拆分收益与三个指标都不匹配。**

判断"该不该拆"的正确指标是 **内聚性 / 变更频率 / 可测性**，不是行数：

| 指标 | `_setup_ui` | `_build_preview_page` |
|---|---|---|
| 内聚性 | 与 MainWindow 同生命周期，拆分后新类只剩一个 `build()` 方法 → 退化为"函数伪装成的类" | 同左 |
| 变更频率 | 低（布局定型后基本不动） | 低 |
| 可测性 | 拆了也不可单测（要 QApplication + 完整控件树），收益≈0 | 同左 |

**理由三：算术上也不划算。**

1151 行里这两个方法占 242 行（21%）。就算全部搬走，window.py 仍有 ~900 行、方法粒度不变。**为 21% 的声明式代码引入 2-3 个新的间接层，是典型的"为拆而拆"。**

**理由四：项目已有前车之鉴。**

`_build_gallery_placeholder`（527-543）就是因为"先建了占位版、后来换成真 `GalleryPage`"而留下的死代码。声明式 UI 代码被切得越碎，这种"旧版本没删干净"的概率越高。

### 2.3 唯一值得做的提取：`GalleryStrip`

**不是因为行数，而是因为它是一组内聚的、有自己状态的子系统**：

```
可整体搬走的部分（window.py）
  ├── class _GalleryThumbWidget(QLabel)          108-170   73 行
  └── 懒加载状态机
        _refresh_gallery_thumbnails()            975-993
        _append_gallery_batch()                  995-1006
        _maybe_load_more_thumbnails()            1008-1015
        _highlight_current_gallery_item()        1081-1086
        状态字段 _gallery_pending / _gallery_items / GALLERY_THUMB_BATCH / GALLERY_LOAD_AHEAD_PX
```

搬走的收益是**实打实的**：
1. 这组代码自带 4 个实例状态字段，是MainWindow 里唯一一组"独立状态机"，搬走后 MainWindow 少 4 个字段；
2. 可与 `GalleryPage` 合并讨论——两者是**同一功能的重复实现**（都是"缩略图 + 懒加载"），目前预览页横向条有懒加载（每批 10 张），`GalleryPage` 完全没有。合并后能一次性解决 P0-3；
3. `GalleryStrip` 可以脱离 MainWindow 单测（传入 path 列表 + 回调即可）。

### 2.4 正确的方向：搬走职责，而不是切碎方法

按 2.3 的思路继续，优先级排序（预计 window.py 1151 → ~620 行）：

| 顺序 | 提取目标 | 行数 | 收益 |
|---|---|---|---|
| 1 | `GalleryStrip`（缩略图条 + 懒加载状态机） | ~110 | 解 P0-3 的一半；消灭 P0-1 的载体 |
| 2 | `ScanController`（`_scan_running`/`_scan_pending` 状态机 + `_ScanJob`/`_ScanSignals`） | ~90 | 扫描逻辑可单测；MainWindow 少 5 个字段 |
| 3 | 设置业务逻辑下沉为 `SettingsController` | ~120 | 解 P0-2；消除 `_scheduler._interval_minutes` 越界写 |
| 4 | `StatusBarController`（顶栏/底栏/信息卡/时钟） | ~130 | 展示逻辑集中 |

**注意**：1 和 2 应当**在修完 P0-1/P0-2 之后**再做。带着已知缺陷做重构，会把缺陷复制进新结构。

---

## 3. 待优化项分级清单

### P0 — 必须马上做

#### P0-1　`cleanup()` 在生产环境抛异常，第二次扫描起 UI 静默失效

- **位置**：`src/app/window.py:148-152`（`_GalleryThumbWidget.cleanup`）、调用点 `window.py:978-983`
- **问题**：本机 PySide6 的 `QMetaObject.Connection` **没有 `disconnect()` 方法**。实测：

  ```
  hasattr(QMetaObject.Connection, 'disconnect') = False
  _GalleryThumbWidget.cleanup() -> AttributeError:
      'PySide6.QtCore.QMetaObject.Connection' object has no attribute 'disconnect'
  ```

  `cleanup()` 内没有 `try/except`，异常直接冒泡。`_refresh_gallery_thumbnails` 的清理循环在**第一个** widget 处就中断（因 `reversed(range(...))`，即最后一个缩略图）。

- **影响（连锁）**：
  1. `widget.deleteLater()` 从未执行 → **旧缩略图从不移除，每次扫描重复追加，横向缩略图条无限膨胀**；
  2. `_refresh_gallery_thumbnails` 整体中断 → `_apply_scan_results` 中后续的 `gallery.refresh()`(952)、`_update_info_cards()`(961)、`update_bottom_bar()`(962)、空状态引导(953-960) **全部被跳过**；
  3. 异常被 `_on_scan_finished` 的 `except Exception: logger.exception(...)`（933-934）吞掉 → **界面不报错、不崩溃，只是"看起来没反应"**，极难自查；
  4. 首次扫描不受影响（`_gallery_layout` 为空，循环不执行）→ 表现为"刚装上好用，添加了第二个文件夹之后就不刷新了"。

- **为什么测试没发现**：`tests/conftest.py:39-42` 检测到 `Connection` 没有 `disconnect` 后，**打了一个返回 `None` 的 no-op 补丁**。这个补丁让 110 个测试全部通过，同时**完美掩盖了生产环境的真实行为**。

- **建议方案**（三选一，推荐 ①）：
  1. **改 `image_loader` 为按 path 分发的回调注册表**，不再让每个 widget 各自 `connect` 全局信号。`image_loader.load_async(path, size, on_ready)` 内部维护 `dict[str, list[callback]]`，解码完成只回调对应 path 的订阅者。一举解决 P0-1 + P1-4，且不再需要 `disconnect`。
  2. 保留广播，但 `cleanup()` 改用 `self._signals.ready.disconnect(self._on_image_ready)`（`Signal.disconnect(slot)` 是可用 API），并加 `try/except RuntimeError`。
  3. 最小改动：给 `cleanup()` 加 `try/except Exception: pass`（与 `_Tile.cleanup` 一致）——**只能止血，不能解决缩略图条重复膨胀**。

- **改动量**：方案①约 60 行（image_loader 30 行 + 三个调用点各 5-10 行）；方案③约 3 行。

---

#### P0-2　`switch_mode=interval_minutes` + `interval_minutes=null` → 启动崩溃

- **位置**：`src/core/scheduler.py:206`（raise）、`src/main.py:140`（无保护）、成因在 `src/app/window.py:704-714`
- **问题**：`_on_mode_changed` 在切到「间隔时间」时做了兜底：

  ```python
  window.py:706-713
  interval_val = self._settings.get("interval_minutes", 60)
  if interval_val is None or interval_val <= 0:
      interval_val = 60                       # 只给了 UI 和 scheduler
  self._settings_page.set_interval_value(interval_val)
  if self._scheduler and self._scheduler._interval_minutes != interval_val:
      self._scheduler._interval_minutes = interval_val   # 直接改私有字段
  # ← 唯独没有 self._settings.set("interval_minutes", interval_val)
  ```

  而 `switch_mode` 在 702 行**是持久化了的**。于是配置变成 `{switch_mode: "interval_minutes", interval_minutes: null}`。

  下次启动 `main.py:99` 读出 `interval_minutes=None` → `Scheduler(interval_minutes=None)` → `main.py:140 scheduler.start(...)` 无 try/except → 实测：

  ```
  start() 抛出 ValueError: interval_minutes 必须 > 0
  ```

- **影响**：**启动即崩溃**。打包成 `--windowed`（无控制台）后表现为"双击图标 → 托盘闪一下 → 无声无息退出"，用户完全无法自查。触发路径很普通：全新配置 → 设置里选「间隔时间」→ 重启。

- **建议方案**：
  1. `window.py:707-709` 补 `self._settings.set("interval_minutes", interval_val)`（根治，1 行）；
  2. 同时给 `Scheduler` 加公开 setter `set_interval_minutes(n)`，内部处理"运行中则重启定时器"，替换 712/713/742 三处私有字段直写（见 P1-2）；
  3. `main.py:139-141` 用 `try/except ValueError` 包住 `scheduler.start()`，失败时降级为 `manual` 并记 ERROR——**任何调度器异常都不应阻止主窗口显示**。

- **改动量**：约 15 行。

---

#### P0-3　`GalleryPage.refresh()` 无虚拟化，图库稍大即假死

- **位置**：`src/app/pages/gallery_page.py:201-220`（refresh）、`222-225`（showEvent）、`window.py:952`
- **问题**：`refresh()` 对**全部**图片同步创建 `_Tile`（每个 = QWidget + QLabel + 2×QPushButton + 2×布局 ≈ 6 个 QObject），无分页、无虚拟化、无复用。`showEvent` 每次切页都完整重建。

- **实测数据**（独立进程，排除跨轮次残留干扰）：

  | 图片数 | refresh() 耗时 | 每张 | 体感 |
  |---|---|---|---|
  | 100 | 185 ms | 1.85 ms | 可感知 |
  | 200 | 389 ms | 1.94 ms | 可感知 |
  | 400 | 1 093 ms | 2.73 ms | 明显卡顿 |
  | 800 | 7 768 ms | 9.71 ms | **假死** |

  每张成本随 N 上升（1.85 → 9.71 ms），呈**超线性**。1200 张时单次重建达 20 秒。

- **影响**：每次以下操作都会冻结主线程数百毫秒到数十秒：
  - 点击侧边栏「图片库」图标（`showEvent` → `refresh`）
  - 每次扫描完成（`window.py:952`）
  - 每次点收藏（`window.py:850`）
  - 切换 全部/收藏/已跳过（`gallery_page.py:187`）

  **这直接抵消了此前"UI 卡死已修"的成果**：当时的修复（异步扫描 + 缩略图懒加载）只覆盖了预览页的横向缩略图条，而同一轮重构新增的 `GalleryPage` 在自己的路径上重新引入了更严重的卡顿。

- **建议方案**（按性价比）：
  1. **虚拟化**：`GalleryPage` 只创建可视区域内的 tile（约 4×3=12 个）+ 上下各一屏缓冲，滚动时复用 widget（`_Tile.set_path()` 而不是重建）。这是根治，约 80 行。
  2. **兜底分页**：`refresh()` 首屏只建 50 个，滚动到底追加。约 30 行，缓解但不根治。
  3. **去抖**：`showEvent` 增加"数据未变更则跳过 refresh"的判断，避免无谓重建。约 5 行，**建议无论如何都做**。

- **改动量**：方案①+③ 约 85 行。

---

#### P0-4　两个本地配置文件被 git 跟踪，`.gitignore` 对其无效

- **位置**：`.wallpace.json`（393 B）、`.wallspace.json`（373 B）、`.gitignore:38`
- **问题**：`git ls-files` 确认 `.wallpace.json` 已被跟踪；`.gitignore` 只写了 `.wallpace.json` 且**对已跟踪文件不生效**。`.wallspace.json`（历史拼写错误遗留）**连 .gitignore 都没写**。两者内容实质相同（都含 `D:/my_project/source picture` 等本地路径）。
- **影响**：① 本地目录结构等隐私信息入库；② 每次运行若写入会持续产生 diff 噪音；③ 多人/多机协作时冲突。
- **建议方案**：
  ```bash
  git rm --cached .wallpace.json .wallspace.json    # 保留工作区文件，仅停止跟踪
  # 或直接删除（配置已迁移到 %APPDATA%/.wallpace/config.json，两者均为纯遗留物）
  ```
  `.gitignore` 补两行 `*.wallpace.json` / `*.wallspace.json`。

  ⚠️ 注意：`src/core/settings.py:37-40` 的 `LEGACY_CONFIG_PATHS` 只列了 `.wallspace.json`，**没有列 `.wallpace.json`**（正确拼写）。若用户真实配置在 `.wallpace.json` 里，迁移逻辑不会读它——建议迁移路径一并补上，或确认后直接删除两文件。
- **改动量**：3 条命令 + 2 行 .gitignore。

---

### P1 — 近期做

#### P1-1　暂停状态下切换模式，调度器被静默恢复

- **位置**：`src/core/scheduler.py:88-94`（mode setter）+ `src/app/window.py:719-724`
- **问题**：实测（manual 模式启动 → 暂停 → 切到 interval_minutes）：
  ```
  用户暂停后      : is_running=True  is_paused=True   is_active=False
  切到 interval 后 : is_running=False is_paused=False  is_active=False
  ```
  setter 的 `elif self._is_running:` 分支（92-94）执行 `self.stop()` 后再 `self.resume()`，而 `resume()` 首行就是 `if not self._is_running: return`（143-145）——**已被 stop() 置为 False，resume 直接失效**。随后 `window.py:719-724` 的 `is_running` 判断 + `start()` 把调度器彻底拉起（`paused=False`）。

  由于 `start()` 无条件清 `_is_paused`，**任何模式变更都会让暂停状态丢失**（daily→interval 同样如此）。
- **影响**：用户从托盘暂停后去设置里改模式 → 自动切换悄悄恢复，但托盘菜单仍显示"继续切换"、顶栏仍显示"● 已暂停" → **UI 与实际状态背离**，用户以为暂停了，壁纸却在换。
- **建议方案**：① `Scheduler` 增加 `is_paused` 的持久化语义，`start()` 不清暂停位，或提供 `start(preserve_paused=True)`；② `window.py:719-724` 改为"记录暂停态 → 重启后恢复暂停态 → 同步 `self._tray.update_pause_status()` 与 `update_top_status()`"。
- **改动量**：约 20 行。

#### P1-2　`Scheduler` 缺少 `interval_minutes` 公共 API，UI 直写私有字段

- **位置**：`window.py:712, 713, 742`
- **问题**：`Scheduler` 只有 `interval_minutes` 的**只读** property，没有 setter。UI 只能直写 `_interval_minutes`。这样做绕过了定时器重建——`window.py:713` 改了值但**不重启定时器**，只有 742 之后手动 stop/start 才生效，两条路径行为不一致。
- **建议方案**：新增
  ```python
  def set_interval_minutes(self, minutes: int) -> None:
      if minutes is None or minutes <= 0: raise ValueError(...)
      self._interval_minutes = minutes
      if self._is_running and not self._is_paused and self._mode == self.SWITCH_MODE_INTERVAL:
          self._pause_timers(); self._start_interval_timer()
  ```
  三处私有访问全部替换。
- **改动量**：约 15 行。

#### P1-3　`_handle_skip` 无视觉反馈

- **位置**：`window.py:819-825`
- **问题**：跳过当前图后只写了配置，**没有切换壁纸、没有刷新预览卡、没有更新底栏计数、没有刷新缩略图高亮**。用户点「跳过」后界面几乎无变化。对比 `_handle_favorite`（836-851）会刷新画廊页和侧边栏，两者行为不一致。
- **建议方案**：跳过后调用 `_handle_switch()` 换一张，并 `update_bottom_bar()` / `_refresh_gallery_thumbnails()`。
- **改动量**：约 5 行。

#### P1-4　`image_loader` 全局广播信号的 O(N²) 扇出

- **位置**：`src/app/image_loader.py:25, 42-51`；调用点 `gallery_page.py:92`、`window.py:131`、`preview_card.py:43`
- **问题**：所有消费者都连到**同一个**模块级 `ready` 信号，每个消费者在槽里做 `if path != self._path: return`。实测扇出为 O(N)：连接 N 个 slot 时单次 emit 触发 N 次回调。于是 N 张图全部解码完 ≈ **N² 次主线程回调**：

  | 图片数 | 回调次数（量级） |
  |---|---|
  | 300 | 90 000 |
  | 600 | 360 000 |
  | 1000 | 1 000 000 |

  （补充实测结论：连接数本身**不是永久泄漏**——事件循环转动后 widget 被销毁，Qt 会自动断连，实测 slot 数回落到基线。真正的成本是**单次 refresh 的爆发窗口内**的 N² 回调，叠加在 P0-3 的重建耗时上。）

- **建议方案**：同 P0-1 方案①——改为按 path 分发的订阅表，解码完成只回调对应订阅者。
- **改动量**：约 30 行（与 P0-1 合并实施）。

#### P1-5　死代码清理（范围比预估的更大）

实测确认 `src/` 内**只有定义、零调用点**的成员：

| 位置 | 成员 | 备注 |
|---|---|---|
| `window.py:527-543` | `_build_gallery_placeholder` | 占位版，被 `GalleryPage` 取代 |
| `sidebar.py:274-278` | `set_favorite_state` | 函数体只有一行 `logger.debug`，未完成实现 |
| `sidebar.py:289-290` | `update_status` | 函数体是 `pass` |
| `tray.py:62-64` | `set_active` | 从未调用 |
| `preview_card.py:250-254` | `cleanup` | 从未调用，**且同样会因 `disconnect()` 抛异常** |
| `preview_card.py:240` | `clear_preview` | 仅测试引用 |
| `image_library.py:251,258,280,289` | `clear_skip` / `clear_favorites` / `to_dict` / `from_dict` | 仅测试引用 |
| `wallpaper_manager.py:112-136` | `verify_set` | 仅测试引用 |

- **建议**：`_build_gallery_placeholder`、`set_favorite_state`、`update_status`、`set_active`、`preview_card.cleanup` 直接删；`core` 层的 5 个方法若计划做"清空跳过列表/清空收藏"UI 则保留，否则一并删（测试同步删）。
- **改动量**：约 -90 行。

#### P1-6　git 仓库历史损坏 + 游离 worktree

- **位置**：仓库本身；`D:/my_project/wallpace.worktrees/276ba404-...`
- **问题**：`git log` 报 `fatal: Failed to traverse parents of commit bc80c3ac`（缺失 `0db1da95`）；`git branch -vv` 显示 `agents/276ba404-...` 指向一个停在 "Initial commit" 的游离 worktree，疑似 Agent session 残留。
- **影响**：开发/打包/运行不受影响（master 可达的 7 个 commit 完好），但 `git gc` / `git fsck` / `rebase` / `bisect` 全部不可用，**一旦需要回滚就抓瞎**。
- **建议**：① 清理游离 worktree：`git worktree remove` + `git branch -D agents/276ba404-...`；② 彻底修复需联网 re-clone（当前状态可接受，但**务必先备份**）；③ 最低限度：把当前工作区打包一份快照存到仓库外。
- **改动量**：2 条命令（清理）；re-clone 约 10 分钟。

#### P1-7　1 Hz 常驻时钟定时器

- **位置**：`window.py:579-605`
- **问题**：`_clock_timer` 每秒触发 `_update_clock()`，更新 3 个标签并调用 `_get_next_switch_text()`。**与当前是否停在时钟页无关**。
- **影响**：这是常驻后台的壁纸应用，1 秒唤醒会阻止 CPU 进入深度休眠、持续占用定时器资源，对笔记本续航有可测量影响。功能上无正确性风险。
- **建议**：仅在时钟页可见时启动（`QStackedWidget.currentChanged` 或 page 的 `showEvent`/`hideEvent`），其余时候降级为 30 秒一次或直接停表。
- **改动量**：约 10 行。

---

### P2 — 可选

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| P2-1 | **裸 `except:`** 会吞掉 `SystemExit`/`KeyboardInterrupt`；且 `_start_clock_timer` 的"清理已有定时器"分支是死代码（构造函数里只调用一次） | `window.py:585, 589` | 改 `except Exception:`；删掉不可达分支 |
| P2-2 | `open_settings` 硬编码页面索引 `3` | `window.py:1107` | 改为具名常量或从 `NAV_ITEMS` 推导 |
| P2-3 | 用猴子补丁覆盖 Qt 虚方法：`self._top_status.mousePressEvent = self._on_top_status_click`、`self._thumb.mousePressEvent = lambda...` | `window.py:274`、`gallery_page.py:63` | 改用 `installEventFilter` 或 `clicked` 语义的自定义子类；当前写法依赖 PySide6 实现细节 |
| P2-4 | **`_remove_directory()` 无条件 `dirs.pop()` 删掉最后一个目录**，与用户选择无关；源码注释自认"简易方式" | `window.py:754-767` | 设置页已有逐条 `✕` 删除（`on_remove_single_dir`），建议直接移除「移除文件夹」按钮与该回调，消除误删风险 |
| P2-5 | `Settings.save()` 每次 `set()` 都全量写盘，无节流/原子写 | `settings.py:131-145` | 改为临时文件 + `os.replace` 原子替换，避免写一半崩溃导致配置损坏 |
| P2-6 | `faulthandler` 的日志文件句柄 `open(...)` 从不关闭 | `main.py:25` | 可保留（生命周期与进程一致），但建议加 `# noqa` 注释说明是有意为之 |
| P2-7 | 根目录杂物：`temp_repro.py`（1138 B）、空目录 `D/Desk`、未跟踪的 `.agnes/`（持续污染 `git status`） | 根目录 | 删除前两者；`.gitignore` 补 `.agnes/` |
| P2-8 | 常量分散：`MODE_DISPLAY_MAP` 在 `settings_page.py:28`，`window.py:693-697` 又定义了一份并在每次调用时反转 | `window.py:693-699` | 统一到 `settings_page` 导出，反转映射提到模块级 |
| P2-9 | `update_bottom_bar` 直读 `self._library._directories` | `window.py:1091` | 用已有的 `directory_count` property 或新增 `directories` 只读 property |

---

## 4. 风险点

### 4.1 线程安全

| 风险 | 位置 | 说明 | 建议 |
|---|---|---|---|
| **扫描线程与主线程共享 `ImageLibrary`** | `_ScanJob.run` → `library.scan()`（window.py:90-92）vs 主线程 `list_available()`/`get_random()` | 当前靠 GIL + `self._all_images = unique` 的原子赋值侥幸安全；`scan()` 内部迭代 `self._directories` 期间若主线程 `add/remove_directory` 会破坏迭代。现有 `_scan_running` 守卫覆盖了三个入口，**但 `Scheduler._do_interval_switch` 不走该守卫**，可与扫描并发调用 `get_random()`。虽然当前不会崩，但这是隐式契约，任何新增的 library 变更入口都可能踩雷 | 把 library 的读写收敛到一个 facade，扫描期间加锁或做快照 |
| **`QThreadPool` 析构可能阻塞退出** | `window.py:222` `QThreadPool(self)` 以 MainWindow 为父 | 窗口析构时若扫描仍在进行，线程池析构会等待任务完成 → 退出被拖住数秒；`closeEvent`（1123-1146）**没有停止扫描或等待线程池** | `closeEvent` 真正退出分支中先调 `self._scan_pool.clear()` + `waitForDone(1000)` |
| **`Scheduler` 的 QTimer 无父对象** | `scheduler.py:217, 226` | `QTimer()` 无 parent，仅靠 `self._timer` 持有；`_pause_timers` 用 `deleteLater()`（依赖事件循环）。若应用退出时事件循环已停，定时器不会及时析构 | 给 QTimer 传父对象或在 `stop()` 中显式处理 |

### 4.2 Qt 生命周期与资源

| 风险 | 位置 | 说明 |
|---|---|---|
| **`deleteLater()` 依赖事件循环** | `window.py:983`、`gallery_page.py:209` | 在 `refresh()` 这类同步密集循环里连续 `deleteLater()` 而不让事件循环转动，widget 不会真正销毁，连接与内存会短期堆积（实测：不转事件循环时连续 refresh 从 358 ms 退化到 1708 ms）。P0-3 修复后此压力自然缓解 |
| **`_Tile` / `_GalleryThumbWidget` 的 `cleanup()` 实际无效** | `gallery_page.py:121-126`（被 try/except 吞掉）、`window.py:148-152`（抛异常） | 详见 P0-1 |
| **`TrayIcon` 与 `MainWindow` 互相持有** | `window.py:356` `TrayIcon(self)`、`tray.py:26` `self._main_window` | 形成引用环。靠 `closeEvent` 中 `self._tray.deleteLater()`（1144）断开，但如果走"最小化到托盘"分支（1130-1137）则不断开——这是**有意的**（要保留托盘），但需确保 `quit_app()` 一定被调用，否则进程无法退出 |
| **`closeEvent` 最小化分支未停调度器** | `window.py:1130-1137` | 有意为之（切换需继续工作），但托盘退出路径只有 `tray._on_exit → quit_app` 一条。若托盘图标因系统原因不可用，用户将**无法退出应用**（无窗口、无托盘） | 增加兜底：连续两次关闭请求强制退出，或在托盘不可用时降级为直接退出 |

### 4.3 可维护性

| 风险 | 说明 |
|---|---|
| **`conftest.py` 的 no-op 补丁掩盖真实缺陷** | `tests/conftest.py:39-42` 给 `Connection.disconnect` 打了返回 `None` 的补丁，使 110 个测试在生产环境行为不同的前提下全部通过。这是 P0-1 潜伏至今的根本原因 |
| **测试盲区** | 现有测试未覆盖：`GalleryPage` / `_Tile`（本次新增的最大模块，P0-3 就在这里）、`_refresh_gallery_thumbnails` 的二次重建（P0-1 触发路径）、`MainWindow` 与 `Scheduler` 的模式切换交互（P1-1）。`test_window.py` 是否存在对这些的弱覆盖需确认 |
| **异常处理过宽** | `window.py:933-934` 与 `863-864` 的 `except Exception: logger.exception(...)` 把 P0-1 这类缺陷变成"静默无反应"。建议对 UI 更新路径保留兜底，但**至少把异常计数暴露到状态栏**，避免"看起来没反应"的黑洞 |

---

## 5. 建议执行顺序

```
第 1 批（止血，同一天完成，互不冲突）
  P0-2  补 interval_minutes 持久化 + main.py 启动保护        ~15 行
  P0-4  git rm --cached 两个配置文件 + 补 .gitignore          3 条命令
  P0-1  最小止血：cleanup() 加 try/except                     3 行
  补测：GalleryPage.refresh 二次重建 / 模式切换后调度器状态     ~40 行

第 2 批（根治 P0-1 + P1-4，一起做）
  image_loader 改为按 path 分发订阅                           ~30 行
  三个调用点（window / gallery_page / preview_card）改造       ~30 行
  → 顺带删除三处 cleanup()

第 3 批（解决 P0-3）
  GalleryStrip 提取 + GalleryPage 虚拟化                      ~110 行
  showEvent 去重 refresh                                      ~5 行

第 4 批（清理 + 一致性）
  P1-1 暂停态、P1-2 Scheduler 公开 API、P1-3 跳过反馈、P1-5 死代码、P1-7 定时器

第 5 批（可选）
  P2 各项 + 第 2 节的重构（GalleryStrip / ScanController / SettingsController）
```

**关键提醒**：第 2 节的重构（搬走职责）**务必排在第 1-3 批之后**。带着 P0-1 和 P0-2 做结构拆分，只会把缺陷复制到新文件里，并且让回归验证的基线变得不可信。

---

## 附：本次评审的验证脚本位置

均在项目外，未污染仓库：

```
%TEMP%/wallpace-audit/v1_scheduler.py      P0-2 / P1-1 复现
%TEMP%/wallpace-audit/v3_perf_detail.py    扇出与瓶颈定位
%TEMP%/wallpace-audit/v4_leak.py           Connection.disconnect 可用性
%TEMP%/wallpace-audit/v5_prod_impact.py    P0-1 生产后果复现
%TEMP%/wallpace-audit/v7_one.py            P0-3 独立进程基准（取 N 为参数）
```
