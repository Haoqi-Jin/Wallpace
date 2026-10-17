# Wallpace
## Project Structure
```
Wallpace/
├── docs/           # API design, architecture docs
├── src/            # Source code
│   ├── app/        # UI components (window, tray, widgets)
│   ├── core/       # Business logic (scheduler, library, settings, wallpaper)
│   └── utils/      # Utility modules
├── tests/          # Unit tests
├── .wallpace.json  # User configuration
├── build.bat       # Windows build script
├── pyproject.toml  # Python project config
└── README.md       # This document
``` — 桌面壁纸自动切换器

## 项目概览

一个运行在 Windows 上的壁纸自动切换桌面小工具。用户配置图片库文件夹路径后，程序自动扫描并每天随机选一张设为系统壁纸。支持手动切换、跳过不喜欢、自定义间隔等特性。

**技术栈：** Python 3.13 + PySide6 (LGPL)
**目标平台：** Windows 10/11

---

## 目录结构

```
wallpace/
├── README.md                    ← 本文件（项目总纲）
├── docs/                        ← 项目开发文档集
│   ├── architecture.md          ← 架构设计文档
│   ├── api-design.md            ← API 设计文档
│   ├── dev-standards.md         ← 开发执行标准
│   └── implementation-plan.md   ← 实施计划
├── src/                         ← 源代码
│   ├── main.py                  ← 入口点
│   ├── __init__.py              ← 包元数据 (__version__)
│   ├── app/                     ← 应用模块 (GUI)
│   │   ├── __init__.py
│   │   ├── window.py            ← 主窗口
│   │   ├── sidebar.py           ← 窄图标侧边栏导航
│   │   ├── tray.py              ← 系统托盘
│   │   ├── theme.py             ← 粉蓝渐变 QSS 主题引擎
│   │   └── widgets/
│   │       ├── __init__.py
│   │       └── preview_card.py  ← 大图预览卡片组件
│   ├── core/                    ← 核心逻辑模块
│   │   ├── settings.py          ← 配置管理 (.wallpace.json)
│   │   ├── image_library.py     ← 图片库管理
│   │   ├── scheduler.py         ← 定时调度器
│   │   ├── wallpaper_manager.py ← 壁纸设置 (Windows SPI API)
│   │   └── autostart_registry.py← 开机自启 (HKCU 注册表)
│   └── utils/                   ← 工具模块 (预留)
│       └── __init__.py
├── tests/                       ← pytest 单元测试
│   ├── conftest.py              ← 共享 fixtures
│   ├── test_settings.py
│   ├── test_image_library.py
│   ├── test_scheduler.py
│   ├── test_wallpaper_manager.py
│   └── test_autostart_registry.py
├── dev-log/                     ← 每日开发记录
├── logs/                        ← 运行时日志 (写入 ~/.wallpace/logs/)
├── .wallpace.json               ← 用户配置文件 (可选, .gitignore)
├── requirements.txt             ← Python 依赖
├── pyproject.toml               ← 项目元数据 (setuptools, entry point)
└── build.bat                    ← 打包脚本 (PyInstaller)
```

---

## 核心功能

| # | 功能 | 状态 |
|---|------|------|
| 1 | 配置图片库文件夹路径 (`settings.json`) | ✅ 完成 |
| 2 | 自动扫描 JPG/PNG/WebP/BMP/GIF 图片 | ✅ 完成 |
| 3 | 每日随机切换壁纸 | ✅ 完成 |
| 4 | 手动触发"换一张" (侧边栏 / 按钮) | ✅ 完成 |
| 5 | "不喜欢"跳过机制 (不重复) | ✅ 完成 |
| 6 | 自定义时间间隔切换 | ✅ 完成 |
| 7 | 收藏到精选列表 | ✅ 完成 |
| 8 | 多显示器统一设置 (Windows API) | ✅ 完成 |
| 9 | 图标侧边栏导航 + 粉蓝渐变 UI | ✅ 完成 |
| 10 | 底部自定义栏（目录/日期/数量） | ✅ 完成 |
| 11 | 系统托盘后台运行 + 右键菜单 | ✅ 完成 |
| 12 | 开机自启动 (HKCU 注册表) | ✅ 完成 |
| 13 | 图片库缩略图预览 + 点击设壁纸 | ✅ 完成 |
| 14 | 文件夹变更监听 (watchdog) | ✅ 完成 |

---

## 快速开始

### 环境准备

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### 运行开发版

```bash
python src/main.py
```

### 运行测试

```bash
pytest tests/
python src/main.py --test  # 使用 unittest 也可
```

### 打包发布

```bash
build.bat
```

---

## 使用说明

首次运行时，程序会在 CWD 下生成 `.wallpace.json` 配置文件（不存在则回退默认值）：

```json
{
  "image_directories": ["D:\\my_pictures\\wallpaper"],
  "switch_mode": "daily_random",
  "daily_time": "08:00"
}
```

常用命令/快捷操作：

- **托盘左键** — 手动切换一张
- **托盘右键 → 暂停切换** — 停止自动调度
- **侧边栏底部** — 切换 / 跳过 / 收藏 三个圆形按钮
- **设置页面** — 查看当前配置的图片和切换模式

---

## 技术规范

- **Python 版本：** 3.10+ (本项目使用 3.13)
- **UI 框架：** PySide6 >= 6.6
- **样式语言：** QSS（Qt Style Sheets），粉蓝渐变主题
- **配置格式：** JSON (.wallpace.json)
- **构建工具：** PyInstaller
- **许可协议：** LGPLv3 (PySide6) + MIT (本项目代码，见仓库根 LICENSE)
