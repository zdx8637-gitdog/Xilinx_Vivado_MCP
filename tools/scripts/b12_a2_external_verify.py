#!/usr/bin/env python3
"""b12_a2_external_verify.py — B12-A2 盲测证据的外部独立复核（solid 证据工具）。

用途：不与固件/子代理的测量共用代码路径，独立地从"保存的上传数据文件"重新计算
盲测结论，并绘制 8 通道「ADC 原始值 vs 时间」波形图（Y 轴为原始计数值，不换算电压，
符合 B12-A2 需求 v2）。

用法:
    python tools/scripts/b12_a2_external_verify.py <数据文件.csv> [--out <目录>] \
        [--fs 2000] [--range-v 10] [--format wide|long]

输入 CSV 支持两种形态（按表头自动识别，可用 --format 强制）:
  wide: t,ch1,ch2,...,ch8      （每行一个采样时刻，8 通道值）
  long: t,ch,value             （每行一个样本点）

输出（写至 --out 目录，缺省为数据文件同目录）:
  b12_a2_measurement.json — fs/样本数/各通道方差/有信号通道(板子丝印 1-based)/
                            过零频率/过零次数/Vpp(原始计数与 ±量程换算 V)
  b12_a2_waveforms_8ch.png — 8 子图，原始 ADC 计数值 vs 时间

盲测保密：本工具不含任何通道号/频率常量，一切从数据推导。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    plt = None

VOLTS_PER_LSB = {5.0: 5.0 / 32768.0, 10.0: 20.0 / 65536.0}
NCH = 8


def _detect_format(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        header = f.readline().strip().lower()
    fields = [h.strip() for h in header.split(",") if h.strip()]
    if "ch" in fields and "value" in fields:
        return "long"
    ch_cols = [h for h in fields if h.startswith(("ch", "v"))]
    if len(ch_cols) >= NCH:
        return "wide"
    raise SystemExit(
        f"无法识别 CSV 表头：{header!r}（期望 wide: t,ch1..ch8 或 long: t,ch,value）")


def _load(path: str, fmt: str, fs: float | None):
    if fmt == "auto":
        fmt = _detect_format(path)
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k.strip().lower(): v.strip() for k, v in r.items()})
    if fmt == "wide":
        tcol = next((k for k in ("t", "time", "sample", "n") if k in rows[0]), None)
        chcols = [k for k in rows[0].keys() if k.startswith(("ch", "v"))][:NCH]
        if len(chcols) < NCH:
            raise SystemExit(f"wide 格式需要 8 个通道列，实际 {len(chcols)} 个: {chcols}")
        ts = []
        rows_wide = []
        for r in rows:
            if tcol is not None and r.get(tcol, "") != "":
                ts.append(float(r[tcol]))
            rows_wide.append([int(float(r[c])) for c in chcols])
        # 转置为 [通道][样本]
        data = [[row[c] for row in rows_wide] for c in range(NCH)]
    elif fmt == "long":
        tcol = next((k for k in ("t", "time", "sample", "n") if k in rows[0]), None)
        chcol = next((k for k in ("ch", "channel") if k in rows[0]), None)
        vcol = next((k for k in ("value", "v", "raw") if k in rows[0]), None)
        if chcol is None or vcol is None:
            raise SystemExit("long 格式需要 ch 与 value 列")
        by_ch = {i: [] for i in range(NCH)}
        ts = []
        for r in rows:
            ch = int(float(r[chcol])) - 1  # 丝印 1-based -> 0-based
            if 0 <= ch < NCH:
                by_ch[ch].append(int(float(r[vcol])))
            if tcol is not None and r.get(tcol, "") != "":
                ts.append(float(r[tcol]))
        n = max(len(v) for v in by_ch.values())
        data = [[by_ch[i][k] if k < len(by_ch[i]) else 0 for k in range(n)]
                for i in range(NCH)]
    else:
        raise SystemExit(f"未知格式: {fmt}")

    n = len(data[0]) if data else 0
    if fs is None:
        if len(ts) == n and n > 1:
            fs = 1.0 / (ts[1] - ts[0])
            print(f"[info] 采样率按 t 列推算 = {fs:.3f} Hz")
        else:
            raise SystemExit("无法确定采样率：请用 --fs 指定")
    return fs, n, data


def _estimate_frequency(fs: float, x, mean: float):
    """频率估计：插值过零给初值 + 四参数正弦最小二乘拟合精修。

    1s 窗口内朴素过零只有 ±0.5Hz 分辨率，达不到 ≤1% 对账精度；
    此处用（a）线性插值过零求平均周期，再（b）Gauss-Newton 拟合
    v(t)=A*sin(2πf t+φ)+C 精修。拟合失败时退回（a）。
    """
    n = len(x)
    ts = [i / fs for i in range(n)]
    # (a) 插值过零（仅上升沿），求平均周期
    rise_t = []
    for i in range(1, n):
        y0, y1 = x[i - 1] - mean, x[i] - mean
        if y0 < 0 <= y1:
            frac = -y0 / (y1 - y0) if y1 != y0 else 0.0
            rise_t.append((i - 1 + frac) / fs)
    zc_freq = None
    if len(rise_t) >= 2:
        zc_freq = (len(rise_t) - 1) / (rise_t[-1] - rise_t[0])
    if zc_freq is None:
        return None, len(rise_t)

    # (b) 四参数正弦最小二乘拟合（Gauss-Newton）
    try:
        import numpy as np
        t = np.asarray(ts, dtype=float)
        y = np.asarray(x, dtype=float)
        amp = (max(x) - min(x)) / 2.0
        w = 2.0 * math.pi * zc_freq
        phi = 0.0
        c = mean
        for _ in range(8):
            s = np.sin(w * t + phi)
            cw = np.cos(w * t + phi)
            jac = np.column_stack([s, amp * t * cw, amp * cw, np.ones(n)])
            res = y - (amp * s + c)
            try:
                step, *_ = np.linalg.lstsq(jac, res, rcond=None)
            except np.linalg.LinAlgError:
                break
            amp += step[0]
            w += step[1]
            phi += step[2]
            c += step[3]
            if abs(step[1]) < 1e-9:
                break
        fit_freq = w / (2.0 * math.pi)
        if 0.1 < fit_freq < fs / 2:
            return fit_freq, len(rise_t)
    except Exception:
        pass
    return zc_freq, len(rise_t)


def _measure(fs: float, n: int, data, range_v: float):
    variance = []
    for ch in range(NCH):
        x = data[ch]
        mean = sum(x) / n
        variance.append(sum((v - mean) ** 2 for v in x) / n)
    active = variance.index(max(variance))
    x = data[active]
    mean = sum(x) / n
    freq, zc = _estimate_frequency(fs, x, mean)
    duration = n / fs
    vpp_raw = max(x) - min(x)
    lsb = VOLTS_PER_LSB.get(range_v)
    vpp_v = vpp_raw * lsb if lsb else None
    return {
        "fs": fs,
        "n_samples": n,
        "duration_s": round(duration, 6),
        "range_v": range_v,
        "active_channel_silkscreen": active + 1,
        "active_channel_index0": active,
        "variances": [round(v, 2) for v in variance],
        "active_variance": round(variance[active], 2),
        "zero_crossings_rising": zc,
        "frequency_hz": round(freq, 4) if freq else None,
        "frequency_method": "zero-crossing seed + 4-param sine fit",
        "vpp_raw": int(vpp_raw),
        "vpp_volts": round(vpp_v, 4) if vpp_v is not None else None,
        "note": "波形图 Y 轴为 ADC 原始计数值（不换算电压）",
    }


def _plot(out_png: str, fs: float, data):
    if plt is None:
        print("[warn] matplotlib 不可用，跳过波形图")
        return False
    n = len(data[0]) if data else 0
    t = [i / fs for i in range(n)]
    fig, axes = plt.subplots(8, 1, figsize=(12, 16), sharex=True)
    for ch, ax in enumerate(axes):
        ax.plot(t, data[ch], linewidth=0.6)
        ax.set_ylabel(f"CH{ch + 1} (raw)", fontsize=8)
        ax.grid(True, linewidth=0.3)
        if ch == 7:
            ax.set_xlabel("time (s)")
    fig.suptitle("B12-A2 ADC raw counts vs time (8 channels)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return True


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台中文可读
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="B12-A2 盲测数据外部复核")
    ap.add_argument("csv_path", help="保存的采样数据 CSV")
    ap.add_argument("--out", default=None, help="输出目录（缺省=CSV 同目录）")
    ap.add_argument("--fs", type=float, default=None, help="采样率 Hz（缺省按 t 列推算）")
    ap.add_argument("--range-v", type=float, default=10.0, choices=(5.0, 10.0),
                    help="ADC 量程（±V，缺省 10）")
    ap.add_argument("--format", default="auto", choices=("auto", "wide", "long"),
                    help="CSV 形态（缺省自动识别）")
    args = ap.parse_args(argv)

    fs, n, data = _load(args.csv_path, args.format, args.fs)
    result = _measure(fs, n, data, args.range_v)
    outdir = args.out or os.path.dirname(os.path.abspath(args.csv_path)) or "."
    os.makedirs(outdir, exist_ok=True)
    mjson = os.path.join(outdir, "b12_a2_measurement.json")
    with open(mjson, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    png = os.path.join(outdir, "b12_a2_waveforms_8ch.png")
    ok_png = _plot(png, fs, data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[out] {mjson}")
    if ok_png:
        print(f"[out] {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
