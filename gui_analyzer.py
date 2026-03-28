#!/usr/bin/env python3
"""
Video Frame Analyzer — 视频帧级分析工具 (GUI版)
专业级界面，支持文件选择、分析选项配置、进度显示、报告自动打开。
"""

import subprocess
import json
import sys
import os
import math
import time
import threading
import tempfile
import webbrowser
from pathlib import Path
from collections import Counter
from datetime import datetime

# ─────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ─────────────────────────────────────────────────────────
# ffprobe 封装
# ─────────────────────────────────────────────────────────

def get_ffprobe_path():
    # --- 新增：处理 PyInstaller 单文件打包后的内部路径 ---
    if hasattr(sys, '_MEIPASS'):
        # 当程序以 --onefile 运行时，资源被解压到这个临时文件夹
        bundle_dir = sys._MEIPASS
        for name in ['ffprobe.exe', 'ffprobe']:
            p = os.path.join(bundle_dir, name)
            if os.path.isfile(p):
                return p
    """获取 ffprobe 路径，支持打包后的同目录 ffmpeg、便携版ffmpeg目录"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    # 1. 同目录
    for name in ['ffprobe.exe', 'ffprobe']:
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p

    # 2. 同目录下的子目录
    for sub in ['ffmpeg', 'bin', 'tools', 'ffmpeg/bin']:
        for name in ['ffprobe.exe', 'ffprobe']:
            p = os.path.join(base, sub, name)
            if os.path.isfile(p):
                return p

    # 3. 常见便携版路径
    common_dirs = [
        r'C:\ffmpeg\bin',
        r'D:\ffmpeg\bin',
        r'C:\Program Files\ffmpeg\bin',
        os.path.expanduser(r'~\ffmpeg\bin'),
        os.path.expanduser(r'~\scoop\apps\ffmpeg\current\bin'),
    ]
    for d in common_dirs:
        for name in ['ffprobe.exe', 'ffprobe']:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p

    # 4. PATH
    return 'ffprobe'


def run_ffprobe(filepath, args, ffprobe_path='ffprobe'):
    cmd = [ffprobe_path, "-v", "quiet", "-print_format", "json", *args, filepath]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            # 尝试不带 quiet 模式获取错误信息
            err_info = result.stderr.strip() if result.stderr else "unknown error"
            print(f"[ffprobe error] rc={result.returncode} cmd={' '.join(cmd)} stderr={err_info[:200]}")
            return None
        out = result.stdout.strip()
        if not out:
            print(f"[ffprobe warn] empty stdout for cmd={' '.join(cmd)}")
            return None
        return json.loads(out)
    except subprocess.TimeoutExpired:
        print(f"[ffprobe timeout] cmd={' '.join(cmd)}")
        return None
    except json.JSONDecodeError as e:
        print(f"[ffprobe json error] {e} cmd={' '.join(cmd)}")
        return None
    except Exception as e:
        print(f"[ffprobe exception] {e} cmd={' '.join(cmd)}")
        return None


def get_container_info(filepath, ffprobe_path='ffprobe'):
    """获取容器信息，多策略尝试"""
    # 策略1: -show_format
    data = run_ffprobe(filepath, ["-show_format"], ffprobe_path)
    if data and data.get("format"):
        return data["format"]

    # 策略2: -show_format 同时拉 streams，从 format 字段取
    data = run_ffprobe(filepath, ["-show_format", "-show_streams"], ffprobe_path)
    if data and data.get("format"):
        return data["format"]

    # 策略3: 用 -show_entries format 只取 format 相关字段
    data = run_ffprobe(filepath, [
        "-show_entries",
        "format=format_name,format_long_name,duration,size,bit_rate,nb_streams,nb_programs,probe_score",
        "-show_entries", "format_tags"
    ], ffprobe_path)
    if data and data.get("format"):
        return data["format"]

    # 策略4: 用最简单的命令
    data = run_ffprobe(filepath, ["-show_format", "-loglevel", "error"], ffprobe_path)
    if data and data.get("format"):
        return data["format"]

    # 兜底: 从 probe 输出推断
    try:
        cmd = [ffprobe_path, "-v", "error", "-show_entries",
               "format=format_name,format_long_name,duration,bit_rate",
               "-of", "json", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if data.get("format"):
                return data["format"]
    except Exception:
        pass

    return {}


def safe_float(val, default=0.0):
    if val is None: return default
    try: return float(val)
    except: return default

def safe_int(val, default=0):
    if val is None: return default
    try: return int(val)
    except: return default


# ─────────────────────────────────────────────────────────
# 分析引擎
# ─────────────────────────────────────────────────────────

class VideoAnalyzer:
    def __init__(self, filepath, options, ffprobe_path, progress_callback=None):
        self.filepath = filepath
        self.options = options
        self.ffprobe = ffprobe_path
        self.progress = progress_callback or (lambda p, t: None)
        self.container_info = {}
        self.stream_info = []
        self.chapters = []
        self.video_frames = []
        self.audio_frames = []
        self.packets = []          # NEW: packet-level data
        self.video_stats = {}
        self.audio_stats = {}
        self.timestamp_health = {}  # NEW: timestamp health
        self.summary = {}           # NEW: summary/conclusion

    def run(self):
        """执行全部分析"""
        steps = []
        if self.options.get('container', True):
            steps.append(('container', '读取容器信息'))
        if self.options.get('streams', True):
            steps.append(('streams', '读取流信息'))
        if self.options.get('chapters', True):
            steps.append(('chapters', '读取章节信息'))
        need_frames = any(self.options.get(k, True) for k in
            ['frames_pts','frames_type','gop','bitrate','frame_table','packets','timestamps'])
        if need_frames:
            steps.append(('frames', '分析帧/Packet数据'))

        total = max(len(steps), 1)
        for i, (key, label) in enumerate(steps):
            self.progress(i / total * 100, label)
            print(f"[step {i+1}/{total}] {key}: {label}")
            if key == 'container':
                self.container_info = get_container_info(self.filepath, self.ffprobe)
                print(f"  container_info keys: {list(self.container_info.keys())[:10]}")
            elif key == 'streams':
                data = run_ffprobe(self.filepath, ["-show_streams"], self.ffprobe)
                self.stream_info = data.get("streams", []) if data else []
                print(f"  streams: {len(self.stream_info)}")
            elif key == 'chapters':
                data = run_ffprobe(self.filepath, ["-show_chapters"], self.ffprobe)
                self.chapters = data.get("chapters", []) if data else []
                print(f"  chapters: {len(self.chapters)}")
            elif key == 'frames':
                self._analyze_frames_and_packets()
                print(f"  video_frames: {len(self.video_frames)}, audio_frames: {len(self.audio_frames)}, packets: {len(self.packets)}")

        self._compute_timestamp_health()
        self._generate_summary()
        self.progress(100, '完成')
        return True

    def _analyze_frames_and_packets(self):
        """帧 + Packet 分析，分步查询避免 ffprobe 参数冲突"""

        # ── 第1步: 拉取帧数据 ──
        frames_data = run_ffprobe(self.filepath, [
            "-show_frames",
            "-show_entries",
            "frame=media_type,stream_index,pts,pts_time,dts,dts_time,"
            "duration,duration_time,pict_type,interlaced_frame,"
            "top_field_first,key_frame,pkt_size,pkt_pos,"
            "nb_samples,sample_fmt"
        ], self.ffprobe)
        frames_raw = frames_data.get("frames", []) if frames_data else []

        # ── 第2步: 拉取 packet 数据 ──
        packets_data = run_ffprobe(self.filepath, [
            "-show_packets",
            "-show_entries",
            "packet=codec_type,stream_index,pts,pts_time,dts,dts_time,"
            "duration,duration_time,size,flags"
        ], self.ffprobe)
        packets_raw = packets_data.get("packets", []) if packets_data else []

        # ── 如果帧数据为空，用 -show_frames 不带 -show_entries 再试一次 ──
        if not frames_raw:
            frames_data = run_ffprobe(self.filepath, ["-show_frames"], self.ffprobe)
            frames_raw = frames_data.get("frames", []) if frames_data else []

        # ── 如果 packet 数据为空，用 -show_packets 不带 -show_entries 再试一次 ──
        if not packets_raw:
            packets_data = run_ffprobe(self.filepath, ["-show_packets"], self.ffprobe)
            packets_raw = packets_data.get("packets", []) if packets_data else []

        # ── 处理帧 ──
        last_pts_time = {}
        for f in frames_raw:
            pts_time = safe_float(f.get("pts_time"))
            stream_idx = safe_int(f.get("stream_index"))
            prev = last_pts_time.get(stream_idx)
            if prev is not None:
                interval_ms = round((pts_time - prev) * 1000, 3)
            else:
                interval_ms = None
            last_pts_time[stream_idx] = pts_time

            fd = {
                "pts": safe_int(f.get("pts")),
                "pts_time": pts_time,
                "dts": safe_int(f.get("dts")),
                "dts_time": safe_float(f.get("dts_time")),
                "duration": safe_float(f.get("duration")),
                "duration_time": safe_float(f.get("duration_time")),
                "pict_type": f.get("pict_type", ""),
                "key_frame": safe_int(f.get("key_frame")),
                "pkt_size": safe_int(f.get("pkt_size")),
                "interval_ms": interval_ms,
                "interlaced": safe_int(f.get("interlaced_frame")),
                "top_field_first": safe_int(f.get("top_field_first")),
                "nb_samples": safe_int(f.get("nb_samples")),
            }
            if f.get("media_type") == "video":
                self.video_frames.append(fd)
            elif f.get("media_type") == "audio":
                self.audio_frames.append(fd)

        # ── 处理 packets ──
        for p in packets_raw:
            self.packets.append({
                "codec_type": p.get("codec_type", ""),
                "stream_index": safe_int(p.get("stream_index")),
                "pts_time": safe_float(p.get("pts_time")),
                "dts_time": safe_float(p.get("dts_time")),
                "duration_time": safe_float(p.get("duration_time")),
                "size": safe_int(p.get("size")),
                "flags": p.get("flags", ""),
            })

        self._compute_video_stats()
        self._compute_audio_stats()
        self._compute_packet_bitrate()

    def _compute_video_stats(self):
        vf = self.video_frames
        if not vf:
            return

        pts_t = [f["pts_time"] for f in vf]
        dts_t = [f["dts_time"] for f in vf if f["dts_time"] > 0 or f["dts"] >= 0]
        durs = [f["duration_time"] for f in vf if f["duration_time"] > 0]
        sizes = [f["pkt_size"] for f in vf]
        types = Counter(f["pict_type"] for f in vf)
        kf = sum(1 for f in vf if f["key_frame"])

        pts_iv = [pts_t[i] - pts_t[i-1] for i in range(1, len(pts_t))]
        dts_iv = [dts_t[i] - dts_t[i-1] for i in range(1, len(dts_t))] if len(dts_t) > 1 else []
        dts_pts_off = [f["pts_time"] - f["dts_time"] for f in vf if abs(f["pts_time"] - f["dts_time"]) < 100]

        # GOP
        gops, cg = [], 0
        for f in vf:
            if f["key_frame"] and cg > 0:
                gops.append(cg)
                cg = 0
            cg += 1
        if cg > 0: gops.append(cg)

        # Bitrate windows (frame-level)
        br = []
        if len(vf) > 1:
            ws, wb = 0, 0
            for f in vf:
                if f["pts_time"] - pts_t[ws] >= 1.0:
                    d = f["pts_time"] - pts_t[ws]
                    if d > 0:
                        br.append({"time": (pts_t[ws] + f["pts_time"]) / 2, "kbps": wb * 8 / d / 1000})
                    ws = vf.index(f)
                    wb = 0
                wb += f["pkt_size"]

        # Histogram
        hist = Counter()
        for iv in pts_iv:
            hist[round(iv * 1000)] += 1

        # 交织帧统计
        interlaced_count = sum(1 for f in vf if f.get("interlaced"))

        # 帧大小分布
        size_sorted = sorted(sizes)
        p50 = size_sorted[len(size_sorted)//2] if size_sorted else 0
        p95 = size_sorted[int(len(size_sorted)*0.95)] if size_sorted else 0
        p99 = size_sorted[int(len(size_sorted)*0.99)] if size_sorted else 0

        def avg(lst): return sum(lst) / len(lst) if lst else 0
        def std(lst):
            if len(lst) < 2: return 0
            m = avg(lst)
            return math.sqrt(sum((x - m)**2 for x in lst) / len(lst))

        avg_pts_interval = avg(pts_iv)
        avg_fps = (1 / avg_pts_interval) if avg_pts_interval > 0 else 0
        anomaly_threshold_ms = max(1, int(avg_pts_interval * 1000)) if avg_pts_interval > 0 else 33

        # 帧间间隔异常检测（基于平均帧率动态推导）
        frame_intervals = [f["interval_ms"] for f in vf if f["interval_ms"] is not None]
        anomaly_intervals = [iv for iv in frame_intervals if iv > anomaly_threshold_ms]
        interval_anomaly_ratio = len(anomaly_intervals) / len(frame_intervals) if frame_intervals else 0

        self.video_stats = {
            "total_frames": len(vf),
            "duration_sec": max(pts_t) - min(pts_t) if len(pts_t) > 1 else 0,
            "pts_range": (min(pts_t), max(pts_t)) if pts_t else (0, 0),
            "avg_pts_interval": avg_pts_interval,
            "avg_fps": avg_fps,
            "anomaly_interval_threshold_ms": anomaly_threshold_ms,
            "min_pts_interval": min(pts_iv) if pts_iv else 0,
            "max_pts_interval": max(pts_iv) if pts_iv else 0,
            "std_pts_interval": std(pts_iv),
            "avg_dts_interval": avg(dts_iv),
            "avg_duration": avg(durs),
            "pict_types": dict(types),
            "key_frames": kf,
            "total_size_bytes": sum(sizes),
            "avg_frame_size": avg(sizes),
            "avg_dts_pts_offset": avg(dts_pts_off),
            "max_dts_pts_offset": max(dts_pts_off) if dts_pts_off else 0,
            "min_dts_pts_offset": min(dts_pts_off) if dts_pts_off else 0,
            "gop_sizes": gops,
            "avg_gop_size": avg(gops),
            "min_gop_size": min(gops) if gops else 0,
            "max_gop_size": max(gops) if gops else 0,
            "bitrate_windows": br,
            "interval_histogram": dict(sorted(hist.items())),
            "frame_intervals": frame_intervals,
            "anomaly_intervals": anomaly_intervals,
            "anomaly_interval_count": len(anomaly_intervals),
            "interval_anomaly_ratio": interval_anomaly_ratio,
            "interlaced_count": interlaced_count,
            "frame_size_p50": p50,
            "frame_size_p95": p95,
            "frame_size_p99": p99,
            "max_frame_size": max(sizes) if sizes else 0,
            "min_frame_size": min(sizes) if sizes else 0,
        }

    def _compute_audio_stats(self):
        af = self.audio_frames
        if not af:
            self.audio_stats = {"total_frames": 0}
            return
        pts = [f["pts_time"] for f in af]
        iv = [pts[i] - pts[i-1] for i in range(1, len(pts))]
        sizes = [f["pkt_size"] for f in af]
        nb_samples = [f.get("nb_samples", 0) for f in af if f.get("nb_samples", 0) > 0]

        # 音频帧间隔异常
        anomaly_iv = [v for v in iv if v > 0.1]  # >100ms
        audio_frame_intervals = [round(v * 1000, 2) for v in iv]

        self.audio_stats = {
            "total_frames": len(af),
            "duration_sec": max(pts) - min(pts) if len(pts) > 1 else 0,
            "avg_pts_interval": sum(iv) / len(iv) if iv else 0,
            "min_pts_interval": min(iv) * 1000 if iv else 0,
            "max_pts_interval": max(iv) * 1000 if iv else 0,
            "total_size_bytes": sum(sizes),
            "avg_frame_size": sum(sizes) / len(sizes) if sizes else 0,
            "anomaly_interval_count": len(anomaly_iv),
            "avg_samples_per_frame": sum(nb_samples) / len(nb_samples) if nb_samples else 0,
            "audio_frame_intervals": audio_frame_intervals[:2000],
        }

    def _compute_packet_bitrate(self):
        """基于 packet 的精确码率分析"""
        if not self.packets:
            self.packet_bitrate = {}
            return

        video_pkts = [p for p in self.packets if p["codec_type"] == "video"]
        audio_pkts = [p for p in self.packets if p["codec_type"] == "audio"]

        def calc_bitrate_windows(pkts, window_sec=1.0):
            if len(pkts) < 2:
                return []
            result = []
            ws, wb = 0, 0
            for i, p in enumerate(pkts):
                dt = p["pts_time"] - pkts[ws]["pts_time"]
                if dt >= window_sec:
                    if dt > 0:
                        mid_t = (pkts[ws]["pts_time"] + p["pts_time"]) / 2
                        result.append({"time": round(mid_t, 2), "kbps": round(wb * 8 / dt / 1000, 1)})
                    ws = i
                    wb = 0
                wb += p["size"]
            return result

        self.packet_bitrate = {
            "video": calc_bitrate_windows(video_pkts),
            "audio": calc_bitrate_windows(audio_pkts),
            "video_avg_kbps": round(sum(p["size"] for p in video_pkts) * 8 / max(p["pts_time"] for p in video_pkts) / 1000, 1) if video_pkts else 0,
            "audio_avg_kbps": round(sum(p["size"] for p in audio_pkts) * 8 / max(p["pts_time"] for p in audio_pkts) / 1000, 1) if audio_pkts else 0,
        }

    def _compute_timestamp_health(self):
        """时间戳健康检测"""
        vf = self.video_frames
        if not vf:
            self.timestamp_health = {}
            return

        issues = []

        # 1. DTS 单调性检查
        dts_vals = [f["dts_time"] for f in vf if f["dts_time"] > 0]
        dts_non_monotonic = 0
        for i in range(1, len(dts_vals)):
            if dts_vals[i] < dts_vals[i-1]:
                dts_non_monotonic += 1
        if dts_non_monotonic > 0:
            issues.append({"level": "error", "msg": f"DTS 非单调递增: {dts_non_monotonic} 处"})

        # 2. PTS 跳变检测
        pts_vals = [f["pts_time"] for f in vf]
        pts_jumps = 0
        expected_interval = self.video_stats.get("avg_pts_interval", 0.033)
        for i in range(1, len(pts_vals)):
            gap = abs(pts_vals[i] - pts_vals[i-1])
            if gap > expected_interval * 10 and gap > 0.1:
                pts_jumps += 1
        if pts_jumps > 0:
            issues.append({"level": "warn", "msg": f"PTS 跳变: {pts_jumps} 处 (间隔 >10x 平均值)"})

        # 3. 负 DTS-PTS 偏移
        neg_offsets = sum(1 for f in vf if f["dts_time"] > 0 and f["pts_time"] < f["dts_time"])
        if neg_offsets > 0:
            issues.append({"level": "warn", "msg": f"PTS < DTS: {neg_offsets} 帧 (通常由 B 帧引起，正常)"})

        # 4. 丢帧检测 (PTS 间隔突然变大)
        interval_anomaly = self.video_stats.get("anomaly_interval_count", 0)
        ratio = self.video_stats.get("interval_anomaly_ratio", 0)
        threshold_ms = self.video_stats.get("anomaly_interval_threshold_ms", 33)
        if ratio > 0.01:
            issues.append({"level": "warn", "msg": f"帧间隔异常 (>{threshold_ms}ms): {interval_anomaly} 帧 ({ratio*100:.2f}%)"})
        elif interval_anomaly > 0:
            issues.append({"level": "info", "msg": f"帧间隔轻微异常 (>{threshold_ms}ms): {interval_anomaly} 帧 ({ratio*100:.3f}%)"})

        # 5. 起始时间偏移
        first_pts = vf[0]["pts_time"]
        if abs(first_pts) > 0.5:
            issues.append({"level": "info", "msg": f"首帧 PTS 偏移: {first_pts:.3f}s"})

        # 6. 音视频同步偏移
        af = self.audio_frames
        if af:
            first_audio_pts = af[0]["pts_time"]
            av_offset = abs(first_pts - first_audio_pts)
            if av_offset > 0.05:
                issues.append({"level": "warn", "msg": f"音视频起始偏移: {av_offset*1000:.1f}ms"})

        # 7. 交织帧检测
        interlaced = self.video_stats.get("interlaced_count", 0)
        if interlaced > 0:
            issues.append({"level": "info", "msg": f"交织帧: {interlaced} 帧 (视频为隔行扫描)"})

        # 综合评分
        error_count = sum(1 for i in issues if i["level"] == "error")
        warn_count = sum(1 for i in issues if i["level"] == "warn")
        if error_count > 0:
            score = "⚠️ 存在问题"
        elif warn_count > 0:
            score = "⚡ 轻微异常"
        else:
            score = "✅ 良好"

        self.timestamp_health = {
            "issues": issues,
            "score": score,
            "dts_non_monotonic": dts_non_monotonic,
            "pts_jumps": pts_jumps,
        }

    def _generate_summary(self):
        """生成综合评估"""
        vs = self.video_stats
        ci = self.container_info
        si = self.stream_info
        th = self.timestamp_health
        pb = getattr(self, 'packet_bitrate', {})

        video_stream = next((s for s in si if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in si if s.get("codec_type") == "audio"), {})
        subtitle_streams = [s for s in si if s.get("codec_type") == "subtitle"]

        items = []
        scores = []

        # 容器
        fmt_name = detect_container_format(ci)
        items.append(f"容器格式: {fmt_name}")
        items.append(f"时长: {ci.get('duration') or vs.get('duration_sec', '0')}s")
        items.append(f"文件大小: {os.path.getsize(self.filepath)} bytes")
        items.append(f"流总数: {len(si)} (视频{sum(1 for s in si if s.get('codec_type')=='video')} / 音频{sum(1 for s in si if s.get('codec_type')=='audio')} / 字幕{len(subtitle_streams)})")

        # 视频
        if video_stream:
            codec = video_stream.get('codec_long_name', video_stream.get('codec_name', '?'))
            res = f"{video_stream.get('width','?')}×{video_stream.get('height','?')}"
            fps = video_stream.get('r_frame_rate', '?')
            profile = video_stream.get('profile', '?')
            items.append(f"视频编码: {codec} ({profile}), {res} @ {fps}")

        # 帧统计
        pict = vs.get("pict_types", {})
        ic = pict.get("I", 0)
        bc = pict.get("B", 0)
        total_vf = vs.get("total_frames", 0)
        if total_vf > 0:
            b_ratio = bc / total_vf * 100
            gop_avg = vs.get("avg_gop_size", 0)
            items.append(f"帧统计: {total_vf:,} 帧 (I:{ic} P:{pict.get('P',0)} B:{bc}), B帧占比 {b_ratio:.1f}%, 平均GOP {gop_avg:.1f}")

        # 码率
        vbr = video_stream.get('bit_rate') or pb.get('video_avg_kbps', 0)
        abr = audio_stream.get('bit_rate') or pb.get('audio_avg_kbps', 0)
        if vbr:
            vbr_mbps = int(vbr) / 1e6 if int(vbr) > 1e6 else int(vbr) / 1e3
            vbr_unit = "Mbps" if int(vbr) > 1e6 else "kbps"
            items.append(f"视频码率: {vbr_mbps:.2f} {vbr_unit}")
        if abr:
            items.append(f"音频码率: {abr:.0f} kbps" if isinstance(abr, (int, float)) and abr < 10000 else f"音频码率: {abr}")

        # 时间戳健康
        ts_score = th.get("score", "未知")
        items.append(f"时间戳健康: {ts_score}")
        for issue in th.get("issues", []):
            items.append(f"  [{issue['level'].upper()}] {issue['msg']}")

        # 帧大小
        if total_vf > 0:
            items.append(f"帧大小: avg {vs.get('avg_frame_size',0):.0f}B, p50 {vs.get('frame_size_p50',0)}B, p95 {vs.get('frame_size_p95',0)}B, p99 {vs.get('frame_size_p99',0)}B")

        # 音频
        if self.audio_stats.get("total_frames", 0) > 0:
            audio_codec = audio_stream.get('codec_long_name', audio_stream.get('codec_name', '?'))
            ch_layout = audio_stream.get('channel_layout', '?')
            sr = audio_stream.get('sample_rate', '?')
            items.append(f"音频: {audio_codec}, {ch_layout}, {sr}Hz, {self.audio_stats['total_frames']:,} 帧")

        # 章节
        if self.chapters:
            items.append(f"章节数: {len(self.chapters)}")

        # 流 disposition
        for s in si:
            disp = s.get("disposition", {})
            active = [k for k, v in disp.items() if v == 1]
            if active:
                items.append(f"流{s.get('index','?')} ({s.get('codec_type','')}): 标记为 {', '.join(active)}")

        # 总评
        error_issues = [i for i in th.get("issues", []) if i["level"] == "error"]
        warn_issues = [i for i in th.get("issues", []) if i["level"] == "warn"]
        if error_issues:
            verdict = "⚠️ 存在明显问题，建议检查时间戳和编码参数"
        elif warn_issues:
            verdict = "⚡ 轻微异常，整体可正常使用"
        elif total_vf > 0:
            verdict = "✅ 视频文件状态良好，各项指标正常"
        else:
            verdict = "ℹ️ 信息不足，无法做出评估"

        self.summary = {
            "items": items,
            "verdict": verdict,
            "ts_score": ts_score,
            "has_errors": len(error_issues) > 0,
            "has_warnings": len(warn_issues) > 0,
        }


# ─────────────────────────────────────────────────────────
# HTML 报告生成
# ─────────────────────────────────────────────────────────

def detect_container_format(ci):
    """智能识别容器格式，优先从 major_brand/format_name 判断，而非用 format_long_name"""
    fmt_name = ci.get('format_name', '')
    tags = ci.get('tags', {})
    brand = tags.get('major_brand', '')

    # major_brand 映射
    brand_map = {
        'mp41': 'MP4', 'mp42': 'MP4', 'isom': 'MP4', 'iso2': 'MP4',
        'isml': 'MP4', 'M4V ': 'M4V', 'M4A ': 'M4A', 'qt  ': 'QuickTime / MOV',
        'mkv ': 'Matroska (MKV)', 'webm': 'WebM', 'FLV ': 'FLV',
        'AVI ': 'AVI', '3gp4': '3GPP', '3gp5': '3GPP', '3gp6': '3GPP',
    }
    if brand and brand in brand_map:
        return brand_map[brand]

    # format_name 包含 mp4 相关
    if 'mp4' in fmt_name or 'm4a' in fmt_name or 'm4v' in fmt_name:
        return 'MP4'

    # format_name 包含多候选（如 "mov,mp4,m4a,3gp,3g2,mj2"），优先取具体项
    name_priority = ['mp4', 'matroska', 'webm', 'avi', 'flv', 'mpegts', 'gif', 'wav', 'flac']
    for candidate in fmt_name.split(','):
        for p in name_priority:
            if p in candidate.lower():
                fmt_map = {
                    'mp4': 'MP4', 'matroska': 'Matroska (MKV)', 'webm': 'WebM',
                    'avi': 'AVI', 'flv': 'FLV', 'mpegts': 'MPEG-TS',
                    'gif': 'GIF', 'wav': 'WAV', 'flac': 'FLAC',
                }
                return fmt_map.get(p, candidate)

    # fallback: 用 format_long_name
    return ci.get('format_long_name', fmt_name or '?')


def generate_html_report(filepath, analyzer):
    """根据分析结果生成完整 HTML 报告"""
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    opts = analyzer.options
    ci = analyzer.container_info
    si = analyzer.stream_info
    vs = analyzer.video_stats
    ch = analyzer.chapters
    th = analyzer.timestamp_health
    pb = getattr(analyzer, 'packet_bitrate', {})
    summ = analyzer.summary

    def fmt_size(b):
        for u in ['B','KB','MB','GB']:
            if b < 1024: return f"{b:.1f} {u}"
            b /= 1024
        return f"{b:.1f} TB"

    def fmt_dur(d):
        if not d: return "-"
        d = float(d)
        h, m = int(d//3600), int((d%3600)//60)
        s = d % 60
        return f"{h}:{m:02d}:{s:06.3f}" if h else f"{m}:{s:06.3f}"

    def fmt_br(br):
        if not br: return "-"
        br = int(br)
        return f"{br/1e6:.2f} Mbps" if br > 1e6 else f"{br/1e3:.0f} kbps" if br > 1e3 else f"{br} bps"

    def parse_fps_value(value):
        if value in (None, "", "0/0"):
            return 0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if "/" in text:
            try:
                num, den = text.split("/", 1)
                num = float(num)
                den = float(den)
                return (num / den) if den else 0
            except (TypeError, ValueError, ZeroDivisionError):
                return 0
        try:
            return float(text)
        except ValueError:
            return 0

    def fmt_fps(value):
        fps = parse_fps_value(value)
        if fps <= 0:
            return "-"
        return f"{fps:.3f}".rstrip("0").rstrip(".") + " fps"

    # ── 收集 HTML 片段 ──
    sections = []

    # ── 概览卡片 ──
    vf = analyzer.video_frames
    video_stream = next((s for s in si if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in si if s.get("codec_type") == "audio"), {})

    pict = vs.get("pict_types", {})
    ic, pc, bc = pict.get("I",0), pict.get("P",0), pict.get("B",0)
    total_vf = vs.get("total_frames", 1)

    # 容器格式兜底：如果 ci 为空，从文件扩展名推断
    fmt_display = detect_container_format(ci)
    if not fmt_display:
        ext = os.path.splitext(filename)[1].lower().lstrip('.')
        ext_map = {
            'mp4': 'MPEG-4 Part 14', 'mkv': 'Matroska / WebM', 'avi': 'AVI (Audio Video Interleaved)',
            'mov': 'QuickTime / MOV', 'flv': 'FLV (Flash Video)', 'webm': 'WebM',
            'ts': 'MPEG-TS', 'm4v': 'MPEG-4 Part 14', 'wmv': 'ASF (Advanced / WMA / WMV)',
            'mpg': 'MPEG-PS (MPEG-2 Program Stream)', 'mpeg': 'MPEG-PS (MPEG-2 Program Stream)',
            '3gp': '3GP (3GPP Multimedia)', 'rmvb': 'RealMedia Variable Bitrate',
        }
        fmt_display = ext_map.get(ext, ext.upper() if ext else '未知')

    ci_dur = ci.get('duration', '')
    if not ci_dur and vs.get('duration_sec', 0) > 0:
        ci_dur = str(vs['duration_sec'])
    avg_fps = (
        vs.get('avg_fps')
        or parse_fps_value(video_stream.get('avg_frame_rate'))
        or parse_fps_value(video_stream.get('r_frame_rate'))
    )
    anomaly_threshold_ms = vs.get('anomaly_interval_threshold_ms', 33)

    sections.append(f"""
    <div class="cards">
      <div class="card accent"><div class="cl">容器格式</div><div class="cv">{fmt_display}</div></div>
      <div class="card"><div class="cl">总时长</div><div class="cv c">{fmt_dur(ci_dur)}</div></div>
      <div class="card"><div class="cl">文件大小</div><div class="cv p">{fmt_size(filesize)}</div></div>
      <div class="card"><div class="cl">总码率</div><div class="cv y">{fmt_br(ci.get('bit_rate'))}</div></div>
      <div class="card"><div class="cl">平均帧率</div><div class="cv c">{fmt_fps(avg_fps)}</div></div>
      <div class="card"><div class="cl">视频帧数</div><div class="cv r">{total_vf:,}</div></div>
      <div class="card"><div class="cl">音频帧数</div><div class="cv g">{analyzer.audio_stats.get('total_frames',0):,}</div></div>
      <div class="card"><div class="cl">流总数</div><div class="cv b">{len(si)}</div></div>
      <div class="card"><div class="cl">章节数</div><div class="cv o">{len(ch)}</div></div>
    </div>""")

    # ── 视频编码信息 ──
    if opts.get('streams', True) and video_stream:
        sections.append(f"""
    <div class="sec"><h2>🎬 视频编码信息</h2>
      <div class="info-grid">
        <div><span class="il">编码器</span><span class="iv">{video_stream.get('codec_long_name','')}</span></div>
        <div><span class="il">Profile</span><span class="iv">{video_stream.get('profile','')} / Level {video_stream.get('level','')}</span></div>
        <div><span class="il">分辨率</span><span class="iv">{video_stream.get('width','')}×{video_stream.get('height','')}</span></div>
        <div><span class="il">帧率</span><span class="iv">{video_stream.get('r_frame_rate','')}</span></div>
        <div><span class="il">像素格式</span><span class="iv">{video_stream.get('pix_fmt','')}</span></div>
        <div><span class="il">色彩空间</span><span class="iv">{video_stream.get('color_space','')} / {video_stream.get('color_transfer','')}</span></div>
        <div><span class="il">色度位置</span><span class="iv">{video_stream.get('chroma_location','')}</span></div>
        <div><span class="il">码率</span><span class="iv">{fmt_br(video_stream.get('bit_rate'))}</span></div>
        <div><span class="il">采样宽高比</span><span class="iv">{video_stream.get('sample_aspect_ratio','')} → {video_stream.get('display_aspect_ratio','')}</span></div>
        <div><span class="il">参考帧数</span><span class="iv">{video_stream.get('refs','')}</span></div>
        <div><span class="il">has_b_frames</span><span class="iv">{video_stream.get('has_b_frames','')}</span></div>
        <div><span class="il">field_order</span><span class="iv">{video_stream.get('field_order','')}</span></div>
        <div><span class="il">closed_gops</span><span class="iv">{video_stream.get('closed_gops','')}</span></div>
        <div><span class="il">coded_width</span><span class="iv">{video_stream.get('coded_width','')}</span></div>
        <div><span class="il">coded_height</span><span class="iv">{video_stream.get('coded_height','')}</span></div>
      </div></div>""")

    # ── 音频编码信息 ──
    if opts.get('streams', True) and audio_stream:
        sections.append(f"""
    <div class="sec"><h2>🔊 音频编码信息</h2>
      <div class="info-grid">
        <div><span class="il">编码器</span><span class="iv">{audio_stream.get('codec_long_name','')}</span></div>
        <div><span class="il">采样率</span><span class="iv">{audio_stream.get('sample_rate','')} Hz</span></div>
        <div><span class="il">声道布局</span><span class="iv">{audio_stream.get('channel_layout','')} ({audio_stream.get('channels','')}ch)</span></div>
        <div><span class="il">采样格式</span><span class="iv">{audio_stream.get('sample_fmt','')}</span></div>
        <div><span class="il">码率</span><span class="iv">{fmt_br(audio_stream.get('bit_rate'))}</span></div>
        <div><span class="il">帧大小</span><span class="iv">{audio_stream.get('frame_size','')}</span></div>
        <div><span class="il">Profile</span><span class="iv">{audio_stream.get('profile','')}</span></div>
      </div></div>""")

    # ── PTS/DTS 统计 ──
    if opts.get('frames_pts', True) and vs:
        sections.append(f"""
    <div class="sec"><h2>⏱ PTS / DTS 时序统计</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">平均 PTS 间隔</div><div class="cv g">{vs.get('avg_pts_interval',0)*1000:.3f}ms</div>
          <div class="ch">min {vs.get('min_pts_interval',0)*1000:.3f}ms · max {vs.get('max_pts_interval',0)*1000:.3f}ms · σ {vs.get('std_pts_interval',0)*1000:.3f}ms</div></div>
        <div class="card"><div class="cl">平均 DTS 间隔</div><div class="cv y">{vs.get('avg_dts_interval',0)*1000:.3f}ms</div></div>
        <div class="card"><div class="cl">DTS-PTS 偏移</div><div class="cv p">{vs.get('avg_dts_pts_offset',0)*1000:.2f}ms</div>
          <div class="ch">范围 [{vs.get('min_dts_pts_offset',0)*1000:.1f}, {vs.get('max_dts_pts_offset',0)*1000:.1f}]ms</div></div>
        <div class="card"><div class="cl">平均帧时长</div><div class="cv c">{vs.get('avg_duration',0)*1000:.3f}ms</div></div>
        <div class="card"><div class="cl">帧间隔异常 (&gt;{anomaly_threshold_ms}ms)</div><div class="cv" style="color:#f87171">{vs.get('anomaly_interval_count',0):,}帧</div>
          <div class="ch">占比 {vs.get('interval_anomaly_ratio',0)*100:.2f}%</div></div>
        <div class="card"><div class="cl">交织帧</div><div class="cv o">{vs.get('interlaced_count',0):,}</div></div>
      </div>
      <div class="chart-box"><canvas id="chartPtsDts"></canvas></div>
      <div class="chart-box"><canvas id="chartPtsIv"></canvas></div>
    </div>""")

    # ── 帧类型 ──
    if opts.get('frames_type', True) and vs:
        sections.append(f"""
    <div class="sec"><h2>🎞 帧类型分布</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">I 帧 (关键帧)</div><div class="cv r">{ic:,}</div><div class="ch">{ic/total_vf*100:.1f}%</div></div>
        <div class="card"><div class="cl">P 帧</div><div class="cv y">{pc:,}</div><div class="ch">{pc/total_vf*100:.1f}%</div></div>
        <div class="card"><div class="cl">B 帧</div><div class="cv g">{bc:,}</div><div class="ch">{bc/total_vf*100:.1f}%</div></div>
        <div class="card"><div class="cl">帧大小 P50/P95/P99</div><div class="cv" style="font-size:14px">{vs.get('frame_size_p50',0)}/{vs.get('frame_size_p95',0)}/{vs.get('frame_size_p99',0)}</div>
          <div class="ch">max {vs.get('max_frame_size',0):,}B · min {vs.get('min_frame_size',0)}B</div></div>
      </div>
      <div class="chart-row">
        <div class="chart-box half"><canvas id="chartPictPie"></canvas></div>
        <div class="chart-box half"><canvas id="chartFrameType"></canvas></div>
      </div>
    </div>""")

    # ── GOP ──
    if opts.get('gop', True) and vs.get('gop_sizes'):
        gop_json = json.dumps(vs['gop_sizes'][:500])
        sections.append(f"""
    <div class="sec"><h2>📐 GOP 结构分析</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">平均 GOP</div><div class="cv c">{vs.get('avg_gop_size',0):.1f} 帧</div></div>
        <div class="card"><div class="cl">GOP 范围</div><div class="cv">{vs.get('min_gop_size',0)} — {vs.get('max_gop_size',0)}</div></div>
        <div class="card"><div class="cl">GOP 总数</div><div class="cv p">{len(vs['gop_sizes'])}</div></div>
      </div>
      <div class="chart-box"><canvas id="chartGop"></canvas></div>
    </div>""")

    # ── Packet 码率 (精确) ──
    if opts.get('packets', True) and pb.get('video'):
        pb_v_json = json.dumps(pb['video'], separators=(',',':'))
        pb_a_json = json.dumps(pb.get('audio', []), separators=(',',':'))
        sections.append(f"""
    <div class="sec"><h2>📈 Packet 码率分析 (精确)</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">视频平均码率</div><div class="cv r">{pb.get('video_avg_kbps',0):.0f} kbps</div></div>
        <div class="card"><div class="cl">音频平均码率</div><div class="cv g">{pb.get('audio_avg_kbps',0):.0f} kbps</div></div>
      </div>
      <div class="chart-box"><canvas id="chartPacketBitrate"></canvas></div>
    </div>""")

    # ── 帧级码率 ──
    if opts.get('bitrate', True) and vs.get('bitrate_windows'):
        br_json = json.dumps(vs['bitrate_windows'], separators=(',',':'))
        sections.append(f"""
    <div class="sec"><h2>📉 帧级码率曲线</h2>
      <div class="chart-box"><canvas id="chartBitrate"></canvas></div>
      <div class="chart-box"><canvas id="chartFrameSize"></canvas></div>
    </div>""")

    # ── 帧间隔直方图 ──
    if opts.get('frames_pts', True) and vs.get('interval_histogram'):
        hist_json_data = json.dumps(vs['interval_histogram'])
        sections.append(f"""
    <div class="sec"><h2>📊 PTS 间隔分布</h2>
      <div class="chart-box"><canvas id="chartHist"></canvas></div>
    </div>""")

    # ── 时间戳健康 ──
    if opts.get('timestamps', True) and th:
        issue_rows = ""
        level_cls = {"error": "ts-err", "warn": "ts-warn", "info": "ts-info"}
        level_icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}
        for iss in th.get("issues", []):
            cls = level_cls.get(iss["level"], "ts-info")
            icon = level_icon.get(iss["level"], "🔵")
            issue_rows += f'<div class="ts-item {cls}">{icon} {iss["msg"]}</div>'
        if not issue_rows:
            issue_rows = '<div class="ts-item ts-ok">✅ 未检测到时间戳异常</div>'

        sections.append(f"""
    <div class="sec"><h2>🏥 时间戳健康检测</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">综合评估</div><div class="cv" style="font-size:18px">{th.get('score','未知')}</div></div>
        <div class="card"><div class="cl">DTS 非单调</div><div class="cv r">{th.get('dts_non_monotonic',0)}</div></div>
        <div class="card"><div class="cl">PTS 跳变</div><div class="cv y">{th.get('pts_jumps',0)}</div></div>
      </div>
      <div class="ts-list">{issue_rows}</div>
    </div>""")

    # ── 音频帧分析 ──
    if opts.get('timestamps', True) and analyzer.audio_stats.get("total_frames", 0) > 0:
        ast = analyzer.audio_stats
        sections.append(f"""
    <div class="sec"><h2>🔊 音频帧分析</h2>
      <div class="cards tight">
        <div class="card"><div class="cl">总帧数</div><div class="cv g">{ast['total_frames']:,}</div></div>
        <div class="card"><div class="cl">平均间隔</div><div class="cv c">{ast['avg_pts_interval']*1000:.2f}ms</div></div>
        <div class="card"><div class="cl">间隔范围</div><div class="cv">{ast['min_pts_interval']:.2f} — {ast['max_pts_interval']:.2f}ms</div></div>
        <div class="card"><div class="cl">异常间隔 (&gt;100ms)</div><div class="cv r">{ast['anomaly_interval_count']}</div></div>
        <div class="card"><div class="cl">平均采样/帧</div><div class="cv y">{ast['avg_samples_per_frame']:.0f}</div></div>
        <div class="card"><div class="cl">平均帧大小</div><div class="cv p">{ast['avg_frame_size']:.0f}B</div></div>
      </div>""")

    # ── 帧数据表 ──
    if opts.get('frame_table', True) and vf:
        # 采样
        MAX = 50000
        if len(vf) > MAX:
            step = len(vf) // MAX
            sampled = vf[::step]
        else:
            sampled = vf

        sections.append(f"""
    <div class="sec"><h2>📋 帧级数据表</h2>
      <p class="desc">共 {len(vf):,} 帧 · 支持搜索、筛选、排序 · <span style="color:#f87171">⚠ 红色标记为间隔 &gt;{anomaly_threshold_ms}ms 的异常帧</span></p>
      <div class="search-row">
        <input type="text" id="frameSearch" placeholder="搜索帧号、时间、类型...">
        <select id="frameFilter"><option value="">全部</option><option value="I">I帧</option><option value="P">P帧</option><option value="B">B帧</option><option value="kf">关键帧</option><option value="anomaly">间隔异常(&gt;{anomaly_threshold_ms}ms)</option></select>
      </div>
      <div class="tbl-wrap"><table id="frameTable"><thead><tr>
        <th data-col="0">#</th><th data-col="1">PTS</th><th data-col="2">PTS时间(s)</th>
        <th data-col="3">DTS</th><th data-col="4">DTS时间(s)</th><th data-col="5">DTS-PTS(ms)</th>
        <th data-col="6">类型</th><th data-col="7">关键帧</th><th data-col="8">时长(ms)</th><th data-col="9">大小(B)</th>
        <th data-col="10">间隔(ms)</th>
      </tr></thead><tbody id="frameBody"></tbody></table></div>
    </div>""")

    # ── 流信息表 ──
    if opts.get('streams', True) and si:
        rows = ""
        for s in si:
            st = s.get("codec_type","-")
            disp = s.get("disposition", {})
            flags = ", ".join(k for k, v in disp.items() if v == 1) if disp else "-"
            lang = s.get("tags", {}).get("language", "") if s.get("tags") else ""
            rows += f"""<tr><td>{s.get('index','')}</td><td>{st}</td>
              <td><code>{s.get('codec_name','')}</code></td><td>{s.get('codec_long_name','')}</td>
              <td>{s.get('profile','')}</td>
              <td>{s.get('width','')}×{s.get('height','') if st=='video' else ''}</td>
              <td>{s.get('r_frame_rate','') if st=='video' else s.get('sample_rate','')}</td>
              <td>{fmt_br(s.get('bit_rate'))}</td><td>{lang}</td><td>{flags}</td></tr>"""
        sections.append(f"""
    <div class="sec"><h2>🔧 流信息详情</h2>
      <div class="tbl-wrap"><table><thead><tr>
        <th>索引</th><th>类型</th><th>编码</th><th>完整名称</th><th>Profile</th><th>分辨率/采样率</th><th>帧率</th><th>码率</th><th>语言</th><th>属性</th>
      </tr></thead><tbody>{rows}</tbody></table></div>
    </div>""")

    # ── 容器标签 ──
    if opts.get('container', True) and ci.get('tags'):
        tag_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k,v in ci['tags'].items())
        sections.append(f"""
    <div class="sec"><h2>🏷 容器标签</h2>
      <div class="tbl-wrap"><table><thead><tr><th>标签</th><th>值</th></tr></thead><tbody>{tag_rows}</tbody></table></div>
    </div>""")

    # ── 章节 ──
    if opts.get('chapters', True) and ch:
        ch_rows = ""
        for c in ch:
            title = c.get("tags",{}).get("title","未命名") if c.get("tags") else "未命名"
            ch_rows += f"<tr><td>{c.get('id','')}</td><td>{fmt_dur(c.get('start_time',0))}</td><td>{fmt_dur(c.get('end_time',0))}</td><td>{title}</td></tr>"
        sections.append(f"""
    <div class="sec"><h2>📑 章节</h2>
      <div class="tbl-wrap"><table><thead><tr><th>ID</th><th>开始</th><th>结束</th><th>标题</th></tr></thead><tbody>{ch_rows}</tbody></table></div>
    </div>""")

    # ── 综合总结 ──
    if summ:
        summary_items = "".join(f"<div class='sum-item'>• {item}</div>" for item in summ.get("items", []))
        verdict = summ.get("verdict", "")
        verdict_cls = "verdict-ok" if "良好" in verdict else "verdict-warn" if "轻微" in verdict else "verdict-err" if "问题" in verdict else "verdict-info"
        sections.append(f"""
    <div class="sec summary-sec"><h2>📝 综合评估总结</h2>
      <div class="verdict-box {verdict_cls}"><span class="verdict-icon">{verdict[:2]}</span><span class="verdict-text">{verdict[2:].strip()}</span></div>
      <div class="summary-list">{summary_items}</div>
    </div>""")

    body_html = "\n".join(sections)

    # ── 构建 JS 数据 ──
    js_data_parts = []
    if opts.get('frames_pts', True) or opts.get('frames_type', True) or opts.get('frame_table', True):
        MAX_JS = 50000
        sampled_js = vf[::max(1, len(vf)//MAX_JS)] if len(vf) > MAX_JS else vf
        js_data_parts.append(f"const VF={json.dumps([{'i':i,'pts_t':round(f['pts_time'],6),'dts_t':round(f['dts_time'],6),'dur':round(f['duration_time'],6),'type':f['pict_type'],'kf':f['key_frame'],'size':f['pkt_size'],'dts_pts':round(f['pts_time']-f['dts_time'],6),'interval':f['interval_ms']} for i,f in enumerate(sampled_js)],separators=(',',':'))};")
    else:
        js_data_parts.append("const VF=[];")

    js_data_parts.append(f"const TOTAL_VF={total_vf};")
    js_data_parts.append(f"const IC={ic};const PC={pc};const BC={bc};")
    js_data_parts.append(f"const ANOMALY_THRESHOLD_MS={anomaly_threshold_ms};")

    if vs.get('interval_histogram'):
        js_data_parts.append(f"const HIST={json.dumps(vs['interval_histogram'])};")
    else:
        js_data_parts.append("const HIST={};")

    if vs.get('gop_sizes'):
        js_data_parts.append(f"const GOP={json.dumps(vs['gop_sizes'][:500])};")
    else:
        js_data_parts.append("const GOP=[];")

    if vs.get('bitrate_windows'):
        js_data_parts.append(f"const BR={json.dumps(vs['bitrate_windows'],separators=(',',':'))};")
    else:
        js_data_parts.append("const BR=[];")

    # Packet bitrate data
    if pb.get('video'):
        js_data_parts.append(f"const PB_V={json.dumps(pb['video'],separators=(',',':'))};")
    else:
        js_data_parts.append("const PB_V=[];")
    if pb.get('audio'):
        js_data_parts.append(f"const PB_A={json.dumps(pb['audio'],separators=(',',':'))};")
    else:
        js_data_parts.append("const PB_A=[];")

    js_data = "\n".join(js_data_parts)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>视频帧分析 — {filename}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0a0c10;--card:#12151d;--card2:#181c28;--border:#1e2233;--text:#e0e2ea;--muted:#6b7088;
--accent:#5b7fff;--green:#2dd4a0;--red:#f87171;--yellow:#fbbf24;--purple:#a78bfa;--blue:#60a5fa;--cyan:#22d3ee;--orange:#fb923c}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}
.hdr{{background:linear-gradient(135deg,#151822,#0c0e14);border-bottom:1px solid var(--border);padding:20px 28px}}
.hdr h1{{font-size:20px;font-weight:700}}.hdr .sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.wrap{{max-width:1400px;margin:0 auto;padding:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:16px}}
.cards.tight{{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}}
.card.accent{{border-color:var(--accent)}}
.cl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}}
.cv{{font-size:22px;font-weight:700}}.cv.r{{color:var(--red)}}.cv.g{{color:var(--green)}}.cv.y{{color:var(--yellow)}}
.cv.p{{color:var(--purple)}}.cv.c{{color:var(--cyan)}}.cv.b{{color:var(--blue)}}.cv.o{{color:var(--orange)}}
.ch{{font-size:10px;color:var(--muted);margin-top:2px}}
.sec{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px}}
.sec h2{{font-size:15px;font-weight:600;margin-bottom:4px}}.desc{{font-size:12px;color:var(--muted);margin-bottom:12px}}
.chart-box{{position:relative;height:300px;margin-bottom:8px}}.chart-box.half{{height:260px}}
.chart-row{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.info-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px;margin:12px 0}}
.info-grid div{{display:flex;gap:8px;font-size:13px}}.il{{color:var(--muted);min-width:80px}}.iv{{color:var(--text)}}
.tbl-wrap{{overflow-x:auto;max-height:500px;overflow-y:auto;border-radius:8px;border:1px solid var(--border)}}
table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}
thead{{position:sticky;top:0;z-index:2}}
th{{background:var(--card2);color:var(--muted);font-weight:600;padding:7px 10px;text-align:left;border-bottom:2px solid var(--border);cursor:pointer}}
th:hover{{color:var(--accent)}}td{{padding:5px 10px;border-bottom:1px solid var(--border)}}
tr:hover td{{background:rgba(91,127,255,.05)}}
.type-I{{color:var(--red);font-weight:700}}.type-P{{color:var(--yellow);font-weight:600}}.type-B{{color:var(--green);font-weight:600}}
.kf{{background:rgba(248,113,113,.08)}}
.interval-anomaly{{background:rgba(248,113,113,.12)}}
.interval-anomaly td{{border-bottom-color:rgba(248,113,113,.3)}}
.search-row{{display:flex;gap:8px;margin-bottom:10px}}
.search-row input{{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:6px 12px;color:var(--text);font-size:12px;width:260px;outline:none}}
.search-row select{{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:12px;outline:none}}
/* Timestamp health */
.ts-list{{margin-top:12px}}
.ts-item{{padding:8px 12px;border-radius:6px;margin-bottom:4px;font-size:13px}}
.ts-err{{background:rgba(248,113,113,.12);border-left:3px solid var(--red)}}
.ts-warn{{background:rgba(251,191,36,.1);border-left:3px solid var(--yellow)}}
.ts-info{{background:rgba(96,165,250,.08);border-left:3px solid var(--blue)}}
.ts-ok{{background:rgba(45,212,160,.08);border-left:3px solid var(--green)}}
/* Summary */
.summary-sec{{border:1px solid var(--accent);background:linear-gradient(135deg,#12151d,#141825)}}
.verdict-box{{display:flex;align-items:center;gap:10px;padding:14px 18px;border-radius:10px;margin-bottom:14px;font-size:15px;font-weight:600}}
.verdict-ok{{background:rgba(45,212,160,.1);border:1px solid var(--green)}}
.verdict-warn{{background:rgba(251,191,36,.08);border:1px solid var(--yellow)}}
.verdict-err{{background:rgba(248,113,113,.1);border:1px solid var(--red)}}
.verdict-info{{background:rgba(96,165,250,.08);border:1px solid var(--blue)}}
.verdict-icon{{font-size:24px}}
.verdict-text{{color:var(--text)}}
.summary-list{{display:grid;gap:4px}}
.sum-item{{font-size:12px;color:var(--text);padding:3px 0;border-bottom:1px solid rgba(255,255,255,.03)}}
.footer{{text-align:center;font-size:10px;color:var(--muted);padding:16px;opacity:.5}}
@media(max-width:768px){{.cards{{grid-template-columns:repeat(2,1fr)}}.chart-row{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="hdr"><h1>🎬 视频帧级分析报告</h1>
<div class="sub">{filename} · {fmt_size(filesize)} · {fmt_dur(ci_dur)} · 生成于 {now_str}</div></div>
<div class="wrap">{body_html}</div>
<div class="footer">Video Frame Analyzer · ffprobe 驱动 · 自动生成</div>
<script>
{js_data}

Chart.defaults.color='#6b7088';Chart.defaults.borderColor='#1e2233';Chart.defaults.font.size=11;
const o={{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
scales:{{x:{{grid:{{color:'#ffffff05'}},ticks:{{maxTicksLimit:18}}}},y:{{grid:{{color:'#ffffff08'}}}}}},
elements:{{point:{{radius:0}},line:{{borderWidth:1.5}}}}}};

// PTS vs DTS
if(VF.length>0 && document.getElementById('chartPtsDts')){{
  new Chart(document.getElementById('chartPtsDts'),{{type:'line',data:{{labels:VF.map((_,i)=>i),datasets:[
    {{label:'PTS',data:VF.map(f=>f.pts_t),borderColor:'#f87171',fill:false}},
    {{label:'DTS',data:VF.map(f=>f.dts_t),borderColor:'#60a5fa',fill:false}}]}},
    options:{{...o,plugins:{{legend:{{display:true,position:'top',labels:{{usePointStyle:true,padding:10}}}}}},
      scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'时间(秒)'}}}}}}}}}});
}}

// PTS interval
if(VF.length>1 && document.getElementById('chartPtsIv')){{
  const iv=[];for(let i=1;i<VF.length;i++)iv.push((VF[i].pts_t-VF[i-1].pts_t)*1000);
  new Chart(document.getElementById('chartPtsIv'),{{type:'line',data:{{labels:VF.slice(1).map((_,i)=>i+1),datasets:[
    {{label:'PTS间隔(ms)',data:iv,borderColor:'#2dd4a0',backgroundColor:'rgba(45,212,160,0.08)',fill:true}}]}},
    options:{{...o,scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'ms'}}}}}}}}}});
}}

// Pie
if(document.getElementById('chartPictPie')){{
  new Chart(document.getElementById('chartPictPie'),{{type:'doughnut',
    data:{{labels:['I帧','P帧','B帧'],datasets:[{{data:[IC,PC,BC],backgroundColor:['#f87171','#fbbf24','#2dd4a0'],borderWidth:0}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'bottom',labels:{{padding:12,usePointStyle:true}}}}}}}}}});
}}

// Frame type timeline
if(VF.length>0 && document.getElementById('chartFrameType')){{
  const tc={{'I':'#f87171','P':'#fbbf24','B':'#2dd4a0'}},tn={{'I':3,'P':2,'B':1}};
  new Chart(document.getElementById('chartFrameType'),{{type:'bar',
    data:{{labels:VF.map((_,i)=>i),datasets:[{{data:VF.map(f=>tn[f.type]||0),backgroundColor:VF.map(f=>tc[f.type]||'#555'),borderWidth:0,barPercentage:1,borderRadius:0}}]}},
    options:{{...o,scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{ticks:{{callback:v=>({{3:'I',2:'P',1:'B'}})[v]||''}},min:0,max:4}}}},
      plugins:{{tooltip:{{callbacks:{{label:ctx=>VF[ctx.dataIndex]?.type}}}}}}}}}});
}}

// GOP
if(GOP.length>0 && document.getElementById('chartGop')){{
  new Chart(document.getElementById('chartGop'),{{type:'bar',
    data:{{labels:GOP.map((_,i)=>'GOP'+(i+1)),datasets:[{{data:GOP,backgroundColor:GOP.map(v=>v>30?'#f87171':v>20?'#fbbf24':'#2dd4a0'),borderRadius:2}}]}},
    options:{{...o,indexAxis:'y',scales:{{x:{{title:{{display:true,text:'帧数'}}}},y:{{ticks:{{maxTicksLimit:25}}}}}}}}}});
}}

// Packet Bitrate
if((PB_V.length>0||PB_A.length>0) && document.getElementById('chartPacketBitrate')){{
  const datasets=[];
  if(PB_V.length>0)datasets.push({{label:'视频 kbps',data:PB_V.map(b=>b.kbps),borderColor:'#f87171',backgroundColor:'rgba(248,113,113,0.08)',fill:true,tension:.3}});
  if(PB_A.length>0)datasets.push({{label:'音频 kbps',data:PB_A.map(b=>b.kbps),borderColor:'#2dd4a0',backgroundColor:'rgba(45,212,160,0.08)',fill:true,tension:.3}});
  const labels=(PB_V.length>PB_A.length?PB_V:PB_A).map(b=>b.time.toFixed(1)+'s');
  new Chart(document.getElementById('chartPacketBitrate'),{{type:'line',
    data:{{labels,datasets}},
    options:{{...o,plugins:{{legend:{{display:true,position:'top',labels:{{usePointStyle:true,padding:10}}}}}},
      scales:{{x:{{title:{{display:true,text:'时间(s)'}}}},y:{{title:{{display:true,text:'kbps'}}}}}}}}}});
}}

// Frame Bitrate
if(BR.length>0 && document.getElementById('chartBitrate')){{
  new Chart(document.getElementById('chartBitrate'),{{type:'line',
    data:{{labels:BR.map(b=>b.time.toFixed(1)+'s'),datasets:[{{label:'kbps',data:BR.map(b=>b.kbps),borderColor:'#22d3ee',backgroundColor:'rgba(34,211,238,0.1)',fill:true,tension:.3}}]}},
    options:{{...o,scales:{{x:{{title:{{display:true,text:'时间(s)'}}}},y:{{title:{{display:true,text:'kbps'}}}}}}}}}});
}}

// Frame size
if(VF.length>0 && document.getElementById('chartFrameSize')){{
  new Chart(document.getElementById('chartFrameSize'),{{type:'line',
    data:{{labels:VF.map((_,i)=>i),datasets:[{{label:'字节',data:VF.map(f=>f.size),borderColor:'#fb923c',backgroundColor:'rgba(251,146,60,0.08)',fill:true}}]}},
    options:{{...o,scales:{{x:{{title:{{display:true,text:'帧序号'}}}},y:{{title:{{display:true,text:'字节'}}}}}}}}}});
}}

// Histogram
if(Object.keys(HIST).length>0 && document.getElementById('chartHist')){{
  const kl=Object.keys(HIST).map(Number).sort((a,b)=>a-b);
  new Chart(document.getElementById('chartHist'),{{type:'bar',
    data:{{labels:kl.map(k=>k+'ms'),datasets:[{{data:kl.map(k=>HIST[k]),backgroundColor:'#5b7fff',borderRadius:2}}]}},
    options:{{...o,plugins:{{tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y.toLocaleString()+' 帧'}}}}}}}}}});
}}

// Frame table
if(VF.length>0){{
  const tbody=document.getElementById('frameBody');
  const allR=VF.map((f,i)=>({{html:`<tr class="${{f.kf?'kf':''}}${{f.interval!==null&&f.interval>ANOMALY_THRESHOLD_MS?' interval-anomaly':''}}"><td>${{i}}</td><td>${{(f.pts_t*90000).toFixed(0)}}</td><td>${{f.pts_t.toFixed(6)}}</td><td>${{(f.dts_t*90000).toFixed(0)}}</td><td>${{f.dts_t.toFixed(6)}}</td><td>${{(f.dts_pts*1000).toFixed(2)}}</td><td class="type-${{f.type}}">${{f.type}}</td><td>${{f.kf?'✅':'-'}}</td><td>${{(f.dur*1000).toFixed(2)}}</td><td>${{f.size.toLocaleString()}}</td><td${{f.interval!==null&&f.interval>ANOMALY_THRESHOLD_MS?' style="color:#f87171;font-weight:bold"':''}}>${{f.interval!==null?f.interval.toFixed(3):'-'}}</td></tr>`,type:f.type,kf:f.kf,isAnomaly:f.interval!==null&&f.interval>ANOMALY_THRESHOLD_MS}}));
  let filtered=[...allR],shown=Math.min(200,filtered.length);
  function render(){{tbody.innerHTML=filtered.slice(0,shown).map(r=>r.html).join('');}}
  render();
  document.querySelector('.tbl-wrap')?.addEventListener('scroll',function(){{if(this.scrollTop+this.clientHeight>=this.scrollHeight-50&&shown<filtered.length){{shown=Math.min(shown+200,filtered.length);render();}}}});
  document.getElementById('frameSearch')?.addEventListener('input',function(){{const q=this.value.toLowerCase();filtered=allR.filter((r,i)=>!q||String(i).includes(q)||VF[i].type.toLowerCase().includes(q)||VF[i].pts_t.toFixed(6).includes(q));shown=Math.min(200,filtered.length);render();}});
  document.getElementById('frameFilter')?.addEventListener('change',function(){{const v=this.value;filtered=v==='kf'?allR.filter(r=>r.kf):v==='anomaly'?allR.filter(r=>r.isAnomaly):v?allR.filter(r=>r.type===v):[...allR];shown=Math.min(200,filtered.length);render();}});
}}
</script></body></html>"""
    return html


# ─────────────────────────────────────────────────────────
# 主窗口 GUI
# ─────────────────────────────────────────────────────────

# 颜色主题
COLORS = {
    'bg': '#0f1118',
    'card': '#171a24',
    'card_hover': '#1e2230',
    'border': '#252a3a',
    'text': '#e0e2ea',
    'muted': '#6b7088',
    'accent': '#5b7fff',
    'accent_hover': '#7094ff',
    'green': '#2dd4a0',
    'red': '#f87171',
    'yellow': '#fbbf24',
    'entry_bg': '#1a1e2a',
    'button_bg': '#5b7fff',
    'button_fg': '#ffffff',
    'progress': '#5b7fff',
}


class VideoAnalyzerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Video Frame Analyzer  视频帧分析器")
        self.root.geometry("780x920")
        self.root.minsize(700, 750)
        self.root.configure(bg=COLORS['bg'])

        try:
            self.root.iconbitmap(default='')
        except:
            pass

        self.filepath = tk.StringVar()
        self.output_path = tk.StringVar()
        self.status_text = tk.StringVar(value="就绪")
        self.progress_var = tk.DoubleVar(value=0)

        # 分析选项
        self.opt_vars = {
            'container': tk.BooleanVar(value=True),
            'streams': tk.BooleanVar(value=True),
            'frames_pts': tk.BooleanVar(value=True),
            'frames_type': tk.BooleanVar(value=True),
            'gop': tk.BooleanVar(value=True),
            'bitrate': tk.BooleanVar(value=True),
            'packets': tk.BooleanVar(value=True),
            'timestamps': tk.BooleanVar(value=True),
            'frame_table': tk.BooleanVar(value=True),
            'chapters': tk.BooleanVar(value=True),
        }

        self._build_ui()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        # 配置暗色样式
        style.configure('Dark.TFrame', background=COLORS['bg'])
        style.configure('Card.TFrame', background=COLORS['card'])
        style.configure('Dark.TLabel', background=COLORS['bg'], foreground=COLORS['text'], font=('Segoe UI', 10))
        style.configure('Title.TLabel', background=COLORS['bg'], foreground=COLORS['text'], font=('Segoe UI', 16, 'bold'))
        style.configure('Sub.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=('Segoe UI', 9))
        style.configure('Card.TLabel', background=COLORS['card'], foreground=COLORS['text'], font=('Segoe UI', 10))
        style.configure('Muted.TLabel', background=COLORS['card'], foreground=COLORS['muted'], font=('Segoe UI', 9))
        style.configure('Accent.TLabel', background=COLORS['bg'], foreground=COLORS['accent'], font=('Segoe UI', 10))
        style.configure('Status.TLabel', background=COLORS['bg'], foreground=COLORS['muted'], font=('Segoe UI', 9))

        style.configure('Dark.TCheckbutton', background=COLORS['card'], foreground=COLORS['text'], font=('Segoe UI', 10))
        style.map('Dark.TCheckbutton', background=[('active', COLORS['card'])])

        style.configure('Accent.TButton', background=COLORS['button_bg'], foreground=COLORS['button_fg'],
                        font=('Segoe UI', 10, 'bold'), padding=(20, 8))
        style.map('Accent.TButton', background=[('active', COLORS['accent_hover'])])

        style.configure('Sec.TButton', background=COLORS['card'], foreground=COLORS['text'],
                        font=('Segoe UI', 10), padding=(16, 8))
        style.map('Sec.TButton', background=[('active', COLORS['card_hover'])])

        style.layout('Dark.TProgressbar', style.layout('Horizontal.TProgressbar'))
        style.configure('Dark.TProgressbar', background=COLORS['progress'], troughcolor=COLORS['border'],thickness=20)

        style.configure('Dark.TEntry', fieldbackground=COLORS['entry_bg'], foreground=COLORS['text'],
                        insertcolor=COLORS['text'], bordercolor=COLORS['border'])

        # ── 标题区 ──
        header = ttk.Frame(self.root, style='Dark.TFrame')
        header.pack(fill='x', padx=24, pady=(20, 8))

        ttk.Label(header, text="🎬 Video Frame Analyzer", style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text="视频帧级分析工具 · PTS/DTS/GOP/码率/Packet/时间戳健康 全面分析 → HTML交互报告", style='Sub.TLabel').pack(anchor='w', pady=(2, 0))

        # ── 文件选择区 ──
        file_frame = ttk.Frame(self.root, style='Card.TFrame')
        file_frame.pack(fill='x', padx=24, pady=8)

        inner = ttk.Frame(file_frame, style='Card.TFrame')
        inner.pack(fill='x', padx=16, pady=12)

        ttk.Label(inner, text="📹 视频文件", style='Card.TLabel').pack(anchor='w')

        path_row = ttk.Frame(inner, style='Card.TFrame')
        path_row.pack(fill='x', pady=(6, 0))

        entry = ttk.Entry(path_row, textvariable=self.filepath, style='Dark.TEntry', width=60)
        entry.pack(side='left', fill='x', expand=True)

        ttk.Button(path_row, text="选择文件", style='Sec.TButton',
                   command=self._browse_file).pack(side='right', padx=(8, 0))

        ttk.Label(inner, text="支持: MP4 / MKV / AVI / MOV / FLV / WebM / TS 等所有 ffmpeg 支持的格式",
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 0))

        # ── ffprobe 路径 ──
        ffprobe_row = ttk.Frame(inner, style='Card.TFrame')
        ffprobe_row.pack(fill='x', pady=(8, 0))

        ttk.Label(ffprobe_row, text="🔧 ffprobe:", style='Card.TLabel').pack(side='left')
        self.ffprobe_path_var = tk.StringVar(value=get_ffprobe_path())
        ttk.Entry(ffprobe_row, textvariable=self.ffprobe_path_var, style='Dark.TEntry',
                  width=50).pack(side='left', padx=(8, 0), fill='x', expand=True)
        ttk.Button(ffprobe_row, text="选择文件", style='Sec.TButton',
                   command=self._browse_ffprobe).pack(side='right', padx=(4, 0))
        ttk.Button(ffprobe_row, text="选择目录", style='Sec.TButton',
                   command=self._browse_ffprobe_dir).pack(side='right')

        # ── 分析选项 ──
        opt_frame = ttk.Frame(self.root, style='Card.TFrame')
        opt_frame.pack(fill='x', padx=24, pady=8)

        opt_inner = ttk.Frame(opt_frame, style='Card.TFrame')
        opt_inner.pack(fill='x', padx=16, pady=12)

        ttk.Label(opt_inner, text="⚙️ 分析选项", style='Card.TLabel').pack(anchor='w')

        opt_grid = ttk.Frame(opt_inner, style='Card.TFrame')
        opt_grid.pack(fill='x', pady=(8, 0))

        opt_items = [
            ('container', '📦 容器/格式信息', 'format, tags, duration'),
            ('streams', '🔧 流编码信息', 'codec, resolution, bitrate, lang'),
            ('frames_pts', '⏱ PTS/DTS 时序', '时间戳、间隔、偏移分析'),
            ('frames_type', '🎞 帧类型分布', 'I/P/B 帧统计与时间线'),
            ('gop', '📐 GOP 结构分析', '关键帧间隔、GOP大小分布'),
            ('bitrate', '📉 帧级码率曲线', '滑动窗口码率、帧大小'),
            ('packets', '📈 Packet码率(精确)', '基于packet的精确码率分析'),
            ('timestamps', '🏥 时间戳健康检测', 'DTS单调性/PTS跳变/丢帧'),
            ('frame_table', '📋 帧数据表', '每帧详细数据，可搜索筛选'),
            ('chapters', '📑 章节信息', '如有 chapter 数据'),
        ]

        self._opt_buttons = {}
        for i, (key, label, desc) in enumerate(opt_items):
            row = i // 2
            col = i % 2
            cell = ttk.Frame(opt_grid, style='Card.TFrame')
            cell.grid(row=row, column=col, sticky='w', padx=(0 if col==0 else 20, 0), pady=3)

            var = self.opt_vars[key]
            cb = tk.Button(cell, text='✓' if var.get() else '', width=2, relief='flat',
                           bg=COLORS['card'], fg=COLORS['accent'], font=('Segoe UI', 11, 'bold'),
                           activebackground=COLORS['card_hover'], activeforeground=COLORS['accent'],
                           bd=1, highlightthickness=1, highlightbackground=COLORS['border'],
                           highlightcolor=COLORS['accent'])
            self._opt_buttons[key] = cb
            def make_toggle(v, btn):
                def toggle(*_):
                    v.set(not v.get())
                    btn.config(text='✓' if v.get() else '')
                return toggle
            cb.config(command=make_toggle(var, cb))
            cb.pack(side='left', padx=(0, 6))
            ttk.Label(cell, text=label, style='Card.TLabel').pack(side='left')
            ttk.Label(cell, text=f"  {desc}", style='Muted.TLabel').pack(side='left')

        # 全选/全不选
        btn_row = ttk.Frame(opt_inner, style='Card.TFrame')
        btn_row.pack(fill='x', pady=(8, 0))
        ttk.Button(btn_row, text="全选", style='Sec.TButton',
                   command=lambda: self._toggle_all(True)).pack(side='left')
        ttk.Button(btn_row, text="全不选", style='Sec.TButton',
                   command=lambda: self._toggle_all(False)).pack(side='left', padx=(8, 0))
        ttk.Button(btn_row, text="仅基本信息", style='Sec.TButton',
                   command=self._preset_basic).pack(side='left', padx=(8, 0))
        ttk.Button(btn_row, text="全部详细", style='Sec.TButton',
                   command=self._preset_full).pack(side='left', padx=(8, 0))

        # ── 输出路径 ──
        out_frame = ttk.Frame(self.root, style='Card.TFrame')
        out_frame.pack(fill='x', padx=24, pady=8)

        out_inner = ttk.Frame(out_frame, style='Card.TFrame')
        out_inner.pack(fill='x', padx=16, pady=12)

        ttk.Label(out_inner, text="💾 输出报告", style='Card.TLabel').pack(anchor='w')

        out_row = ttk.Frame(out_inner, style='Card.TFrame')
        out_row.pack(fill='x', pady=(6, 0))

        ttk.Entry(out_row, textvariable=self.output_path, style='Dark.TEntry',
                  width=60).pack(side='left', fill='x', expand=True)
        ttk.Button(out_row, text="选择路径", style='Sec.TButton',
                   command=self._browse_output).pack(side='right', padx=(8, 0))

        ttk.Label(out_inner, text="留空则自动生成在视频同目录下",
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 0))

        # ── 分析按钮 + 进度 ──
        action_frame = ttk.Frame(self.root, style='Dark.TFrame')
        action_frame.pack(fill='x', padx=24, pady=(12, 4))

        self.start_btn = ttk.Button(action_frame, text="🚀 开始分析", style='Accent.TButton',
                                     command=self._start_analysis)
        self.start_btn.pack(side='left')

        self.open_btn = ttk.Button(action_frame, text="📂 打开报告", style='Sec.TButton',
                                    command=self._open_report, state='disabled')
        self.open_btn.pack(side='left', padx=(12, 0))

        # 进度条
        progress_frame = ttk.Frame(self.root, style='Dark.TFrame')
        progress_frame.pack(fill='x', padx=24, pady=(4, 0))

        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                             maximum=100, style='Dark.TProgressbar')
        self.progress_bar.pack(fill='x')

        ttk.Label(progress_frame, textvariable=self.status_text, style='Status.TLabel').pack(anchor='w', pady=(2, 0))

        # 底部留白
        ttk.Frame(self.root, style='Dark.TFrame').pack(fill='both', expand=True)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.avi *.mov *.flv *.webm *.ts *.m4v *.wmv *.mpg *.mpeg *.3gp *.rmvb"),
                ("所有文件", "*.*"),
            ]
        )
        if path:
            self.filepath.set(path)
            if not self.output_path.get():
                base = os.path.splitext(path)[0]
                self.output_path.set(base + "_report.html")

    def _browse_ffprobe(self):
        path = filedialog.askopenfilename(
            title="选择 ffprobe 可执行文件",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
        )
        if path:
            self.ffprobe_path_var.set(path)

    def _browse_ffprobe_dir(self):
        dirpath = filedialog.askdirectory(title="选择 ffmpeg 所在目录（包含 ffprobe.exe）")
        if not dirpath:
            return
        for name in ['ffprobe.exe', 'ffprobe']:
            p = os.path.join(dirpath, name)
            if os.path.isfile(p):
                self.ffprobe_path_var.set(p)
                return
        for sub in ['bin', '']:
            for name in ['ffprobe.exe', 'ffprobe']:
                p = os.path.join(dirpath, sub, name) if sub else os.path.join(dirpath, name)
                if os.path.isfile(p):
                    self.ffprobe_path_var.set(p)
                    return
        messagebox.showwarning("未找到", f"在选择的目录中未找到 ffprobe:\n{dirpath}\n\n请确认该目录下有 ffprobe.exe", parent=self.root)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="保存报告",
            defaultextension=".html",
            filetypes=[("HTML文件", "*.html"), ("所有文件", "*.*")]
        )
        if path:
            self.output_path.set(path)

    def _toggle_all(self, val):
        for k, v in self.opt_vars.items():
            v.set(val)
            if k in self._opt_buttons:
                self._opt_buttons[k].config(text='✓' if val else '')

    def _preset_basic(self):
        for k in self.opt_vars: self.opt_vars[k].set(False)
        for k in ['container', 'streams']: self.opt_vars[k].set(True)
        for k, btn in self._opt_buttons.items():
            btn.config(text='✓' if self.opt_vars[k].get() else '')

    def _preset_full(self):
        for k, v in self.opt_vars.items(): v.set(True)
        for btn in self._opt_buttons.values(): btn.config(text='✓')

    def _start_analysis(self):
        fp = self.filepath.get().strip()
        if not fp:
            messagebox.showwarning("提示", "请先选择视频文件")
            return
        if not os.path.isfile(fp):
            messagebox.showerror("错误", f"文件不存在:\n{fp}")
            return

        out = self.output_path.get().strip()
        if not out:
            base = os.path.splitext(fp)[0]
            out = base + "_report.html"
            self.output_path.set(out)

        ffprobe = self.ffprobe_path_var.get().strip()
        try:
            subprocess.run([ffprobe, "-version"], capture_output=True, timeout=5)
        except Exception:
            messagebox.showerror("错误", f"ffprobe 不可用:\n{ffprobe}\n\n请安装 ffmpeg 并确保 ffprobe 在 PATH 中，或手动指定路径。")
            return

        options = {k: v.get() for k, v in self.opt_vars.items()}
        if not any(options.values()):
            messagebox.showwarning("提示", "请至少选择一个分析选项")
            return

        self.start_btn.configure(state='disabled')
        self.open_btn.configure(state='disabled')
        self.progress_var.set(0)
        self.status_text.set("准备中...")

        def run():
            try:
                analyzer = VideoAnalyzer(fp, options, ffprobe, self._on_progress)
                analyzer.run()

                # ── DEBUG: 输出分析结果摘要 ──
                vf_count = analyzer.video_stats.get('total_frames', 0)
                af_count = analyzer.audio_stats.get('total_frames', 0)
                ci_keys = list(analyzer.container_info.keys())[:5]
                si_count = len(analyzer.stream_info)
                pkt_count = len(analyzer.packets)
                debug_msg = (f"DEBUG: vf={vf_count} af={af_count} ci_keys={ci_keys} "
                             f"si={si_count} pkt={pkt_count} "
                             f"opts={options}")
                print(debug_msg)
                with open(os.path.join(os.path.dirname(out), '_debug.log'), 'w', encoding='utf-8') as dbg:
                    dbg.write(debug_msg + "\n")
                    dbg.write(f"container_info={json.dumps(analyzer.container_info, ensure_ascii=False)[:500]}\n")
                    dbg.write(f"stream_info_count={len(analyzer.stream_info)}\n")
                    dbg.write(f"video_stats_keys={list(analyzer.video_stats.keys())}\n")
                    dbg.write(f"timestamp_health={json.dumps(analyzer.timestamp_health, ensure_ascii=False)[:300]}\n")
                    dbg.write(f"summary={json.dumps(analyzer.summary, ensure_ascii=False)[:500]}\n")

                self.root.after(0, self.status_text.set, "生成报告...")
                html = generate_html_report(fp, analyzer)

                with open(out, 'w', encoding='utf-8') as f:
                    f.write(html)

                sz = os.path.getsize(out)

                self.root.after(0, self._on_done, out, vf_count, af_count, sz)

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"ERROR: {e}\n{tb}")
                try:
                    with open(os.path.join(os.path.dirname(out), '_debug.log'), 'w', encoding='utf-8') as dbg:
                        dbg.write(f"EXCEPTION: {e}\n{tb}\n")
                except:
                    pass
                self.root.after(0, self._on_error, f"{e}\n{tb[:500]}")

        threading.Thread(target=run, daemon=True).start()

    def _on_progress(self, pct, text):
        self.root.after(0, self.progress_var.set, pct)
        self.root.after(0, self.status_text.set, f"⏳ {text}...")

    def _on_done(self, outpath, vf, af, size):
        self.progress_var.set(100)
        self.status_text.set(f"✅ 完成! 视频帧 {vf:,} · 音频帧 {af:,} · 报告 {size/1024:.0f}KB")
        self.start_btn.configure(state='normal')
        self.open_btn.configure(state='normal')
        self._last_report = outpath
        messagebox.showinfo("分析完成", f"报告已生成:\n{outpath}\n\n视频帧: {vf:,}\n音频帧: {af:,}\n报告大小: {size/1024:.0f} KB\n\n是否打开报告？",
                           parent=self.root)
        self._open_report()

    def _on_error(self, err):
        self.progress_var.set(0)
        self.status_text.set(f"❌ 错误: {err}")
        self.start_btn.configure(state='normal')
        messagebox.showerror("分析失败", f"发生错误:\n{err}")

    def _open_report(self):
        path = getattr(self, '_last_report', self.output_path.get().strip())
        if path and os.path.isfile(path):
            webbrowser.open('file://' + os.path.abspath(path))
        else:
            messagebox.showinfo("提示", "报告文件不存在")

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────

def main():
    app = VideoAnalyzerApp()
    app.run()


if __name__ == "__main__":
    main()
