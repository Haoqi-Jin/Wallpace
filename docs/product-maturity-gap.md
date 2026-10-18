# Wallpace 产品成熟度缺口分析

> 作者：许清楚（产品经理） · 团队：software-wallpace
> 定位：本文件回答「一个能让普通用户**放心装、放心用、出问题知道怎么办、能持续迭代**的产品，还差什么」。
> 范围：**产品成熟度**，不重复已有功能与工程基线（CI/lint/类型/打包由架构师负责）。
> 所有缺口均带 `文件:行` 证据，已逐条 Read 复核。

---

## 1. 目标用户与核心场景

| 画像 | 特征 | 核心诉求 |
|---|---|---|
| A. 桌面美化党（主力） | 攒了几千张壁纸，想每天自动换 | 配一次目录，之后不用管；换到不喜欢的能跳过 |
| B. 多屏办公用户 | 2~3 块显示器，希望每屏不同图 | 逐屏独立；重启后设置还在 |
| C. 轻量工具党 | 只想要个托盘小工具，无感运行 | 开机静默、不弹窗、不占资源、卸载干净 |

**主流程**：首次启动 → 添加图片文件夹 → 选切换模式（每日随机/间隔/手动）→ 后台扫描 → 自动换壁纸 → 托盘常驻；不满意的图「跳过」，喜欢的「收藏」，可点缩略图手动切。

**当前状态一句话**：功能闭环已完整、技术债清理到位（239 测试全绿），但**「首次运行 / 安装卸载 / 出错自救 / 发布合规」四个面向普通用户的面仍是空白**——这正是"能跑"和"成熟"之间的差距。

---

## 2. 成熟度缺口清单

优先级：**P0** = 不修则普通用户会踩坑/不放心；**P1** = 成熟产品应有；**P2** = 锦上添花。
成本：**S** ≈ ≤0.5 天，**M** ≈ 1~3 天，**L** ≈ >3 天。
结论：**本批做 / 下批做 / 延后**。

### P0 — 本批做（"放心装、放心用、装得上也卸得掉"）

| ID | 缺口（用户会遇到什么） | 证据 | 用户影响 | 优先级 | 成本 | 结论 |
|---|---|---|---|---|---|---|
| **G01** | **无单实例保护**：双击两次图标 → 两个托盘图标、两个调度器同时抢设壁纸 | 全仓无 `QLockFile`/单实例逻辑（grep 无命中）；`src/main.py:117-215` 直接 `QApplication` | 所有用户，误双击即发生；壁纸被两个进程轮流覆盖 | P0 | S | ✅ 已做（`src/core/single_instance.py` + main.py 启动时 `SingleInstanceGuard`，第二实例激活首实例后退出） |
| **G02** | **卸载残留**：删掉 `wallpace.exe` 后，HKCU 自启项仍指向不存在的路径（任务管理器留下坏启动项），`~/.wallpace/`（config+日志）也不清理 | `src/core/autostart_registry.py:16-17,71-89` 写死注册；无卸载路径；实测 `~/.wallpace/config.json` 存在 | 卸载/换机用户；坏启动项 + 隐私数据残留 | P0 | M | ✅ 已做（G02 启动自愈 `is_valid`/`cleanup_invalid` + main.py 启动清理；设置页「卸载清理」入口移除自启项并删 `~/.wallpace`） |
| **G03** | **首次运行静默注册开机自启**：`auto_start` 默认 `True`，首启即写注册表，用户从没同意过 | `src/core/settings.py:28` `"auto_start": True`；`src/main.py:175-179` 启动即 `mgr.enable()` | 所有新用户；隐私冒犯 + 卸载残留（见 G02） | P0 | S | ✅ 已做（默认改 `False`；main.py 缺键兜底值同步为 False；设置页复选框兜底值同步为 False） |
| **G04** | **配置损坏即静默重置、且不留备份**：config.json 一旦损坏（断电/磁盘错误），直接回退默认值，用户的目录/收藏/跳过**全部丢失**且只在日志里记一行 | `src/core/settings.py:109-114`：`except JSONDecodeError → dict(DEFAULT_CONFIG)`，不备份坏文件、不通知用户 | 断电/异常关机用户；**不可逆的数据丢失** | P0 | M | ✅ 已做（损坏时备份为 `config.json.corrupt-*` 再重置，`load_warning` 经 `show_platform_warning` 弹给用户；测试 `test_corrupt_json_is_backed_up_and_reset` 覆盖） |
| **G05** | **无 LICENSE 文件，声明与实物不符**：`pyproject.toml` 和 README 都写 MIT，但仓库里没有 LICENSE；PySide6 是 **LGPLv3**（README 写成 "LGPL-2.1"） | `find` 无任何 LICENSE；`pyproject.toml:11` `license={text="MIT"}`；`README.md:153` | 公开分发时法律风险；个人自用可忽略 | P0 | S | ✅ 已做（补 MIT `LICENSE` 文件；README 更正为 LGPLv3 + 指向 LICENSE） |

### P1 — 下批做（"好用、可诊断、可迁移、可信"）

| ID | 缺口 | 证据 | 用户影响 | 优先级 | 成本 | 结论 |
|---|---|---|---|---|---|---|
| **G06** | **日志无轮转**：`wallpace.log` 只增不减，托盘常驻数月 → 无限增长 | `src/main.py:32` 仅 `FileHandler`，全仓无 `RotatingFileHandler`（grep 无命中） | 长期后台用户；磁盘占用、日志打开变慢 | P1 | S | ✅ 已做（`main.py` 改 `RotatingFileHandler`：1MB × 3 备份封顶，旧日志按序号保留） |
| **G07** | **无"打开日志/诊断"入口**：用户不知日志在 `~/.wallpace/logs/`，出错只能干瞪眼 | 无 `QDesktopServices`/`openUrl`（grep 无命中）；`src/main.py:18` LOG_DIR 无 UI 暴露 | 出问题时无法自助上报/排查 | P1 | S | ✅ 已做（新增 `src/core/diagnostics.py` 单一真源；设置页「打开日志目录（诊断）」按钮 + 托盘菜单项，打开失败弹提示而非静默） |
| **G13** | **版本号三处硬编码，UI 显示写死**：UI 版本恒显示 `v0.1.0`，改版本要改 3 个文件 | `src/app/window.py:300` `QLabel("v0.1.0")`；`src/__init__.py:3`；`pyproject.toml:7` | 发版漂移；用户看到的版本永远过期 | P1 | S | ✅ 已做（CHANGELOG 0.2.0 记了"版本号改为单一真源 `src/__init__.py::__version__`，UI 与打包元数据均派生"，源码已无硬编码） |
| **G14** | **README 与代码 ≥7 处不一致 + 目录树过时** | 见 §6 详表 | 误导用户/未来贡献者 | P1 | S | ✅ 已做（2415122 重写 README 目录树补全全部模块、配置路径更正为 `~/.wallpace/config.json`、功能表扩至 20 项；§6 各条已随 G05/G13 等修复逐条消除） |
| **G10** | **`per_monitor_map` 是死配置**：只写不读，重启后不恢复逐屏壁纸，失效的显示器 ID 永不清 | `src/app/window.py:719` 唯一写入点；`src/core/settings.py:33,308` 仅校验；全仓无读取应用 | 多屏用户；重启后逐屏设置"消失"，配置无谓膨胀 | P1 | S | ✅ 已做（`window.py:_restore_per_monitor_wallpapers()` 启动时读取 + 有效性过滤（monitor id 不存在/图片缺失→丢弃）+ 写回清理后的 map；5 个测试覆盖匹配/未知 monitor/图片缺失/全无效/非逐屏分支） |
| **G23** | **图片库页面无空状态提示**：没有图片时网格纯白，用户以为坏了 | `src/app/pages/gallery_page.py:167-206` 无空态分支（仅预览页有引导 `window.py:340-357`） | 新用户；困惑"是不是没扫到" | P1 | S | ✅ 已做（`gallery_page.py` 新增 `_empty_hint` QLabel + `_update_empty_hint()` 方法，筛选后无图片时显示对应引导文案；测试覆盖 5 用例） |
| **G08** | **切换失败无兜底、无反馈**：随机选到刚被删/损坏的图时静默失败（"点了没反应"） | `src/core/scheduler.py:394-408` 仅成功才回调；`src/core/wallpaper_manager.py:56-58` 失败只写日志 | 偶发但高频；用户以为程序卡了 | P1 | M | ✅ 已做（`Scheduler._try_switch_with_fallback()`：取 3 张互不相同候选依次尝试（`ImageLibrary.get_random_candidates`），单张失败自动换下一张；候选全败才调 `set_switch_failure_handler` 回调 → `MainWindow.notify_switch_failed()` 托盘气泡提示；覆盖手动/每日/间隔 3 个触发点） |
| **G09** | **无壁纸填充方式设置**（填充/适应/拉伸/居中/平铺）：非标准比例图显示不全或有黑边 | `src/core/wallpaper_manager.py:64-69` 仅调 SPI，从不写 `WallpaperStyle` 注册表 | 所有用户；宽高比不匹配时观感差 | P1 | M | 下批做（2 批） |
| **G11** | **无配置导入/导出、无"恢复默认"入口** | `src/core/settings.py:232` `reset_to_defaults()` 存在但**从未接 UI**（grep 仅此一处定义）；无 export/import | 换机/备份/重置需求的用户 | P1 | M | 下批做（2 批） |
| **G15** | **面向用户的文档缺失**：`docs/` 全是开发向（architecture/api-design/dev-standards/implementation-plan/review），无使用说明/FAQ/故障排查 | `ls docs/`；README 仅列开发向子文档 | 新用户无法自助 | P1 | M | 下批做（2 批） |
| **G12** | **打包为 UPX+onefile**：启动慢、杀软高频误报、SmartScreen 报"未知发布者" | `wallpace.spec:29` `upx=True`；`build.bat:39-44` `--onefile`；`dist/wallpace.exe` 47.9MB；无签名 | 新用户"不敢装" | P1 | M/L | 下批做（需拍板，见 §5） |

### P2 — 延后 / 按需

| ID | 缺口 | 证据 | 结论 |
|---|---|---|---|
| **G16** | 无自动更新机制（`update check` 全无） | 全仓无版本检查 | 延后：个人工具用户量小，ROI 低 |
| **G17** | 无安装包（Inno/NSIS/MSI）、无开始菜单/桌面快捷方式 | `build.bat` 只产裸 exe | 延后：与 G12 合并考虑 |
| **G18** | 无 i18n（全硬编码中文） | 无 `tr()`/`QTranslator`（grep 无命中） | 延后/不做：目标用户中文 |
| **G19** | 无暗色主题 | `src/app/theme.py:45,54` 仅 `light_qss`，`window.py:183` 只用亮色 | 延后 |
| **G20** | 无全局快捷键（换一张/暂停） | 无 `QShortcut`/`QKeySequence`（grep 无命中） | 延后：托盘已够用 |
| **G21** | 大图库（10 万张）性能未实测：签名每次全量递归 scandir | `src/core/image_library.py:138-188` 每次扫描都全量遍历；内存全量 list | 延后：先实测再优化 |
| **G22** | 缩略图缓存上限固定 128MB 不可配 | `src/app/image_loader.py:123,196` | 不做：默认值合理 |
| **G24** | 未显式设置 High-DPI 策略 | 无 `HighDpi` 相关（grep 无命中） | 延后：Qt6 默认 per-monitor，实测为准 |
| **G25** | dev 态自启写入错误命令（`python.exe --hidden` 缺脚本路径） | `src/core/autostart_registry.py:20-24,77-84` | 不做：仅影响开发机，打包态正常 |

---

## 3. 已核实：**不是缺口**（避免过度设计，先写明）

| 项 | 说明 |
|---|---|
| 首次运行空态引导 | **已有**：`src/app/window.py:340-357` 显示"📂 还没有添加图片文件夹 / 点击左侧设置→添加文件夹"，`_apply_scan_results`（`window.py:951-955`）自动切换显隐。**无需再加向导弹窗** |
| 文件夹变更监听 | **已实现**（README `L90` 标"📋 待开发"是错的）：`src/core/directory_watcher.py` 全套 + 接线于 `window.py:124-130` |
| 配置原子写 | **已有**：`src/core/settings.py:144-174` 临时文件+fsync+os.replace |
| 配置字段校验/修复 | **已有**：`settings.py:243-311` validate + `_merge_with_defaults` |
| 扫描异步化 | **已有**：`ScanController` + QThreadPool，不阻塞主线程（`window.py:118-122`） |
| 缩略图懒加载 | **已有**：`gallery_page.py:46,275-299` 分批 50 张；`GalleryStrip` 同理 |

---

## 4. 路线图（每批可独立交付一个可感知改进）

| 批次 | 范围 | 交付的"可感知改进" |
|---|---|---|
| **批次 1（P0，本批）** | G01 单实例 · G03 首启不自启（默认改关或首启询问）· G04 配置损坏备份 + 友好提示 · G05 补 LICENSE/THIRD_PARTY_NOTICES + README 更正 · G02 启动时校验自启项有效性 + 卸载清理入口/文档 | **"装得上、用得放心、卸得干净"** |
| **批次 2（P1 一批）** | G06 日志轮转 · G07 诊断/打开日志入口 · G13 版本单一来源 · G14 README 修正 · G10 per_monitor_map 修复 · G23 图库空态 | **"出问题知道去哪看，配置/文档可信"** |
| **批次 3（P1 二批）** | G08 切换失败兜底重试 + 状态提示 · G09 壁纸填充方式 · G11 配置导入导出/恢复默认 · G15 用户文档/FAQ | **"更好用、可控、可迁移"** |
| **批次 4（发布工程，需拍板）** | G12 去 UPX / onedir 或安装包 / 代码签名 · G17 开始菜单快捷方式 | **"双击不报警、装得下"** |
| **延后池** | G16 · G18 · G19 · G20 · G21 · G22 · G24 · G25 | 按用户反馈再启 |

---

## 5. 待用户拍板的问题（不替他决定）

| # | 问题 | 影响哪些缺口 |
|---|---|---|
| Q1 | **分发范围**：仅自己用，还是要公开分发（GitHub Release/朋友）？ | 决定 G05/G12/G16/G17 的紧迫度 |
| Q2 | 是否接受 Windows SmartScreen "未知发布者"提示？是否愿意为**代码签名**付费？ | G12 |
| Q3 | 是否需要**自动更新**（个人工具通常不需要）？ | G16 |
| Q4 | 开机自启默认值：**改为默认关闭**，还是默认开但**首启弹窗询问**？ | G03 |
| Q5 | 配置/日志目录是否迁到 Windows 惯例的 `%APPDATA%\Wallpace`（当前 `~/.wallpace` 点号目录）？ | G04/G07 |
| Q6 | 是否需要多语言 / 暗色主题？ | G18/G19 |
| Q7 | 是否要做**安装包**（Inno Setup），还是只发绿色版？ | G12/G17 |

---

## 6. 附：README 与代码不一致清单（已逐条核实）

| README 行 | README 写的 | 实际代码 | 修正建议 |
|---|---|---|---|
| L11 | 目录树 `.wallpace.json # User configuration` | `~/.wallpace/config.json`（`settings.py:39-40`） | 改为 `%USERPROFILE%\.wallpace\config.json` |
| L49 | `settings.py ← 配置管理 (.wallpace.json)` | 同上 | 改路径 |
| L65 | `.wallpace.json ← 用户配置文件` | 同上 | 改路径 |
| L77 | 功能1 `配置图片库文件夹路径 (settings.json)` | 文件名应为 config.json | 改文件名 |
| L90 | 功能14 `文件夹变更监听 (watchdog) 📋 待开发` | **已实现**（`directory_watcher.py` + `window.py:124-130`） | 改 ✅ 完成 |
| L127 | "程序会在 **CWD** 下生成 `.wallpace.json`" | 用户目录 `~/.wallpace/config.json` | 改路径 + 说明迁移逻辑 |
| L151 | 配置格式 `JSON (.wallpace.json)` | config.json | 改文件名 |
| L153 | 许可 `LGPL-2.1 (PySide6) + MIT` | PySide6 实为 **LGPLv3**；且无 LICENSE 文件 | 更正版本号 + 补文件 |
| L36-68 | 目录树缺 `scan_controller.py`/`settings_controller.py`/`gallery_strip.py`/`pages/*`/`image_loader.py`/`icon.py`/`directory_watcher.py`/`desktop_wallpaper_com.py` | 实际 8+ 个新文件 | 补全目录树 |

> 注：`README.md:64` "logs/ 写入 ~/.wallpace/logs/" 与 `main.py:18` **一致**，无需改。

---

## 7. 明确"不做"清单

| 不做项 | 理由 |
|---|---|
| 内置示例图库 / 自带壁纸 | 增加体积 + 版权风险，价值低 |
| 首次运行向导弹窗 | 已有空态引导（`window.py:340-357`），弹窗属过度设计 |
| 云同步 / 账号体系 | 与"本地个人小工具"定位不符 |
| 插件系统 / 脚本化 | 典型过度设计 |
| 桌面壁纸过渡动画 | Windows SPI 无法实现真正的桌面过渡，收益低、风险高 |
| 自动更新 | 见 G16，个人工具用户量小，ROI 低 |
| i18n 国际化 | 目标用户为中文，延后 |
| 换框架 / 大规模架构重构 | 现有架构（MVC + controller 拆分）够用，ROI 低 |
