#!/usr/bin/env python3
"""音乐卡拍：量出配乐的 BPM、拍点、小节首拍和能量段落，再把镜头切点对齐到拍点（通用）。

用法:
  python3 beat_map.py analyze audio/music.wav [--out audio/beats.json] [--bpm-min 60 --bpm-max 180] [--beats-per-bar 4]
  python3 beat_map.py snap audio/beats.json --cuts 2.1,4.3,7.9 [--to beat|downbeat|half] [--max-shift 0.25]
  python3 beat_map.py snap audio/beats.json --project project.json      # 用 project.json 的镜头时长算切点
  python3 beat_map.py snap audio/beats.json --cuts-json work/timeline.json

analyze 输出 beats.json：bpm、拍点 beats、强拍/小节首拍 downbeats、按能量分的段落 sections
（前奏 / 主歌 / 高潮 / 尾声，标签是按能量的近似，不是乐理分析）、onset 强度。
snap 输出每个切点最近的拍点、偏差（秒和帧）、建议时间，以及调整后各镜时长；偏差超过 --max-shift 的标“需人工”。
算法：ffmpeg 解码单声道 → numpy 计算频谱通量 onset 包络 → 自相关定 BPM → 动态规划跟拍（Ellis 2007 思路）。
装了 librosa 时可加 --librosa 用它的 beat_track 对照；默认只用 numpy。
"""
import argparse, json, math, subprocess, sys
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("beat_map: 需要 numpy（pip install numpy）")

SR, HOP, NFFT = 22050, 256, 1024


def decode(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        sys.exit(f"beat_map: 无法解码 {path}: {r.stderr.decode()[:200]}")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def onset_envelope(y):
    n = 1 + max(0, (len(y) - NFFT) // HOP)
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(NFFT)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1))
    logm = np.log1p(100 * mag)
    flux = np.maximum(0, np.diff(logm, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # 去局部均值
    flux = np.maximum(flux, 0)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return flux / (flux.max() or 1), rms


def estimate_bpm(env, fr, lo, hi):
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    ac /= ac[0] or 1
    lags = np.arange(len(ac))
    with np.errstate(divide="ignore"):
        bpms = 60 * fr / np.maximum(lags, 1)
    # 对数高斯先验，偏向 120 左右，压住倍频/半频
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpms, 1) / 120) / 1.0) ** 2)
    score = ac * weight
    mask = (bpms >= lo) & (bpms <= hi)
    score[~mask] = -np.inf
    lag = int(np.argmax(score))
    # 抛物线插值细化
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        den = a - 2 * b + c
        lag_f = lag + (0.5 * (a - c) / den if den else 0)
    else:
        lag_f = lag
    return 60 * fr / lag_f, lag_f


def track_beats(env, period, tightness=100):
    n = len(env)
    score = env.copy().astype(float)
    back = -np.ones(n, dtype=int)
    lo, hi = int(round(period * 0.5)), int(round(period * 2))
    for i in range(n):
        a, b = max(0, i - hi), i - lo
        if b <= a:
            continue
        prev = np.arange(a, b)
        pen = -tightness * (np.log((i - prev) / period)) ** 2
        cand = score[prev] + pen
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = prev[j]
    # 从末尾 period 范围内最好的位置回溯
    tail = np.arange(max(0, n - int(period * 1.5)), n)
    i = int(tail[np.argmax(score[tail])])
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    return np.array(beats[::-1])


def sections(rms, fr, beats_t, duration, bar_len):
    win = max(1, int(round(bar_len * fr)))
    nb = max(1, int(math.ceil(len(rms) / win)))
    energy = np.array([rms[i * win:(i + 1) * win].mean() for i in range(nb)])
    if energy.max() <= 0:
        return []
    e = energy / energy.max()
    # 平滑到 2 小节
    k = np.ones(2) / 2
    e_s = np.convolve(e, k, mode="same")
    hi_thr = np.quantile(e_s, 0.7)
    lo_thr = np.quantile(e_s, 0.3)
    labels = []
    for i, v in enumerate(e_s):
        labels.append("high" if v >= hi_thr else ("low" if v <= lo_thr else "mid"))
    segs, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            segs.append([start, i, labels[start]])
            start = i
    # 合并太短（<2 小节）的段
    merged = []
    for s in segs:
        if merged and s[1] - s[0] < 2:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    fused = []
    for seg in merged:  # 合并相邻同类段
        if fused and fused[-1][2] == seg[2]:
            fused[-1][1] = seg[1]
        else:
            fused.append(seg)
    merged = fused
    out = []
    for idx, (a, b, lab) in enumerate(merged):
        t0, t1 = a * win / fr, min(duration, b * win / fr)
        if idx == 0 and lab != "high":
            name = "前奏"
        elif idx == len(merged) - 1 and lab != "high" and len(merged) > 1:
            name = "尾声"
        else:
            name = {"high": "高潮", "mid": "主歌", "low": "主歌（弱）"}[lab]
        out.append({"label": name, "start": round(t0, 3), "end": round(t1, 3),
                    "energy": round(float(e[a:b].mean()), 3)})
    return out


def analyze(args):
    y = decode(args.audio)
    duration = len(y) / SR
    env, rms = onset_envelope(y)
    fr = SR / HOP
    bpm, lag = estimate_bpm(env, fr, args.bpm_min, args.bpm_max)
    period = 60 * fr / bpm
    bi = track_beats(env, period)
    # 帧 i 的通量对应窗口中心；实测 Hann 窗下峰值比真实起音早约 1 个 hop，补偿一个 hop
    beats = (bi * HOP + NFFT / 2 + HOP) / SR
    beats = beats[(beats >= 0) & (beats <= duration)]
    # 恒速时用回归拟合一条等距网格（抗抖），并给出抖动
    if len(beats) >= 4:
        k = np.arange(len(beats))
        slope, icpt = np.polyfit(k, beats, 1)
        jitter = float(np.std(beats - (slope * k + icpt)))
        if jitter < 0.02:
            beats = slope * k + icpt
            bpm = 60 / slope
    else:
        jitter = 0.0
    bpb = args.beats_per_bar
    # 小节首拍：选 onset 强度累加最大的相位
    # 小节首拍：每拍起音后 ~80 ms 的峰值能量（不取对数，保留重音差），按局部均值归一后比较 bpb 个相位
    def peak(b):
        i0 = max(0, int((b * SR - NFFT / 2) / HOP)); i1 = i0 + max(2, int(0.08 * fr))
        return float(rms[i0:i1].max()) if i1 <= len(rms) and i1 > i0 else 0.0
    strength = np.array([peak(b) for b in beats]) if len(beats) else np.array([])
    if len(strength) >= bpb:
        local = np.convolve(strength, np.ones(bpb) / bpb, mode="same") + 1e-9
        rel = strength / local
        phase = int(np.argmax([rel[p::bpb].mean() for p in range(bpb)]))
    else:
        phase = 0
    downbeats = beats[phase::bpb]
    res = {"source": str(args.audio), "duration": round(duration, 3), "bpm": round(float(bpm), 2),
           "beat_interval": round(60 / float(bpm), 4), "beats_per_bar": bpb, "tempo_jitter_s": round(jitter, 4),
           "beats": [round(float(b), 3) for b in beats], "downbeats": [round(float(b), 3) for b in downbeats],
           "sections": sections(rms, fr, beats, duration, bpb * 60 / float(bpm)),
           "note": "sections 是按能量的近似分段；拍点只是候选，关键切点要听一遍确认"}
    if args.librosa:
        try:
            import librosa  # noqa
            yy, sr = librosa.load(str(args.audio), sr=SR, mono=True)
            t, b = librosa.beat.beat_track(y=yy, sr=sr)
            res["librosa"] = {"bpm": round(float(np.atleast_1d(t)[0]), 2), "beats": [round(float(x), 3) for x in librosa.frames_to_time(b, sr=sr)]}
        except Exception as exc:  # librosa 可选
            res["librosa"] = f"unavailable: {exc}"
    out = Path(args.out) if args.out else Path(args.audio).with_name("beats.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"BPM {res['bpm']}（拍间隔 {res['beat_interval']} 秒，抖动 {res['tempo_jitter_s']} 秒），{len(res['beats'])} 拍，{len(res['downbeats'])} 个小节首拍")
    for s in res["sections"]:
        print(f"  {s['start']:7.2f}–{s['end']:7.2f}s  {s['label']}  能量 {s['energy']}")
    print(out)


def cuts_from_project(path):
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    t, cuts, ids = 0.0, [], []
    for shot in p["shots"]:
        if "duration" not in shot:
            sys.exit("beat_map: project.json 里每镜都要有 duration 才能算切点（或改用 --cuts）")
        t += float(shot["duration"])
        cuts.append(round(t, 3)); ids.append(shot.get("id", str(len(ids) + 1)))
    return cuts[:-1], ids, t


def cuts_from_json(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = d.get("cuts", d) if isinstance(d, dict) else d
    starts = [float(r[1]) if isinstance(r, (list, tuple)) else float(r.get("start", r)) if isinstance(r, dict) else float(r) for r in rows]
    return [s for s in starts if s > 0], None, None


def snap(args):
    beats = json.loads(Path(args.beats).read_text(encoding="utf-8"))
    grid = {"beat": beats["beats"], "downbeat": beats["downbeats"]}.get(args.to)
    if args.to == "half":
        b = beats["beats"]
        grid = sorted(b + [round((x + y) / 2, 3) for x, y in zip(b, b[1:])])
    ids, total = None, None
    if args.project:
        cuts, ids, total = cuts_from_project(args.project)
    elif args.cuts_json:
        cuts, ids, total = cuts_from_json(args.cuts_json)
    else:
        cuts = [float(x) for x in args.cuts.split(",") if x.strip()]
    if not grid:
        sys.exit("beat_map: beats.json 里没有拍点")
    fps = args.fps
    rows, worst = [], 0.0
    for i, c in enumerate(cuts):
        g = min(grid, key=lambda x: abs(x - c))
        d = g - c
        worst = max(worst, abs(d))
        rows.append({"cut": round(c, 3), "after_shot": ids[i] if ids else None, "nearest": g, "shift_s": round(d, 3),
                     "shift_frames": round(d * fps, 1), "status": "ok" if abs(d) <= args.tolerance else
                     ("snap" if abs(d) <= args.max_shift else "需人工")})
    for r in rows:
        tag = {"ok": "已在拍上", "snap": "建议对齐", "需人工": "偏差大，需人工（换拍、改镜头时长或接受）"}[r["status"]]
        who = f" {r['after_shot']}→" if r["after_shot"] else ""
        print(f"  切点{who} {r['cut']:7.3f}s → 拍 {r['nearest']:7.3f}s  偏差 {r['shift_s']:+.3f}s ({r['shift_frames']:+.1f} 帧)  {tag}")
    suggested = [r["nearest"] if r["status"] != "需人工" else r["cut"] for r in rows]
    if ids and total:
        bounds = [0.0] + suggested + [total]
        durs = [round(b - a, 3) for a, b in zip(bounds, bounds[1:])]
        print("  建议各镜时长：" + ", ".join(f"{i}={d}" for i, d in zip(ids, durs)))
    else:
        durs = None
    mean_abs = sum(abs(r["shift_s"]) for r in rows) / len(rows) if rows else 0
    print(f"最大偏差 {worst:.3f}s，平均 {mean_abs:.3f}s；容差 {args.tolerance}s（约 {args.tolerance*fps:.0f} 帧）")
    if args.out:
        Path(args.out).write_text(json.dumps({"grid": args.to, "rows": rows, "suggested_cuts": suggested,
                                              "suggested_durations": durs}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(args.out)
    return 1 if any(r["status"] == "需人工" for r in rows) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="量 BPM、拍点、小节首拍、能量段落 → beats.json")
    a.add_argument("audio")
    a.add_argument("--out")
    a.add_argument("--bpm-min", type=float, default=60)
    a.add_argument("--bpm-max", type=float, default=180)
    a.add_argument("--beats-per-bar", type=int, default=4)
    a.add_argument("--librosa", action="store_true", help="可选：装了 librosa 时附上它的结果对照")
    s = sub.add_parser("snap", help="把镜头切点对齐到最近的拍点并报告偏差")
    s.add_argument("beats")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--cuts", help="逗号分隔的切点秒数")
    g.add_argument("--project", help="project.json（按镜头 duration 累加算切点）")
    g.add_argument("--cuts-json", help="含 cuts 的 JSON（[[id,start,end],...] 或秒数列表）")
    s.add_argument("--to", choices=["beat", "downbeat", "half"], default="beat")
    s.add_argument("--fps", type=float, default=30)
    s.add_argument("--tolerance", type=float, default=0.04, help="视为已在拍上的偏差（秒，默认约 1 帧）")
    s.add_argument("--max-shift", type=float, default=0.25, help="超过此偏差不自动对齐（秒）")
    s.add_argument("--out")
    args = ap.parse_args()
    if args.cmd == "analyze":
        analyze(args); return 0
    return snap(args)


if __name__ == "__main__":
    raise SystemExit(main())
