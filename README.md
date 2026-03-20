# Video Frame Analyzer — 视频帧级分析工具

专业级视频帧分析桌面应用，基于 ffprobe 对视频进行逐帧分析，生成交互式 HTML 报告。

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-green)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

## 功能特性

### 🎬 分析维度
| 模块 | 内容 |
|------|------|
| 📦 容器信息 | 格式、时长、码率、元数据标签 |
| 🔧 流信息 | 编码器、分辨率、Profile、像素格式、色彩空间 |
| ⏱ PTS/DTS 时序 | 每帧时间戳、间隔统计、DTS-PTS偏移分析 |
| 🎞 帧类型 | I/P/B帧分布、占比、时间线 |
| 📐 GOP 结构 | 关键帧间隔、GOP大小分布 |
| 📈 码率分析 | 滑动窗口瞬时码率曲线、帧大小分布 |
| 📋 帧数据表 | 每帧详细数据，支持搜索/筛选/排序/无限滚动 |
| 📑 章节 | 视频章节信息 |

### 🖥 GUI 特性
- 深色专业主题
- 文件拖放 / 浏览器选择
- 可勾选分析模块（全选/全不选/预设方案）
- ffprobe 路径可配置（支持同目录内置）
- 实时进度条
- 分析完成后自动打开 HTML 报告

## 安装使用

### 方式一：直接运行 Python 脚本

```bash
# 安装依赖（无额外依赖，仅需 Python 3.8+ 和 ffmpeg）
python gui_analyzer.py
```

### 方式二：打包为 EXE

```cmd
build.bat
```

打包输出在 `dist\` 目录下。

### 前置条件

- **Python** 3.8+ — [下载](https://www.python.org/downloads/)（安装时勾选 Add to PATH）
- **ffmpeg** — 包含 ffprobe

```cmd
# Windows 安装 ffmpeg
winget install ffmpeg

# 或手动下载: https://ffmpeg.org/download.html
```

## 项目结构

```
video-frame-analyzer/
├── gui_analyzer.py      ← GUI 主程序
├── video_analyzer.py    ← 命令行版（无GUI）
├── build.bat            ← Windows 打包
└── README.md
```

## 界面预览

```
┌─────────────────────────────────────────────────┐
│  🎬 Video Frame Analyzer                        │
│  视频帧级分析工具                                 │
├─────────────────────────────────────────────────┤
│  📹 视频文件                                     │
│  [C:\Videos\sample.mp4          ] [选择文件]    │
├─────────────────────────────────────────────────┤
│  ⚙️ 分析选项                                     │
│  ☑ 容器/格式    ☑ 流编码信息                     │
│  ☑ PTS/DTS时序  ☑ 帧类型分布                     │
│  ☑ GOP结构      ☑ 码率曲线                       │
│  ☑ 帧数据表     ☑ 章节信息                       │
│  [全选] [全不选] [仅基本信息] [全部详细]         │
├─────────────────────────────────────────────────┤
│  💾 输出报告                                     │
│  [C:\Videos\sample_report.html    ] [选择路径]  │
├─────────────────────────────────────────────────┤
│  [🚀 开始分析]  [📂 打开报告]                    │
│  ████████████████████░░░░░  78%  ⏳ 分析帧数据... │
└─────────────────────────────────────────────────┘
```

分析完成后自动弹出交互式 HTML 报告（6个标签页，10+图表）。

## 支持格式

MP4 / MKV / AVI / MOV / FLV / WebM / TS / M4V / WMV / MPG / MPEG / 3GP / RMVB — 所有 ffmpeg 支持的视频格式。
