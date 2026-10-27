#!/usr/bin/env python3
"""audio_qc.py — 通用音频验收（素材级 + 成片级）。

对任意音频或带音轨的视频（mp3/wav/m4a/aac/mp4/mov…）做全程逐窗扫描：
  整体：采样率/声道、积分响度 LUFS、真峰值 dBTP、采样峰值、削波样本数、直流偏移
  逐窗：响度突跳、噼啪/静电/爆音（陡峭起音的短促宽带脉冲）、嘶嘶声/高频噪声异常、
        静音段或语句间隙里的底噪、窗口内削波
  输出：问题时间点（合并成区间）、全程频谱图、每个问题区间的局部频谱图；有问题 exit 1。

用法：
  python3 audio_qc.py audio/vo/s01.mp3 --kind vo
  python3 audio_qc.py audio/amb_fire.wav --kind ambient
  python3 audio_qc.py output/final.mp4 --kind mix --lufs -16 --tp -1
  python3 audio_qc.py a.wav b.wav c.mp3 --out qc/audio     # 批量
  --json 输出机器可读结果；--no-png 不出图。
依赖：ffmpeg/ffprobe、numpy。
"""
import argparse, json, os, re, subprocess, sys
import numpy as np

SR = 48000
KIND_DEFAULTS = {
    # gap_floor: 静音/间隙里允许的最大底噪 dBFS；hf_ratio: 高频(>4k)能量占比上限（持续时）
    'vo':      dict(gap_floor=-60.0, hf_ratio=0.45, lufs=None, tp=-1.0),
    'ambient': dict(gap_floor=None,  hf_ratio=0.25, lufs=None, tp=-1.0),
    'music':   dict(gap_floor=None,  hf_ratio=0.45, lufs=None, tp=-1.0),
    'mix':     dict(gap_floor=None, hf_ratio=0.35, lufs=-16.0, tp=-1.0),
    'auto':    dict(gap_floor=-55.0, hf_ratio=0.35, lufs=None, tp=-1.0),
}

def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def probe(path):
    r = sh(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
            'stream=sample_rate,channels,codec_name:format=duration', '-of', 'json', path])
    j = json.loads(r.stdout or '{}')
    if not j.get('streams'):
        return None
    s = j['streams'][0]
    return dict(sr=int(s['sample_rate']), ch=int(s['channels']), codec=s.get('codec_name'),
                dur=float(j.get('format', {}).get('duration', 0) or 0))

def decode(path, ch):
    # 保留原声道（不做下混，避免把削波“混没了”），统一重采样到 48k 做分析
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ar', str(SR),
                          '-f', 'f32le', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    return x[: len(x) // ch * ch].reshape(-1, ch) if x.size else np.zeros((0, ch), np.float32)

def loudness(path):
    r = sh(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    tail = r.stderr[r.stderr.rfind('Summary'):]
    i = re.search(r'I:\s+(-?[\d.]+|-inf) LUFS', tail)
    p = re.search(r'Peak:\s+(-?[\d.]+|-inf) dBFS', tail)
    f = lambda m: (float(m.group(1)) if m and m.group(1) != '-inf' else -120.0)
    return f(i), f(p)

def db(v):
    return 20 * np.log10(np.maximum(v, 1e-9))

def highpass(x, fc):
    """FFT 分块高通（无相位失真，足够做检测）。"""
    out = np.zeros_like(x); blk = SR * 20; ov = 4096
    for st in range(0, len(x), blk):
        a = max(0, st - ov); b = min(len(x), st + blk + ov)
        X = np.fft.rfft(x[a:b]); f = np.fft.rfftfreq(b - a, 1 / SR)
        X[f < fc] = 0
        y = np.fft.irfft(X, b - a)
        out[st:min(st + blk, len(x))] = y[st - a: st - a + min(blk, len(x) - st)]
    return out

def fmt(t):
    m, s = divmod(t, 60); return f'{int(m)}:{s:05.2f}'

def merge(hits, gap):
    """hits: [(t0,t1,type,detail)] -> 同类合并成区间"""
    out = []
    for typ in sorted(set(h[2] for h in hits)):
        hs = sorted(h for h in hits if h[2] == typ); cur = None
        for t0, t1, _, d in hs:
            if cur and t0 <= cur[1] + gap:
                cur[1] = max(cur[1], t1); cur[3].append(d)
            else:
                if cur: out.append(cur)
                cur = [t0, t1, typ, [d]]
        if cur: out.append(cur)
    return sorted(out)

LABEL = {
    'clip': '削波（采样值触顶）',
    'crackle': '噼啪/静电/爆音（陡峭起音的短促宽带脉冲）',
    'hiss': '嘶嘶声/高频噪声异常（高频能量持续偏高）',
    'gap_noise': '静音或间隙里有底噪',
    'jump': '响度突跳',
    'dc': '直流偏移',
}

def analyze(path, kind, win, args):
    cfg = dict(KIND_DEFAULTS[kind])
    for k in ('gap_floor', 'hf_ratio', 'lufs', 'tp'):
        v = getattr(args, k)
        if v is not None: cfg[k] = v
    info = probe(path)
    res = dict(file=path, kind=kind, issues=[], summary={})
    if not info:
        res['issues'].append(dict(type='no_audio', start=0, end=0, msg='没有音轨或无法解码')); return res
    x2 = decode(path, info['ch'])
    if len(x2) < SR * 0.2:
        res['issues'].append(dict(type='no_audio', start=0, end=0, msg='音频过短或解码失败')); return res
    x = x2.mean(axis=1).astype(np.float64)
    I, TP = loudness(path)
    clipped_total = int((np.abs(x2) >= 0.999).sum())
    dc = float(np.abs(x2.mean(axis=0)).max())
    dur = len(x) / SR
    res['summary'] = dict(duration=round(dur, 2), sample_rate=info['sr'], channels=info['ch'], codec=info['codec'],
                          lufs=I, true_peak=TP, sample_peak=round(float(db(np.abs(x2).max())), 2),
                          clipped_samples=clipped_total, dc_offset=round(dc, 5))
    hits = []
    # ---- 削波：逐个位置
    cidx = np.flatnonzero((np.abs(x2) >= 0.999).any(axis=1))
    for i in cidx[:: max(1, len(cidx) // 200)]:
        hits.append((i / SR, i / SR + 0.01, 'clip', 'sample≥0.999'))
    if dc > 0.01:
        hits.append((0, dur, 'dc', f'DC={dc:.3f}'))
    # ---- 噼啪/静电：1 ms 帧的高频(>2k)包络，找“瞬间起音 + 短促 + 明显高于本地背景”的脉冲
    hp = highpass(x, 2000.0)
    fl = SR // 1000; nf = len(hp) // fl
    env = np.sqrt((hp[: nf * fl].reshape(nf, fl) ** 2).mean(axis=1) + 1e-18)
    envdb = db(env)
    # 本地背景：前 30 ms 的中位数
    k = 30
    pad = np.concatenate([np.full(k, envdb[0]), envdb])
    from numpy.lib.stride_tricks import sliding_window_view as swv
    bg = np.median(swv(pad[:-1], k), axis=1)[: nf]
    rise = envdb - np.concatenate([[envdb[0]], envdb[:-1]])          # 相邻 1 ms 帧的跳升
    def pulses(thr):
        on_idx = np.flatnonzero((rise >= thr) & (envdb - bg >= thr + 3) & (envdb > args.click_min))
        # 硬切脉冲：起音陡峭，且 3–80 ms 内又陡峭落回（矩形门/数字爆点/拼接 click 的特征；语音爆破音是缓慢衰减）
        keep = []
        for i in on_idx:
            j = np.flatnonzero(rise[i + 1: i + 81] <= -thr * 0.8)
            if j.size or (rise[i] >= 30 and envdb[i] - bg[i] >= 30):
                keep.append(i)
        return np.array(keep) / 1000.0
    on_t = pulses(args.click_rise)                 # 单窗高密度（素材里最明显）
    on_t_s = pulses(args.click_rise - 3)           # 低阈值，用于“持续型”噼啪（被人声/音乐遮住时）
    hp6 = highpass(x, 6000.0) if (on_t.size or on_t_s.size) else None
    def broadband(ts):
        ok = 0
        for t in ts:
            i = int(t * SR); s6 = hp6[i: i + 5 * fl]; s2 = hp[i: i + 5 * fl]
            if s6.size and np.sqrt((s6 ** 2).mean()) > 0.3 * np.sqrt((s2 ** 2).mean() + 1e-18): ok += 1
        return ok
    sustained = []
    # 逐窗统计
    nwin = int(np.ceil(dur / win))
    rms_db = []; hf_r = []
    for w in range(nwin):
        a = int(w * win * SR); b = min(len(x), int((w + 1) * win * SR))
        seg = x[a:b]
        if len(seg) < SR * 0.05: rms_db.append(-120); hf_r.append(0); continue
        r = float(db(np.sqrt((seg ** 2).mean())))
        S = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2; f = np.fft.rfftfreq(len(seg), 1 / SR)
        hr = float(S[f > 4000].sum() / (S.sum() + 1e-20)) if r > -70 else 0.0
        rms_db.append(r); hf_r.append(hr)
        t0, t1 = a / SR, b / SR
        ok = broadband(on_t[(on_t >= t0) & (on_t < t1)])
        if ok / (t1 - t0) >= args.click_rate:
            hits.append((t0, t1, 'crackle', f'{ok} 个/窗 硬切宽带脉冲'))
        sustained.append(broadband(on_t_s[(on_t_s >= t0) & (on_t_s < t1)]))
        # 窗口内削波已在上面
    # 持续型噼啪：连续 ≥N 秒每秒都有硬切脉冲（正常人声偶发 1–3 秒，不会连续很久）
    need = max(2, int(round(args.crackle_run / win))); w = 0
    while w < nwin:
        if sustained[w] >= 1:
            v = w
            while v + 1 < nwin and sustained[v + 1] >= 1: v += 1
            if v - w + 1 >= need:
                hits.append((w * win, min(dur, (v + 1) * win), 'crackle', f'连续 {(v-w+1)*win:g}s 每窗都有硬切脉冲（共 {sum(sustained[w:v+1])} 个）'))
            w = v + 1
        else:
            w += 1
    rms_db = np.array(rms_db); hf_r = np.array(hf_r)
    # 嘶嘶/高频噪声：高频占比超过上限，且显著高于全片中位（相对异常），连续 ≥2 窗
    med_hf = float(np.median(hf_r[rms_db > -60])) if (rms_db > -60).any() else 0
    hiss = (hf_r > cfg['hf_ratio']) & ((hf_r > med_hf * 2 + 0.05) | (med_hf > cfg['hf_ratio'])) & (rms_db > -60)
    for w in np.flatnonzero(hiss):
        prev_ok = w > 0 and hiss[w - 1]; next_ok = w + 1 < nwin and hiss[w + 1]
        if prev_ok or next_ok or kind == 'ambient':
            hits.append((w * win, min(dur, (w + 1) * win), 'hiss', f'HF>4k 占比 {hf_r[w]*100:.0f}%（全片中位 {med_hf*100:.0f}%）'))
    # 响度突跳：比前 3 窗中位高 N dB，且跳到的电平超出全片 95 分位 +3 dB（正常的“停顿后开口”不算）
    p95 = float(np.percentile(rms_db[rms_db > -70], 95)) if (rms_db > -70).any() else 0
    for w in range(1, nwin):
        ref = float(np.median(rms_db[max(0, w - 3): w]))
        if rms_db[w] - ref >= args.jump_db and rms_db[w] > p95 + 3:
            hits.append((w * win - 0.25, w * win + 0.25, 'jump', f'{ref:.0f}→{rms_db[w]:.0f} dBFS（全片 95 分位 {p95:.0f}）'))
    # 间隙底噪：100 ms 帧中“安静但不静音”的帧
    if cfg['gap_floor'] is not None:
        f100 = SR // 10; n1 = len(x) // f100
        e100 = db(np.sqrt((x[: n1 * f100].reshape(n1, f100) ** 2).mean(axis=1)))
        # 找语句间隙：比该处前后 1 s 最大值低 25 dB 以上的帧
        mx = np.array([e100[max(0, i - 10): i + 11].max() for i in range(n1)])
        gap = (e100 < mx - 25)
        bad = gap & (e100 > cfg['gap_floor'])
        # 只认“持续 ≥0.3 s 且电平平稳（std<4 dB）”的间隙噪声，避免把轻声字尾/呼吸当成底噪
        i = 0
        while i < n1:
            if bad[i]:
                j = i
                while j + 1 < n1 and bad[j + 1]: j += 1
                if j - i + 1 >= 3 and np.std(e100[i: j + 1]) < 4:
                    hits.append((i / 10, (j + 1) / 10, 'gap_noise', f'间隙电平约 {np.median(e100[i:j+1]):.0f} dBFS > {cfg["gap_floor"]:.0f}'))
                i = j + 1
            else:
                i += 1
    # 整体
    glob = []
    if clipped_total: glob.append(f'削波样本 {clipped_total} 个')
    if TP > cfg['tp']: glob.append(f'真峰值 {TP:.1f} dBTP > {cfg["tp"]:.1f}')
    if cfg['lufs'] is not None and abs(I - cfg['lufs']) > args.lufs_tol:
        glob.append(f'积分响度 {I:.1f} LUFS，偏离目标 {cfg["lufs"]:.0f}±{args.lufs_tol:g}')
    if kind in ('mix',) and info['sr'] not in (44100, 48000):
        glob.append(f'采样率 {info["sr"]}')
    res['global_issues'] = glob
    for t0, t1, typ, ds in merge(hits, gap=max(win, 0.3)):
        res['issues'].append(dict(type=typ, start=round(t0, 2), end=round(t1, 2), msg=LABEL[typ],
                                  detail=ds[0] if len(ds) == 1 else f'{ds[0]} …（共 {len(ds)} 处）'))
    res['windows'] = dict(win=win, rms_db=[round(float(v), 1) for v in rms_db], hf_ratio=[round(float(v), 3) for v in hf_r])
    return res

def spectro(path, png, ss=None, to=None, w=1800, h=600):
    cmd = ['ffmpeg', '-v', 'error', '-y']
    if ss is not None: cmd += ['-ss', f'{ss:.2f}', '-to', f'{to:.2f}']
    cmd += ['-i', path, '-vn', '-lavfi', f'showspectrumpic=s={w}x{h}:legend=1:scale=log:stop=16000', png]
    subprocess.run(cmd, capture_output=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--kind', default='auto', choices=list(KIND_DEFAULTS), help='vo 旁白 / ambient 环境音 / music / mix 成片混音 / auto')
    ap.add_argument('--win', type=float, default=1.0, help='扫描窗口秒数（1 或 5）')
    ap.add_argument('--lufs', type=float, help='目标积分响度（mix 默认 -16）')
    ap.add_argument('--lufs-tol', type=float, default=1.0)
    ap.add_argument('--tp', type=float, help='真峰值上限 dBTP（默认 -1）')
    ap.add_argument('--gap-floor', type=float, help='间隙底噪上限 dBFS')
    ap.add_argument('--hf-ratio', type=float, help='高频(>4k)能量占比上限')
    ap.add_argument('--click-rise', type=float, default=15.0, help='1 ms 内高频包络跳升 dB 判为陡峭起音')
    ap.add_argument('--click-min', type=float, default=-55.0, help='脉冲最低电平 dBFS')
    ap.add_argument('--click-rate', type=float, default=3.0, help='每秒 ≥N 个宽带陡峭脉冲即标记')
    ap.add_argument('--crackle-run', type=float, default=5.0, help='持续型噼啪：连续 N 秒每秒都有脉冲即标记')
    ap.add_argument('--jump-db', type=float, default=10.0)
    ap.add_argument('--out', help='频谱图目录（默认 <文件目录>/qc）')
    ap.add_argument('--no-png', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    allres = []; bad = False
    for p in args.files:
        r = analyze(p, args.kind, args.win, args)
        if not args.no_png and r.get('summary'):
            od = args.out or os.path.join(os.path.dirname(os.path.abspath(p)), 'qc')
            os.makedirs(od, exist_ok=True); base = os.path.splitext(os.path.basename(p))[0]
            full = os.path.join(od, f'{base}_spectrum.png'); spectro(p, full); r['spectrum'] = full
            r['issue_pngs'] = []
            for i, it in enumerate(r['issues'][:12]):
                a = max(0, it['start'] - 1); b = min(r['summary']['duration'], it['end'] + 1)
                if b - a < 0.5: continue
                png = os.path.join(od, f'{base}_issue{i+1}_{it["type"]}_{a:.1f}-{b:.1f}s.png')
                spectro(p, png, a, b, 1200, 500); r['issue_pngs'].append(png)
        is_bad = bool(r['issues'] or r.get('global_issues'))
        bad |= is_bad; allres.append(r)
        if not args.json:
            s = r.get('summary', {})
            print(f"\n=== {p}  [{r['kind']}]  {'有问题' if is_bad else '通过'}")
            if s:
                print(f"  时长 {s['duration']}s  {s['sample_rate']}Hz/{s['channels']}ch  积分 {s['lufs']:.1f} LUFS  "
                      f"真峰值 {s['true_peak']:.1f} dBTP  采样峰值 {s['sample_peak']:.1f} dBFS  削波样本 {s['clipped_samples']}")
            for g in r.get('global_issues', []): print(f'  [整体] {g}')
            for it in r['issues']:
                print(f"  [{fmt(it['start'])} – {fmt(it['end'])}] {it['msg']}：{it.get('detail','')}")
            if r.get('spectrum'): print(f"  频谱图：{r['spectrum']}")
            if is_bad: print('  → 去上面的时间点逐个抽听，确认后修素材或混音，再重跑本脚本。')
    if args.json:
        for r in allres: r.pop('windows', None)
        print(json.dumps(allres, ensure_ascii=False, indent=1))
    sys.exit(1 if bad else 0)

if __name__ == '__main__':
    main()
