# Changelog

本文件记录 Wallpace 的显著变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- G09 壁纸填充方式设置：与 Windows「个性化 → 背景」同款 5 选项（填满/完整显示/拉伸/居中/平铺），设置页即选即生效（写 `WallpaperStyle` 注册表 + SPI 广播），启动时按已保存配置同步一次；非 Windows 平台安全降级。
- G11 配置备份与迁移：设置页新增「导出配置 / 导入配置 / 恢复默认配置」三个入口。导出为 `{version, config, image_library}` JSON；导入同步配置与图库状态（目录/收藏/跳过）并刷新监听；恢复默认带二次确认，只重置配置值、不动磁盘上的图片文件。
- G08 切换失败兜底：随机选到已删除/损坏图片时不再静默失败——自动换下一张候选（最多 3 张互不相同）；候选全部失败才通过托盘气泡提示用户，覆盖手动/每日/间隔三种触发点。
- G07 诊断入口：设置页「打开日志目录（诊断）」按钮 + 托盘菜单项；`src/core/diagnostics.py` 提供 `LOGS_DIR` 单一真源，写日志与打开日志同一路径。
- G02 卸载清理：设置页「卸载清理」按钮（二次确认 → 移除开机自启项 + 删除 `~/.wallpace`）；启动时自愈清理指向已删 exe 的坏自启项。
- G06 日志轮转：`wallpace.log` 改 `RotatingFileHandler`（1MB × 3 备份封顶）。
- G23 图库空态：图库页无图片时按当前筛选显示引导文案（全部→提示添加目录 / 收藏→提示 ☆ / 已跳过→提示恢复）。
- G04 配置损坏时先备份 `config.json.corrupt-*` 再重置，并经 `show_platform_warning` 弹给用户（不再静默丢数据）。
- G03 开机自启默认关闭（含 main.py 与设置页复选框兜底值同源修复）。
- G05 补 MIT `LICENSE` 文件；README 更正 PySide6 为 LGPLv3。

### 文档

- G15 新增面向普通用户的文档：`docs/user-guide.md`（安装/首用/切换方式/填充方式/多屏/备份迁移/自启/卸载全流程）+ `docs/troubleshooting.md`（FAQ、如何看日志、「切换失败」气泡解释、配置损坏找回）；README 目录树与「使用说明」段均加链接。
- G14 README 全面对齐代码：目录树补全、配置路径更正为 `~/.wallpace/config.json`、功能表扩至 20 项。
- G13 版本号单一真源已于 0.2.0 完成，状态同步。
- `docs/product-maturity-gap.md` 各缺口状态随实现逐条更新。

## [0.2.0] - 2026-10-03

### 修复

- 4 项 P0 缺陷：设置项静默失效、配置导致启动崩溃、图库假死、配置误入库。
- 缩略图条切换当前项后不回写内部状态，懒加载新批次按陈旧路径判定高亮，导致同屏出现两个高亮项。
- 配置损坏时先备份原文件再重置并显式提示用户（原实现静默回落默认值，用户无感知）。
- 开机自启默认关闭，不再在首次启动时静默写入注册表；配置缺键时的兜底默认值同步修正。
- 固定 `--basetemp` 导致并发 pytest 会话互相删除临时目录，产生大量 fixture setup 错误。

### 新增

- 文件夹变更监听（watchdog 递归监听 + 防抖触发重扫）。
- 多显示器逐屏壁纸（ctypes 调用 `IDesktopWallpaper`），环境不支持时优雅降级为统一壁纸。
- 启动时恢复已保存的逐屏壁纸分配，并自动剔除已拔掉的显示器与已删除的图片。
- 暂停态倒计时文案与「立即刷新」。
- 单实例保护（`QLockFile` + `QLocalServer`）：重复启动改为唤醒已有实例后静默退出。
- 缩略图内存 LRU 缓存（命中跳过 `QThreadPool`，双上限淘汰）。
- 开发依赖组：`pip install -e ".[dev]"`（pytest / ruff / mypy）。

### 变更

- 打包配方入库至 `packaging/wallpace.spec`——此前被 `.gitignore` 的 `*.spec` 吞掉，clone 后拿不到打包配方。
- 版本号改为单一真源 `src/__init__.py::__version__`，UI 显示与打包元数据均从它派生（此前 3 处硬编码）。
- 补充 MIT `LICENSE` 与本变更日志。

### 内部

- `window.py` 职责拆分：抽出 `ScanController`、`GalleryStrip`、`SettingsController`，`MainWindow` 保留同名 facade，对外调用方式不变。
- 调度器壁纸设置改走可注入 applier，消除逐屏模式下的双写。
- 20 余项优化：配置防抖落盘、BMP/GIF 支持、扫描优化、增量扫描、调度器加固、平台能力降级提示等。
- 测试基建：basetemp 每次运行唯一化，从根上消除并发冲突。

## [0.1.0] - 2026-08-13

### 新增

- 多文件夹图片库扫描与缩略图展示。
- 跳过当前图片、收藏 / 取消收藏。
- 三种切换模式：每日随机（daily）、固定间隔（interval）、手动（manual）。
- 关闭窗口最小化到系统托盘；托盘菜单可换一张 / 暂停继续 / 打开窗口 / 退出。
- 开机自启注册表（HKCU Run）开关。
- PyInstaller `--onefile` 打包脚本 `build.bat`。
- 配置迁移到 `%APPDATA%/.wallpace/config.json`，收藏持久化。

[未发布]: https://keepachangelog.com/zh-CN/1.1.0/
[0.2.0]: https://keepachangelog.com/zh-CN/1.1.0/
[0.1.0]: https://keepachangelog.com/zh-CN/1.1.0/
