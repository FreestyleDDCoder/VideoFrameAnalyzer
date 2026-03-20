#!/usr/bin/env python3
"""
Video Frame Analyzer — 视频帧级分析工具
分析视频的每一帧 PTS/DTS 时间间隔、帧类型、GOP结构等，生成交互式HTML报告。
支持 MP4 / MKV / AVI / MOV / FLV / WebM 等格式。
"""

import subprocess
import json
import sys
import os
import math
import time
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

# ─────────────────────────────────────────────────────────
# ffprobe 封装
# ─────────────────────────────────────────────────────────

def run_ffprobe(filepath, args):
    """运行 ffprobe 并返回 JSON 输出"""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        *args,
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[ffprobe error] {result.stderr}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("[错误] 未找到 ffprobe，请确保已安装 ffmpeg 并添加到 PATH。", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("[错误] ffprobe 执行超时（5分钟）", file=sys.stderr)
        return None


def get_container_info(filepath):
    """获取容器/格式信息"""
    data = run_ffprobe(filepath, ["-show_format"])
    return data.get("format", {}) if data else {}


def get_stream_info(filepath):
    """获取所有流信息"""
    data = run_ffprobe(filepath, ["-show_streams"])
    return data.get("streams", []) if data else []


def get_all_frames(filepath):
    """获取所有帧信息（最耗时）"""
    data = run_ffprobe(filepath, [
        "-show_frames",
        "-show_entries",
        "frame=media_type,stream_index,pts,pts_time,dts,dts_time,"
        "duration,duration_time,pict_type,interlaced_frame,"
        "top_field_first,key_frame,pkt_size,pkt_pos,width,height,"
        "coded_picture_number,display_picture_number,"
        "best_effort_timestamp,best_effort_timestamp_time,"
        "pkt_dts,pkt_dts_time,repeat_pict,quality,"
        "crop_top,crop_bottom,crop_left,crop_right,"
        "color_range,color_space,color_primaries,color_transfer,chroma_location",
        "-side_data"
    ])
    return data.get("frames", []) if data else []


def get_packets(filepath):
    """获取所有包信息"""
    data = run_ffprobe(filepath, [
        "-show_packets",
        "-show_entries",
        "packet=pts,pts_time,dts,dts_time,duration,duration_time,"
        "size,stream_index,flags,pos"
    ])
    return data.get("packets", []) if data else []


def get_chapters(filepath):
    """获取章节信息"""
    data = run_ffprobe(filepath, ["-show_chapters"])
    return data.get("chapters", []) if data else []


def get_programs(filepath):
    """获取节目信息"""
    data = run_ffprobe(filepath, ["-show_programs"])
    return data.get("programs", []) if data else []


# ─────────────────────────────────────────────────────────
# 数据处理
# ─────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    """安全转换浮点数"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0):
    """安全转换整数"""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def analyze_frames(frames):
    """分析帧数据，计算统计信息"""
    video_frames = []
    audio_frames = []

    last_pts_time = {}  # per stream_index: last pts_time for interval calc

    for f in frames:
        media_type = f.get("media_type", "")
        stream_idx = safe_int(f.get("stream_index"))
        pts_time = safe_float(f.get("pts_time"))

        # 计算与前一帧的时间间隔（ms）
        prev = last_pts_time.get(stream_idx)
        if prev is not None:
            interval_ms = round((pts_time - prev) * 1000, 3)
        else:
            interval_ms = None
        last_pts_time[stream_idx] = pts_time

        frame_data = {
            "pts": safe_int(f.get("pts")),
            "pts_time": pts_time,
            "dts": safe_int(f.get("dts")),
            "dts_time": safe_float(f.get("dts_time")),
            "duration": safe_float(f.get("duration")),
            "duration_time": safe_float(f.get("duration_time")),
            "pict_type": f.get("pict_type", ""),
            "key_frame": safe_int(f.get("key_frame")),
            "pkt_size": safe_int(f.get("pkt_size")),
            "pkt_pos": safe_int(f.get("pkt_pos")),
            "interlaced": safe_int(f.get("interlaced_frame")),
            "top_field_first": safe_int(f.get("top_field_first")),
            "stream_index": stream_idx,
            "interval_ms": interval_ms,
            # 新增字段
            "coded_picture_number": safe_int(f.get("coded_picture_number")),
            "display_picture_number": safe_int(f.get("display_picture_number")),
            "best_effort_timestamp": safe_int(f.get("best_effort_timestamp")),
            "best_effort_timestamp_time": safe_float(f.get("best_effort_timestamp_time")),
            "pkt_dts": safe_int(f.get("pkt_dts")),
            "pkt_dts_time": safe_float(f.get("pkt_dts_time")),
            "repeat_pict": safe_int(f.get("repeat_pict")),
            "quality": f.get("quality"),
            "crop_top": safe_int(f.get("crop_top")),
            "crop_bottom": safe_int(f.get("crop_bottom")),
            "crop_left": safe_int(f.get("crop_left")),
            "crop_right": safe_int(f.get("crop_right")),
            "color_range": f.get("color_range", ""),
            "color_space": f.get("color_space", ""),
            "color_primaries": f.get("color_primaries", ""),
            "color_transfer": f.get("color_transfer", ""),
            "chroma_location": f.get("chroma_location", ""),
            "side_data": f.get("side_data_list", []),
        }

        if media_type == "video":
            video_frames.append(frame_data)
        elif media_type == "audio":
            audio_frames.append(frame_data)

    # ── 视频帧统计 ──
    video_stats = {}
    if video_frames:
        pts_times = [f["pts_time"] for f in video_frames]
        dts_times = [f["dts_time"] for f in video_frames if f["dts_time"] > 0 or f["dts"] >= 0]
        durations = [f["duration_time"] for f in video_frames if f["duration_time"] > 0]
        sizes = [f["pkt_size"] for f in video_frames]
        pict_types = Counter(f["pict_type"] for f in video_frames)
        key_frames = sum(1 for f in video_frames if f["key_frame"])

        # PTS 间隔
        pts_intervals = []
        for i in range(1, len(pts_times)):
            pts_intervals.append(pts_times[i] - pts_times[i - 1])

        # DTS 间隔
        dts_intervals = []
        for i in range(1, len(dts_times)):
            dts_intervals.append(dts_times[i] - dts_times[i - 1])

        # DTS vs PTS 偏移 (decode delay)
        dts_pts_offset = []
        for f in video_frames:
            offset = f["pts_time"] - f["dts_time"]
            if abs(offset) < 100:  # 过滤异常值
                dts_pts_offset.append(offset)

        # GOP 分析
        gops = []
        current_gop = 0
        gop_sizes = []
        for f in video_frames:
            if f["key_frame"] and current_gop > 0:
                gop_sizes.append(current_gop)
                current_gop = 0
            current_gop += 1
        if current_gop > 0:
            gop_sizes.append(current_gop)

        # 码率滑动窗口（每秒）
        bitrate_windows = []
        if len(video_frames) > 1:
            window_start = 0
            window_bytes = 0
            for f in video_frames:
                if f["pts_time"] - pts_times[window_start] >= 1.0:
                    duration = f["pts_time"] - pts_times[window_start]
                    if duration > 0:
                        bitrate_windows.append({
                            "time": (pts_times[window_start] + f["pts_time"]) / 2,
                            "kbps": window_bytes * 8 / duration / 1000
                        })
                    window_start = video_frames.index(f)
                    window_bytes = 0
                window_bytes += f["pkt_size"]

        # PTS reorder 分析（显示 DTS 和 PTS 的顺序差异）
        pts_order_mismatch = 0
        for i, f in enumerate(video_frames):
            if f["dts"] >= 0 and f["pts"] >= 0:
                if f["pts"] < f["dts"]:
                    pts_order_mismatch += 1

        # 计算帧间隔分布
        interval_hist = Counter()
        for iv in pts_intervals:
            bucket = round(iv * 1000)  # 转毫秒
            interval_hist[bucket] += 1

        # 帧间间隔异常检测（>33ms 视为异常）
        frame_intervals = [f["interval_ms"] for f in video_frames if f["interval_ms"] is not None]
        anomaly_intervals = [iv for iv in frame_intervals if iv > 33]
        interval_anomaly_ratio = len(anomaly_intervals) / len(frame_intervals) if frame_intervals else 0

        # coded_picture_number 序列分析（检测丢帧/重复帧）
        coded_nums = [f["coded_picture_number"] for f in video_frames if f["coded_picture_number"] > 0]
        coded_gaps = []
        coded_dupes = 0
        if coded_nums:
            for i in range(1, len(coded_nums)):
                diff = coded_nums[i] - coded_nums[i - 1]
                if diff > 1:
                    coded_gaps.append({"frame": i, "gap": diff})
                elif diff == 0:
                    coded_dupes += 1

        # display_picture_number 序列分析
        display_nums = [f["display_picture_number"] for f in video_frames if f["display_picture_number"] > 0]
        display_gaps = []
        display_dupes = 0
        if display_nums:
            for i in range(1, len(display_nums)):
                diff = display_nums[i] - display_nums[i - 1]
                if diff > 1:
                    display_gaps.append({"frame": i, "gap": diff})
                elif diff == 0:
                    display_dupes += 1

        # repeat_pict 分析（3:2 pulldown / telecine 检测）
        repeat_picts = [f["repeat_pict"] for f in video_frames]
        repeat_counts = Counter(repeat_picts)

        # 色彩空间信息（取第一帧的值）
        first_color = {}
        for f in video_frames:
            if f["color_space"]:
                first_color = {
                    "color_range": f["color_range"],
                    "color_space": f["color_space"],
                    "color_primaries": f["color_primaries"],
                    "color_transfer": f["color_transfer"],
                    "chroma_location": f["chroma_location"],
                }
                break

        # 裁剪信息
        crops = set()
        for f in video_frames:
            crop = (f["crop_top"], f["crop_bottom"], f["crop_left"], f["crop_right"])
            if any(c > 0 for c in crop):
                crops.add(crop)
        crop_info = list(crops) if crops else []

        # HDR side data 检测
        hdr_info = []
        for f in video_frames:
            for sd in f.get("side_data", []):
                sd_type = sd.get("side_data_type", "")
                if "HDR" in sd_type or "Mastering" in sd_type or "Content" in sd_type:
                    hdr_info.append({
                        "type": sd_type,
                        "red_x": sd.get("red_x"), "red_y": sd.get("red_y"),
                        "green_x": sd.get("green_x"), "green_y": sd.get("green_y"),
                        "blue_x": sd.get("blue_x"), "blue_y": sd.get("blue_y"),
                        "white_point_x": sd.get("white_point_x"), "white_point_y": sd.get("white_point_y"),
                        "min_luminance": sd.get("min_luminance"), "max_luminance": sd.get("max_luminance"),
                        "max_content": sd.get("max_content"), "max_average": sd.get("max_average"),
                    })
            if hdr_info:
                break  # 取第一个有 HDR 信息的帧即可

        # best_effort_timestamp vs pts 偏移分析
        bet_offsets = []
        for f in video_frames:
            if f["best_effort_timestamp_time"] > 0 and f["pts_time"] > 0:
                bet_offsets.append(f["best_effort_timestamp_time"] - f["pts_time"])

        video_stats = {
            "total_frames": len(video_frames),
            "pts_range": (min(pts_times), max(pts_times)) if pts_times else (0, 0),
            "dts_range": (min(dts_times), max(dts_times)) if dts_times else (0, 0),
            "duration_sec": max(pts_times) - min(pts_times) if len(pts_times) > 1 else 0,
            "avg_pts_interval": sum(pts_intervals) / len(pts_intervals) if pts_intervals else 0,
            "min_pts_interval": min(pts_intervals) if pts_intervals else 0,
            "max_pts_interval": max(pts_intervals) if pts_intervals else 0,
            "std_pts_interval": math.sqrt(sum((x - sum(pts_intervals)/len(pts_intervals))**2 for x in pts_intervals) / len(pts_intervals)) if len(pts_intervals) > 1 else 0,
            "avg_dts_interval": sum(dts_intervals) / len(dts_intervals) if dts_intervals else 0,
            "min_dts_interval": min(dts_intervals) if dts_intervals else 0,
            "max_dts_interval": max(dts_intervals) if dts_intervals else 0,
            "avg_duration": sum(durations) / len(durations) if durations else 0,
            "pict_types": dict(pict_types),
            "key_frames": key_frames,
            "total_size_bytes": sum(sizes),
            "avg_frame_size": sum(sizes) / len(sizes) if sizes else 0,
            "avg_dts_pts_offset": sum(dts_pts_offset) / len(dts_pts_offset) if dts_pts_offset else 0,
            "max_dts_pts_offset": max(dts_pts_offset) if dts_pts_offset else 0,
            "min_dts_pts_offset": min(dts_pts_offset) if dts_pts_offset else 0,
            "gop_sizes": gop_sizes,
            "avg_gop_size": sum(gop_sizes) / len(gop_sizes) if gop_sizes else 0,
            "min_gop_size": min(gop_sizes) if gop_sizes else 0,
            "max_gop_size": max(gop_sizes) if gop_sizes else 0,
            "bitrate_windows": bitrate_windows,
            "pts_order_mismatch": pts_order_mismatch,
            "interval_hist": dict(sorted(interval_hist.items())),
            "frame_intervals": frame_intervals,
            "anomaly_intervals": anomaly_intervals,
            "anomaly_interval_count": len(anomaly_intervals),
            "interval_anomaly_ratio": interval_anomaly_ratio,
            "interval_histogram": dict(sorted(interval_hist.items())),
            "is_interlaced": any(f["interlaced"] for f in video_frames),
            # 新增统计
            "coded_gaps": coded_gaps,
            "coded_dupes": coded_dupes,
            "coded_num_range": (min(coded_nums), max(coded_nums)) if coded_nums else (0, 0),
            "display_gaps": display_gaps,
            "display_dupes": display_dupes,
            "repeat_pict_counts": dict(repeat_counts),
            "color_info": first_color,
            "crop_info": crop_info,
            "hdr_info": hdr_info,
            "bet_offset_avg": sum(bet_offsets) / len(bet_offsets) if bet_offsets else 0,
            "bet_offset_max": max(bet_offsets) if bet_offsets else 0,
            "bet_offset_min": min(bet_offsets) if bet_offsets else 0,
        }

    # ── 音频帧统计 ──
    audio_stats = {}
    if audio_frames:
        a_pts = [f["pts_time"] for f in audio_frames]
        a_intervals = [a_pts[i] - a_pts[i-1] for i in range(1, len(a_pts))]
        a_sizes = [f["pkt_size"] for f in audio_frames]

        audio_stats = {
            "total_frames": len(audio_frames),
            "duration_sec": max(a_pts) - min(a_pts) if len(a_pts) > 1 else 0,
            "avg_pts_interval": sum(a_intervals) / len(a_intervals) if a_intervals else 0,
            "total_size_bytes": sum(a_sizes),
        }

    return video_frames, audio_frames, video_stats, audio_stats


# ─────────────────────────────────────────────────────────
# HTML 生成
# ─────────────────────────────────────────────────────────

def generate_html(filepath, container_info, stream_info, chapters,
                  video_frames, audio_frames, video_stats, audio_stats):
    """生成完整的 HTML 分析报告"""

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    filesize_mb = filesize / (1024 * 1024)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 提取视频流信息 ──
    video_stream = None
    audio_streams = []
    subtitle_streams = []
    for s in stream_info:
        if s.get("codec_type") == "video" and not video_stream:
            video_stream = s
        elif s.get("codec_type") == "audio":
            audio_streams.append(s)
        elif s.get("codec_type") == "subtitle":
            subtitle_streams.append(s)

    # ── 构建帧 JSON（限制大小，最多 50000 帧）──
    MAX_FRAMES_JSON = 50000
    if len(video_frames) > MAX_FRAMES_JSON:
        step = len(video_frames) // MAX_FRAMES_JSON
        sampled_video = video_frames[::step]
    else:
        sampled_video = video_frames

    video_json = json.dumps([{
        "i": i,
        "pts_t": round(f["pts_time"], 6),
        "dts_t": round(f["dts_time"], 6),
        "dur": round(f["duration_time"], 6),
        "type": f["pict_type"],
        "kf": f["key_frame"],
        "size": f["pkt_size"],
        "dts_pts": round(f["pts_time"] - f["dts_time"], 6),
        "interval": f["interval_ms"],
        "coded_num": f["coded_picture_number"],
        "display_num": f["display_picture_number"],
        "bet_t": round(f["best_effort_timestamp_time"], 6),
        "pkt_dts_t": round(f["pkt_dts_time"], 6),
        "repeat": f["repeat_pict"],
        "interlaced": f["interlaced"],
        "top_first": f["top_field_first"],
        "crop": [f["crop_top"], f["crop_bottom"], f["crop_left"], f["crop_right"]],
    } for i, f in enumerate(sampled_video)], separators=(',', ':'))

    # ── 异常帧数据（间隔 > 33ms）──
    anomaly_intervals = video_stats.get("anomaly_intervals", [])
    interval_anomaly_ratio = video_stats.get("interval_anomaly_ratio", 0)
    anomaly_data = [{
        "i": i,
        "pts_t": round(f["pts_time"], 6),
        "interval": f["interval_ms"],
        "type": f["pict_type"],
        "kf": f["key_frame"],
        "coded_num": f["coded_picture_number"],
        "display_num": f["display_picture_number"],
        "repeat": f["repeat_pict"],
    } for i, f in enumerate(sampled_video) if f["interval_ms"] is not None and f["interval_ms"] > 33]

    # 音频帧JSON
    MAX_AUDIO_JSON = 30000
    if len(audio_frames) > MAX_AUDIO_JSON:
        a_step = len(audio_frames) // MAX_AUDIO_JSON
        sampled_audio = audio_frames[::a_step]
    else:
        sampled_audio = audio_frames

    audio_json = json.dumps([{
        "i": i,
        "pts_t": round(f["pts_time"], 6),
        "size": f["pkt_size"],
        "interval": f["interval_ms"],
    } for i, f in enumerate(sampled_audio)], separators=(',', ':'))

    # ── 码率窗口 JSON ──
    bitrate_json = json.dumps(video_stats.get("bitrate_windows", []), separators=(',', ':'))

    # ── GOP JSON ──
    gop_sizes = video_stats.get("gop_sizes", [])
    gop_json = json.dumps(gop_sizes[:500])  # 最多500个GOP

    # ── 直方图 JSON ──
    hist_json = json.dumps(video_stats.get("interval_histogram", {}))

    # ── 流信息表格 ──
    def fmt_bitrate(br):
        if not br:
            return "-"
        br = int(br)
        if br > 1_000_000:
            return f"{br/1_000_000:.2f} Mbps"
        elif br > 1_000:
            return f"{br/1_000:.0f} kbps"
        return f"{br} bps"

    def fmt_duration(d):
        if not d:
            return "-"
        d = float(d)
        h = int(d // 3600)
        m = int((d % 3600) // 60)
        s = d % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:06.3f}"
        return f"{m}:{s:06.3f}"

    def fmt_size(b):
        if b > 1024*1024*1024:
            return f"{b/(1024*1024*1024):.2f} GB"
        elif b > 1024*1024:
            return f"{b/(1024*1024):.2f} MB"
        elif b > 1024:
            return f"{b/1024:.1f} KB"
        return f"{b} B"

    stream_rows = ""
    for s in stream_info:
        codec = s.get("codec_long_name", s.get("codec_name", "-"))
        codec_name = s.get("codec_name", "")
        st_type = s.get("codec_type", "-")
        lang = s.get("tags", {}).get("language", "-") if s.get("tags") else "-"
        br = fmt_bitrate(s.get("bit_rate") or s.get("tags", {}).get("BPS") if s.get("tags") else None)
        profile = s.get("profile", "")
        pix_fmt = s.get("pix_fmt", "") if st_type == "video" else ""
        res = f'{s.get("width","")}x{s.get("height","")}' if st_type == "video" else ""
        fps = ""
        if st_type == "video":
            fps_val = s.get("r_frame_rate", "")
            if "/" in fps_val:
                num, den = fps_val.split("/")
                if int(den) > 0:
                    fps = f"{int(num)/int(den):.3f}"
            else:
                fps = fps_val

        stream_rows += f"""<tr>
            <td>{s.get('index','')}</td>
            <td>{st_type}</td>
            <td><code>{codec_name}</code></td>
            <td>{codec}</td>
            <td>{profile}</td>
            <td>{res}</td>
            <td>{fps}</td>
            <td>{pix_fmt}</td>
            <td>{br}</td>
            <td>{lang}</td>
        </tr>"""

    # ── 容器信息 ──
    fmt_tags = container_info.get("tags", {})
    tag_rows = ""
    for k, v in fmt_tags.items():
        tag_rows += f"<tr><td>{k}</td><td>{v}</td></tr>"

    # ── 帧类型分布 ──
    pict = video_stats.get("pict_types", {})
    total_vf = video_stats.get("total_frames", 1)
    i_count = pict.get("I", 0)
    p_count = pict.get("P", 0)
    b_count = pict.get("B", 0)
    other_count = total_vf - i_count - p_count - b_count

    # ── 章节 ──
    chapter_html = ""
    if chapters:
        for c in chapters:
            start = fmt_duration(c.get("start_time", 0))
            end = fmt_duration(c.get("end_time", 0))
            title = c.get("tags", {}).get("title", "未命名") if c.get("tags") else "未命名"
            chapter_html += f"<tr><td>{c.get('id','')}</td><td>{start}</td><td>{end}</td><td>{title}</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>视频帧分析 — {filename}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg:#0b0d14;--card:#13161f;--card2:#181b26;--border:#232736;
  --text:#e2e4ed;--muted:#7a7f96;--accent:#6c8cff;
  --green:#34d399;--red:#f87171;--yellow:#fbbf24;--purple:#a78bfa;
  --blue:#60a5fa;--cyan:#22d3ee;--orange:#fb923c;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
  background:var(--bg);color:var(--text);line-height:1.6}}
a{{color:var(--accent);text-decoration:none}}

/* Header */
.hdr{{background:linear-gradient(135deg,#1a1e2e 0%,#0f1219 100%);
  border-bottom:1px solid var(--border);padding:24px 32px}}
.hdr h1{{font-size:22px;font-weight:700;margin-bottom:4px}}
.hdr .sub{{font-size:13px;color:var(--muted)}}
.hdr .badge{{display:inline-block;background:rgba(108,140,255,.12);color:var(--accent);
  font-size:11px;padding:2px 8px;border-radius:12px;margin-top:6px}}

/* Container */
.wrap{{max-width:1400px;margin:0 auto;padding:20px}}

/* Tab navigation */
.tabs{{display:flex;gap:4px;margin-bottom:20px;background:var(--card);
  border-radius:10px;padding:4px;border:1px solid var(--border);flex-wrap:wrap}}
.tab{{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:500;
  cursor:pointer;color:var(--muted);transition:all .2s}}
.tab:hover{{color:var(--text);background:var(--card2)}}
.tab.active{{background:var(--accent);color:#fff}}
.tab-content{{display:none}}.tab-content.active{{display:block}}

/* Cards */
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:20px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px}}
.card .label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.card .val{{font-size:24px;font-weight:700;margin:4px 0}}
.card .hint{{font-size:11px;color:var(--muted)}}

/* Section */
.sec{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:20px}}
.sec h2{{font-size:16px;font-weight:600;margin-bottom:4px}}
.sec .desc{{font-size:12px;color:var(--muted);margin-bottom:16px}}
.chart-box{{position:relative;height:320px;margin-bottom:8px}}
.chart-box.tall{{height:420px}}

/* Table */
.tbl-wrap{{overflow-x:auto;max-height:600px;overflow-y:auto;border-radius:8px;border:1px solid var(--border)}}
table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}
thead{{position:sticky;top:0;z-index:2}}
th{{background:var(--card2);color:var(--muted);font-weight:600;padding:8px 12px;
  text-align:left;border-bottom:2px solid var(--border);cursor:pointer;user-select:none}}
th:hover{{color:var(--accent)}}
td{{padding:6px 12px;border-bottom:1px solid var(--border)}}
tr:hover td{{background:rgba(108,140,255,.06)}}
.type-I{{color:var(--red);font-weight:700}}
.type-P{{color:var(--yellow);font-weight:600}}
.type-B{{color:var(--green);font-weight:600}}
.kf{{background:rgba(248,113,113,.1)}}
.anomaly-highlight{{background:rgba(255,200,50,.08)}}
.anomaly-highlight td{{border-left:3px solid var(--red)}}

/* Frame / Anomaly table */
.frame-table{{font-size:12px;width:100%}}
.frame-table th{{background:var(--card2);color:var(--muted);font-weight:600;padding:8px 12px;
  text-align:left;border-bottom:2px solid var(--border)}}
.frame-table td{{padding:6px 12px;border-bottom:1px solid var(--border)}}
.frame-table tr:hover td{{background:rgba(108,140,255,.06)}}

/* Search */
.search{{margin-bottom:12px}}
.search input{{background:var(--card2);border:1px solid var(--border);border-radius:8px;
  padding:8px 14px;color:var(--text);font-size:13px;width:280px;outline:none}}
.search input:focus{{border-color:var(--accent)}}
.search select{{background:var(--card2);border:1px solid var(--border);border-radius:8px;
  padding:8px 12px;color:var(--text);font-size:13px;margin-left:8px;outline:none}}

/* Footer */
.footer{{text-align:center;font-size:11px;color:var(--muted);padding:20px;opacity:.6}}

/* Stats grid */
.stats-row{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}}
.stats-row .sec{{margin-bottom:0}}

/* Responsive */
@media(max-width:768px){{.cards{{grid-template-columns:repeat(2,1fr)}}
  .stats-row{{grid-template-columns:1fr}}.search input{{width:100%}}}}

/* Tooltip */
.tip{{position:relative;cursor:help;border-bottom:1px dotted var(--muted)}}
.tip:hover::after{{content:attr(data-tip);position:absolute;bottom:100%;left:50%;
  transform:translateX(-50%);background:#333;color:#fff;padding:4px 8px;border-radius:4px;
  font-size:11px;white-space:nowrap;z-index:10}}
</style>
</head>
<body>

<div class="hdr">
  <h1>🎬 视频帧级分析报告</h1>
  <div class="sub">{filename} &nbsp;·&nbsp; {fmt_size(filesize)} &nbsp;·&nbsp; 生成时间 {now_str}</div>
  <span class="badge">Powered by ffprobe</span>
</div>

<div class="wrap">

<!-- Summary Cards -->
<div class="cards">
  <div class="card">
    <div class="label">容器格式</div>
    <div class="val" style="font-size:18px;color:var(--accent)">{container_info.get('format_long_name', container_info.get('format_name','?'))}</div>
  </div>
  <div class="card">
    <div class="label">总时长</div>
    <div class="val" style="color:var(--cyan)">{fmt_duration(container_info.get('duration', video_stats.get('duration_sec',0)))}</div>
  </div>
  <div class="card">
    <div class="label">文件大小</div>
    <div class="val" style="color:var(--purple)">{fmt_size(filesize)}</div>
  </div>
  <div class="card">
    <div class="label">总码率</div>
    <div class="val" style="color:var(--yellow)">{fmt_bitrate(container_info.get('bit_rate'))}</div>
  </div>
  <div class="card">
    <div class="label">流数量</div>
    <div class="val" style="color:var(--green)">{len(stream_info)}</div>
    <div class="hint">视频{sum(1 for s in stream_info if s.get('codec_type')=='video')} 音频{sum(1 for s in stream_info if s.get('codec_type')=='audio')} 字幕{sum(1 for s in stream_info if s.get('codec_type')=='subtitle')}</div>
  </div>
  <div class="card">
    <div class="label">视频帧总数</div>
    <div class="val" style="color:var(--red)">{video_stats.get('total_frames',0):,}</div>
  </div>
</div>

<!-- Tabs -->
<div class="tabs" id="tabs">
  <div class="tab active" data-tab="overview">📊 概览</div>
  <div class="tab" data-tab="timing">⏱ PTS/DTS 时序</div>
  <div class="tab" data-tab="gop">🎞 GOP 结构</div>
  <div class="tab" data-tab="bitrate">📈 码率分析</div>
  <div class="tab" data-tab="frames">📋 帧数据表</div>
  <div class="tab" data-tab="streams">🔧 流/格式详情</div>
</div>

<!-- ════════════ Tab: 概览 ════════════ -->
<div class="tab-content active" id="tab-overview">
  <div class="cards" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">
    <div class="card">
      <div class="label">视频编码</div>
      <div class="val" style="font-size:16px;color:var(--blue)">{video_stream.get('codec_long_name','') if video_stream else '-'}</div>
      <div class="hint">{video_stream.get('profile','') if video_stream else ''} &nbsp; {video_stream.get('pix_fmt','') if video_stream else ''}</div>
    </div>
    <div class="card">
      <div class="label">分辨率</div>
      <div class="val" style="font-size:18px;color:var(--cyan)">{video_stream.get('width','') if video_stream else ''}×{video_stream.get('height','') if video_stream else ''}</div>
    </div>
    <div class="card">
      <div class="label">帧率</div>
      <div class="val" style="font-size:18px;color:var(--orange)">{video_stream.get('r_frame_rate','') if video_stream else ''}</div>
    </div>
    <div class="card">
      <div class="label">平均帧间隔 (PTS)</div>
      <div class="val" style="color:var(--green)">{video_stats.get('avg_pts_interval',0)*1000:.2f}<span style="font-size:12px">ms</span></div>
      <div class="hint">min {video_stats.get('min_pts_interval',0)*1000:.3f}ms · max {video_stats.get('max_pts_interval',0)*1000:.3f}ms</div>
    </div>
    <div class="card">
      <div class="label">平均帧间隔 (DTS)</div>
      <div class="val" style="color:var(--yellow)">{video_stats.get('avg_dts_interval',0)*1000:.2f}<span style="font-size:12px">ms</span></div>
      <div class="hint">min {video_stats.get('min_dts_interval',0)*1000:.3f}ms · max {video_stats.get('max_dts_interval',0)*1000:.3f}ms</div>
    </div>
    <div class="card">
      <div class="label">DTS-PTS 平均偏移</div>
      <div class="val" style="color:var(--purple)">{video_stats.get('avg_dts_pts_offset',0)*1000:.2f}<span style="font-size:12px">ms</span></div>
      <div class="hint">范围 [{video_stats.get('min_dts_pts_offset',0)*1000:.1f}, {video_stats.get('max_dts_pts_offset',0)*1000:.1f}] ms</div>
    </div>
    <div class="card">
      <div class="label">关键帧 (I帧)</div>
      <div class="val" style="color:var(--red)">{i_count:,}</div>
      <div class="hint">占比 {i_count/total_vf*100:.1f}%</div>
    </div>
    <div class="card">
      <div class="label">P帧 / B帧</div>
      <div class="val" style="font-size:16px;color:var(--yellow)">{p_count:,} <span style="font-size:12px;color:var(--muted)">P</span> &nbsp; {b_count:,} <span style="font-size:12px;color:var(--green)">B</span></div>
    </div>
    <div class="card">
      <div class="label">平均 GOP 长度</div>
      <div class="val" style="color:var(--cyan)">{video_stats.get('avg_gop_size',0):.1f}</div>
      <div class="hint">min {video_stats.get('min_gop_size',0)} · max {video_stats.get('max_gop_size',0)}</div>
    </div>
    <div class="card">
      <div class="label">帧间隔异常 (&gt;33ms)</div>
      <div class="val" style="color:var(--red)">{len(anomaly_intervals):,}<span style="font-size:12px">帧</span></div>
      <div class="hint">占比 {interval_anomaly_ratio*100:.2f}%</div>
    </div>
    <div class="card">
      <div class="label">帧大小标准差</div>
      <div class="val" style="color:var(--muted)">{video_stats.get('std_pts_interval',0)*1000:.3f}<span style="font-size:12px">ms</span></div>
      <div class="hint">PTS间隔波动率</div>
    </div>
    {"<div class='card'><div class='label'>色彩空间</div><div class='val' style='font-size:14px;color:var(--cyan)'>"+video_stats.get('color_info',{}).get('color_space','未知')+"</div><div class='hint'>"+video_stats.get('color_info',{}).get('color_range','')+" · "+video_stats.get('color_info',{}).get('color_primaries','')+" · "+video_stats.get('color_info',{}).get('color_transfer','')+"</div></div>" if video_stats.get('color_info',{}).get('color_space') else ""}
    {"<div class='card'><div class='label'>编码序号异常</div><div class='val' style='color:var(--orange)'>"+str(video_stats.get('coded_dupes',0))+" <span style='font-size:12px'>重复</span></div><div class='hint'>丢帧: "+str(len(video_stats.get('coded_gaps',[])))+" 处</div></div>" if video_stats.get('coded_gaps') or video_stats.get('coded_dupes',0) > 0 else ""}
    {"<div class='card'><div class='label'>repeat_pict</div><div class='val' style='color:var(--yellow)'>"+str(video_stats.get('repeat_pict_counts',{}))+"</div><div class='hint'>0=正常 1+=3:2 pulldown</div></div>" if any(k > 0 for k in video_stats.get('repeat_pict_counts',{}).keys()) else ""}
    {"<div class='card'><div class='label'>画面裁剪</div><div class='val' style='font-size:14px;color:var(--purple)'>"+str(video_stats.get('crop_info',[])[0])+"</div><div class='hint'>top/bottom/left/right</div></div>" if video_stats.get('crop_info') else ""}
    {"<div class='card'><div class='label'>HDR</div><div class='val' style='color:var(--green)'>"+video_stats.get('hdr_info',[{}])[0].get('type','')+"</div><div class='hint'>maxCLL: "+str(video_stats.get('hdr_info',[{}])[0].get('max_content',''))+" · maxFALL: "+str(video_stats.get('hdr_info',[{}])[0].get('max_average',''))+"</div></div>" if video_stats.get('hdr_info') else ""}
  </div>

  <!-- 帧类型饼图 + 间隔分布 -->
  <div class="stats-row">
    <div class="sec">
      <h2>帧类型分布</h2>
      <div class="desc">I / P / B 帧数量占比</div>
      <div class="chart-box"><canvas id="chartPictType"></canvas></div>
    </div>
    <div class="sec">
      <h2>PTS 间隔分布 (毫秒)</h2>
      <div class="desc">帧时间间隔的直方图，反映时间基的规律性</div>
      <div class="chart-box"><canvas id="chartHist"></canvas></div>
    </div>
  </div>
</div>

<!-- ════════════ Tab: PTS/DTS 时序 ════════════ -->
<div class="tab-content" id="tab-timing">
  <div class="sec">
    <h2>PTS vs DTS 时间线</h2>
    <div class="desc">每帧的 PTS 和 DTS 随时间变化。B帧会导致 PTS > DTS（解码延迟）</div>
    <div class="chart-box tall"><canvas id="chartPtsDts"></canvas></div>
  </div>
  <div class="sec">
    <h2>DTS-PTS 偏移量</h2>
    <div class="desc">每帧 PTS 与 DTS 的差值（秒），反映解码重排序延迟。B帧出现时此值为正</div>
    <div class="chart-box tall"><canvas id="chartDtsPtsOffset"></canvas></div>
  </div>
  <div class="sec">
    <h2>PTS 间隔时序</h2>
    <div class="desc">相邻帧 PTS 之差。恒定帧率应为水平线，VFR 会波动</div>
    <div class="chart-box tall"><canvas id="chartPtsInterval"></canvas></div>
  </div>
  <div class="sec">
    <h2>DTS 间隔时序</h2>
    <div class="desc">相邻帧 DTS 之差。DTS 始终单调递增，间隔应相对稳定</div>
    <div class="chart-box tall"><canvas id="chartDtsInterval"></canvas></div>
  </div>
  <div class="sec">
    <h2>帧间隔异常明细 (&gt;33ms)</h2>
    <div class="desc">前后帧 pts_time 差值超过 33ms 的帧。30fps 对应 ~33ms 一帧，超过说明丢帧或卡顿</div>
    <div class="search"><input type="text" id="anomalySearch" placeholder="搜索帧号..." oninput="filterAnomaly()"></div>
    <div style="max-height:400px;overflow-y:auto;margin-top:8px">
      <table id="anomalyTable" class="frame-table" style="width:100%">
        <thead><tr><th>#</th><th>pts_time</th><th>间隔(ms)</th><th>帧类型</th><th>关键帧</th><th>编码序号</th><th>显示序号</th><th>repeat</th></tr></thead>
        <tbody>
      {"".join(f'<tr class="anomaly-row"><td>{f["i"]}</td><td>{f["pts_t"]}</td><td style="color:var(--red);font-weight:bold">{f["interval"]}</td><td>{f["type"]}</td><td>{f["kf"]}</td><td>{f.get("coded_num","-")}</td><td>{f.get("display_num","-")}</td><td>{f.get("repeat",0)}</td></tr>' for f in anomaly_data)}
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- ════════════ Tab: GOP 结构 ════════════ -->
<div class="tab-content" id="tab-gop">
  <div class="sec">
    <h2>GOP 大小分布</h2>
    <div class="desc">每个 GOP（关键帧间隔）的帧数。不同编码器和场景切换会影响 GOP 长度</div>
    <div class="chart-box tall"><canvas id="chartGop"></canvas></div>
  </div>
  <div class="sec">
    <h2>帧类型时间线</h2>
    <div class="desc">I帧(红) / P帧(黄) / B帧(绿) 在时间轴上的分布。直观展示 GOP 结构和 B帧密度</div>
    <div class="chart-box tall"><canvas id="chartFrameType"></canvas></div>
  </div>
</div>

<!-- ════════════ Tab: 码率分析 ════════════ -->
<div class="tab-content" id="tab-bitrate">
  <div class="sec">
    <h2>视频码率曲线 (kbps)</h2>
    <div class="desc">按1秒滑动窗口计算的瞬时码率。I帧处出现尖峰是正常的</div>
    <div class="chart-box tall"><canvas id="chartBitrate"></canvas></div>
  </div>
  <div class="sec">
    <h2>帧大小分布</h2>
    <div class="desc">每帧数据包大小（字节）。I帧通常最大，B帧最小</div>
    <div class="chart-box tall"><canvas id="chartFrameSize"></canvas></div>
  </div>
</div>

<!-- ════════════ Tab: 帧数据表 ════════════ -->
<div class="tab-content" id="tab-frames">
  <div class="sec">
    <h2>帧级详细数据</h2>
    <div class="desc">共 {video_stats.get('total_frames',0):,} 个视频帧。支持搜索和排序（点击表头）</div>
    <div class="search">
      <input type="text" id="frameSearch" placeholder="搜索帧号、时间、类型...">
      <select id="frameFilter">
        <option value="">全部类型</option>
        <option value="I">仅 I 帧</option>
        <option value="P">仅 P 帧</option>
        <option value="B">仅 B 帧</option>
        <option value="kf">仅关键帧</option>
      </select>
    </div>
    <div class="tbl-wrap">
      <table id="frameTable">
        <thead><tr>
          <th data-col="0">#</th>
          <th data-col="1">PTS</th>
          <th data-col="2">PTS时间(s)</th>
          <th data-col="3">DTS</th>
          <th data-col="4">DTS时间(s)</th>
          <th data-col="5">DTS-PTS(ms)</th>
          <th data-col="6">类型</th>
          <th data-col="7">关键帧</th>
          <th data-col="8">时长(ms)</th>
          <th data-col="9">大小(B)</th>
          <th data-col="10">间隔(ms)</th>
          <th data-col="11">编码序号</th>
          <th data-col="12">显示序号</th>
          <th data-col="13">repeat</th>
          <th data-col="14">BET时间(s)</th>
        </tr></thead>
        <tbody id="frameBody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ════════════ Tab: 流/格式详情 ════════════ -->
<div class="tab-content" id="tab-streams">
  <div class="sec">
    <h2>流信息</h2>
    <div class="desc">所有媒体流的编解码器、分辨率、码率等详细信息</div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>索引</th><th>类型</th><th>编码</th><th>完整名称</th><th>Profile</th><th>分辨率</th><th>帧率</th><th>像素格式</th><th>码率</th><th>语言</th></tr></thead>
        <tbody>{stream_rows}</tbody>
      </table>
    </div>
  </div>
  {"<div class='sec'><h2>容器标签</h2><div class='tbl-wrap'><table><thead><tr><th>标签</th><th>值</th></tr></thead><tbody>"+tag_rows+"</tbody></table></div></div>" if tag_rows else ""}
  {"<div class='sec'><h2>章节</h2><div class='tbl-wrap'><table><thead><tr><th>ID</th><th>开始</th><th>结束</th><th>标题</th></tr></thead><tbody>"+chapter_html+"</tbody></table></div></div>" if chapter_html else ""}
</div>

</div><!-- /wrap -->

<div class="footer">
  Video Frame Analyzer · 由 ffprobe 驱动 · 报告自动生成
</div>

<script>
// ══════════════════════════════════════
// 数据
// ══════════════════════════════════════
const VF = {video_json};
const AF = {audio_json};
const BR = {bitrate_json};
const GOP = {gop_json};
const HIST = {hist_json};
const TOTAL_VF = {video_stats.get('total_frames',0)};

// ══════════════════════════════════════
// Tab 切换
// ══════════════════════════════════════
document.querySelectorAll('.tab').forEach(tab => {{
  tab.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  }});
}});

// ══════════════════════════════════════
// Chart.js 全局配置
// ══════════════════════════════════════
Chart.defaults.color = '#7a7f96';
Chart.defaults.borderColor = '#232736';
Chart.defaults.font.family = "'Segoe UI', sans-serif";
Chart.defaults.font.size = 11;
const chartOpts = {{
  responsive:true, maintainAspectRatio:false,
  plugins:{{legend:{{display:false}}}},
  scales:{{x:{{grid:{{color:'#ffffff06'}},ticks:{{maxTicksLimit:20}}}},y:{{grid:{{color:'#ffffff08'}}}}}},
  elements:{{point:{{radius:0}},line:{{borderWidth:1.5}}}}
}};

// ══════════════════════════════════════
// 概览图表
// ══════════════════════════════════════

// 帧类型饼图
new Chart(document.getElementById('chartPictType'), {{
  type:'doughnut',
  data:{{
    labels:['I 帧','P 帧','B 帧'],
    datasets:[{{data:[{i_count},{p_count},{b_count}],
      backgroundColor:['#f87171','#fbbf24','#34d399'],
      borderWidth:0}}]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'bottom',labels:{{padding:16,usePointStyle:true}}}},
    tooltip:{{callbacks:{{label:ctx=>ctx.label+': '+ctx.parsed.toLocaleString()+' ('+(ctx.parsed/{total_vf}*100).toFixed(1)+'%)'}}}}}}}}
}});

// PTS 间隔直方图
const histLabels = Object.keys(HIST).map(Number).sort((a,b)=>a-b);
const histData = histLabels.map(k => HIST[k]);
new Chart(document.getElementById('chartHist'), {{
  type:'bar',
  data:{{labels:histLabels.map(k=>k+'ms'),datasets:[{{data:histData,backgroundColor:'#6c8cff',borderRadius:2}}]}},
  options:{{...chartOpts,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{title:ctx=>ctx[0].label+'ms',label:ctx=>ctx.parsed.y.toLocaleString()+' 帧'}}}}}}}}
}});

// ══════════════════════════════════════
// 时序图表
// ══════════════════════════════════════
const ptsLabels = VF.map((_,i)=>i);

// PTS vs DTS
new Chart(document.getElementById('chartPtsDts'), {{
  type:'line',
  data:{{labels:ptsLabels,datasets:[
    {{label:'PTS',data:VF.map(f=>f.pts_t),borderColor:'#f87171',fill:false}},
    {{label:'DTS',data:VF.map(f=>f.dts_t),borderColor:'#60a5fa',fill:false}}
  ]}},
  options:{{...chartOpts,plugins:{{legend:{{display:true,position:'top',labels:{{usePointStyle:true,padding:12}}}}}},
    scales:{{x:{{title:{{display:true,text:'帧序号',color:'#7a7f96'}}}},y:{{title:{{display:true,text:'时间 (秒)',color:'#7a7f96'}}}}}}}}
}});

// DTS-PTS offset
new Chart(document.getElementById('chartDtsPtsOffset'), {{
  type:'line',
  data:{{labels:ptsLabels,datasets:[{{label:'DTS-PTS偏移',data:VF.map(f=>f.dts_pts),borderColor:'#a78bfa',
    backgroundColor:'rgba(167,139,250,0.1)',fill:true}}]}},
  options:{{...chartOpts,
    scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'偏移 (秒)'}}}}}}}}
}});

// PTS interval
const ptsIv = [];
for(let i=1;i<VF.length;i++) ptsIv.push(VF[i].pts_t - VF[i-1].pts_t);
new Chart(document.getElementById('chartPtsInterval'), {{
  type:'line',
  data:{{labels:ptsLabels.slice(1),datasets:[{{label:'PTS间隔',data:ptsIv,borderColor:'#34d399',
    backgroundColor:'rgba(52,211,153,0.08)',fill:true}}]}},
  options:{{...chartOpts,
    scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'间隔 (秒)'}}}}}}}}
}});

// DTS interval
const dtsIv = [];
const dtsFrames = VF.filter(f=>f.dts_t>0||VF.indexOf(f)===0);
for(let i=1;i<VF.length;i++) dtsIv.push(VF[i].dts_t - VF[i-1].dts_t);
new Chart(document.getElementById('chartDtsInterval'), {{
  type:'line',
  data:{{labels:ptsLabels.slice(1),datasets:[{{label:'DTS间隔',data:dtsIv,borderColor:'#fbbf24',
    backgroundColor:'rgba(251,191,36,0.08)',fill:true}}]}},
  options:{{...chartOpts,
    scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'间隔 (秒)'}}}}}}}}
}});

// ══════════════════════════════════════
// GOP 图表
// ══════════════════════════════════════
new Chart(document.getElementById('chartGop'), {{
  type:'bar',
  data:{{labels:GOP.map((_,i)=>'GOP '+(i+1)),datasets:[{{data:GOP,backgroundColor:GOP.map(v=>v>30?'#f87171':v>20?'#fbbf24':'#34d399'),borderRadius:3}}]}},
  options:{{...chartOpts,indexAxis:'y',
    scales:{{x:{{title:{{display:true,text:'帧数'}}}},y:{{ticks:{{maxTicksLimit:30}}}}}},
    plugins:{{tooltip:{{callbacks:{{label:ctx=>ctx.parsed.x+' 帧'}}}}}}}}
}});

// 帧类型时间线
const typeColors = {{'I':'#f87171','P':'#fbbf24','B':'#34d399'}};
const typeNum = {{'I':3,'P':2,'B':1}};
new Chart(document.getElementById('chartFrameType'), {{
  type:'bar',
  data:{{labels:VF.map((_,i)=>i),datasets:[{{data:VF.map(f=>typeNum[f.type]||0),
    backgroundColor:VF.map(f=>typeColors[f.type]||'#555'),borderWidth:0,barPercentage:1,borderRadius:0}}]}},
  options:{{...chartOpts,
    scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{ticks:{{callback:v=>({{3:'I',2:'P',1:'B'}})[v]||''}},min:0,max:4}}}},
    plugins:{{tooltip:{{callbacks:{{label:ctx=>VF[ctx.dataIndex]?.type||'?'}}}}}}}}
}});

// ══════════════════════════════════════
// 码率图表
// ══════════════════════════════════════
if(BR.length > 0) {{
  new Chart(document.getElementById('chartBitrate'), {{
    type:'line',
    data:{{labels:BR.map(b=>b.time.toFixed(1)+'s'),datasets:[{{label:'码率',data:BR.map(b=>b.kbps),
      borderColor:'#22d3ee',backgroundColor:'rgba(34,211,238,0.1)',fill:true,tension:0.3}}]}},
    options:{{...chartOpts,
      scales:{{x:{{title:{{display:true,text:'时间 (秒)'}}}},y:{{title:{{display:true,text:'kbps'}}}}}}}}
  }});
}} else {{
  document.getElementById('chartBitrate').parentElement.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px">码率数据不足</p>';
}}

// 帧大小
new Chart(document.getElementById('chartFrameSize'), {{
  type:'line',
  data:{{labels:ptsLabels,datasets:[{{label:'帧大小',data:VF.map(f=>f.size),
    borderColor:'#fb923c',backgroundColor:'rgba(251,146,60,0.08)',fill:true}}]}},
  options:{{...chartOpts,
    scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'字节'}}}}}}}}
}});

// ══════════════════════════════════════
// 帧数据表格
// ══════════════════════════════════════
const tbody = document.getElementById('frameBody');
const PAGE_SIZE = 200;
let allRows = VF.map((f,i) => {{
  const intervalStr = f.interval != null ? (f.interval > 33 ? '<span style="color:var(--red);font-weight:bold">' + f.interval.toFixed(2) + '</span>' : f.interval.toFixed(2)) : '-';
  return {{
  html: `<tr class="${{f.kf?'kf':''}}${{f.interval!=null && f.interval>33?' anomaly-highlight':''}}">
    <td>${{i}}</td>
    <td>${{VF.length>1?(f.pts_t*90000).toFixed(0):'-'}}</td>
    <td>${{f.pts_t.toFixed(6)}}</td>
    <td>${{VF.length>1?(f.dts_t*90000).toFixed(0):'-'}}</td>
    <td>${{f.dts_t.toFixed(6)}}</td>
    <td>${{(f.dts_pts*1000).toFixed(2)}}</td>
    <td class="type-${{f.type}}">${{f.type}}</td>
    <td>${{f.kf?'✅':'-'}}</td>
    <td>${{(f.dur*1000).toFixed(2)}}</td>
    <td>${{f.size.toLocaleString()}}</td>
    <td>${{intervalStr}}</td>
    <td>${{f.coded_num||'-'}}</td>
    <td>${{f.display_num||'-'}}</td>
    <td>${{f.repeat||0}}</td>
    <td>${{f.bet_t?f.bet_t.toFixed(6):'-'}}</td>
  </tr>`,
  type: f.type,
  kf: f.kf
  }};
}});

let filtered = [...allRows];
let displayCount = Math.min(PAGE_SIZE, filtered.length);

function renderTable() {{
  tbody.innerHTML = filtered.slice(0, displayCount).map(r=>r.html).join('');
}}

renderTable();

// 无限滚动
document.querySelector('.tbl-wrap')?.addEventListener('scroll', function() {{
  if(this.scrollTop + this.clientHeight >= this.scrollHeight - 100) {{
    if(displayCount < filtered.length) {{
      displayCount = Math.min(displayCount + PAGE_SIZE, filtered.length);
      renderTable();
    }}
  }}
}});

// 搜索
document.getElementById('frameSearch').addEventListener('input', function() {{
  const q = this.value.toLowerCase();
  filtered = allRows.filter((r,i) => {{
    const f = VF[i];
    return !q || String(i).includes(q) || f.type.toLowerCase().includes(q) ||
      f.pts_t.toFixed(6).includes(q) || f.dts_t.toFixed(6).includes(q);
  }});
  displayCount = Math.min(PAGE_SIZE, filtered.length);
  renderTable();
}});

// 筛选
document.getElementById('frameFilter').addEventListener('change', function() {{
  const v = this.value;
  if(!v) {{ filtered = [...allRows]; }}
  else if(v === 'kf') {{ filtered = allRows.filter(r=>r.kf); }}
  else {{ filtered = allRows.filter(r=>r.type===v); }}
  displayCount = Math.min(PAGE_SIZE, filtered.length);
  renderTable();
}});

// 排序
let sortCol = -1, sortAsc = true;
document.querySelectorAll('#frameTable th').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = parseInt(th.dataset.col);
    if(sortCol === col) sortAsc = !sortAsc;
    else {{ sortCol = col; sortAsc = true; }}
    // 简单排序：重置到原始数据再排
    const keys = ['i','pts','pts_t','dts','dts_t','dts_pts','type','kf','dur','size','interval','coded_num','display_num','repeat','bet_t'];
    filtered.sort((a,b) => {{
      const ai = allRows.indexOf(a), bi = allRows.indexOf(b);
      let va = VF[ai]?.[keys[col]] ?? 0, vb = VF[bi]?.[keys[col]] ?? 0;
      if(typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      return sortAsc ? va - vb : vb - va;
    }});
    renderTable();
  }});
}});

// ══════════════════════════════════════
// 异常帧搜索过滤
// ══════════════════════════════════════
function filterAnomaly() {{
  const q = document.getElementById('anomalySearch').value.toLowerCase();
  document.querySelectorAll('#anomalyTable .anomaly-row').forEach(row => {{
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: video_analyzer <视频文件路径> [输出html路径]")
        print("示例: video_analyzer sample.mp4")
        print("      video_analyzer sample.mp4 report.html")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.isfile(filepath):
        print(f"[错误] 文件不存在: {filepath}", file=sys.stderr)
        sys.exit(1)

    outpath = sys.argv[2] if len(sys.argv) > 2 else None
    if not outpath:
        base = os.path.splitext(os.path.basename(filepath))[0]
        outpath = os.path.join(os.path.dirname(filepath) or ".", f"{base}_report.html")

    print(f"🎬 分析视频: {filepath}")
    print(f"   文件大小: {os.path.getsize(filepath) / (1024*1024):.1f} MB")

    # 1. 容器信息
    print("   [1/6] 读取容器信息...")
    container_info = get_container_info(filepath)

    # 2. 流信息
    print("   [2/6] 读取流信息...")
    stream_info = get_stream_info(filepath)

    # 3. 章节
    print("   [3/6] 读取章节信息...")
    chapters = get_chapters(filepath)

    # 4. 帧信息（最耗时）
    print("   [4/6] 分析所有帧（可能需要较长时间）...")
    t0 = time.time()
    frames = get_all_frames(filepath)
    t1 = time.time()
    print(f"         共读取 {len(frames)} 帧，耗时 {t1-t0:.1f}秒")

    # 5. 数据处理
    print("   [5/6] 处理帧数据...")
    video_frames, audio_frames, video_stats, audio_stats = analyze_frames(frames)

    # 6. 生成报告
    print("   [6/6] 生成 HTML 报告...")
    html = generate_html(filepath, container_info, stream_info, chapters,
                         video_frames, audio_frames, video_stats, audio_stats)

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ 报告已生成: {outpath}")
    print(f"   视频帧: {video_stats.get('total_frames',0):,}")
    print(f"   音频帧: {audio_stats.get('total_frames',0):,}")
    print(f"   报告大小: {os.path.getsize(outpath) / 1024:.0f} KB")
    print(f"\n   用浏览器打开查看交互式报告。")


if __name__ == "__main__":
    main()
