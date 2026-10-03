# Changelog

本文件记录 Wallpace 的显著变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

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
