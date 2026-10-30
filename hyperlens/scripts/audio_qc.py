#!/usr/bin/env python3
"""audio_qc.py — 通用音频验收（素材级 + 成片级）。

对任意音频或带音轨的视频（mp3/wav/m4a/aac/mp4/mov…）做全程逐窗扫描：
  整体：采样率/声道、积分响度 LUFS、真峰值 dBTP、采样峰值、削波样本数、直流偏移
  逐窗：响度突跳、噼啪/静电/爆音（陡峭起音的短促宽带脉冲）、嘶嘶声/高频噪声异常、
        静音段或语句间隙里的底噪、窗口内削波
  嗡声：持续低频单音（静态正弦铺底/电源 50/60 Hz 及谐波），所有 kind 默认查
  间隙：旁白停顿处铺底过响、停顿处低频（<150 Hz）偏高（mix/auto 默认查；--vo 给旁白分轨时最准）
  输出：问题时间点（合并成区间）、全程频谱图、每个问题区间的局部频谱图；有问题 exit 1。

用法：
  python3 audio_qc.py audio/vo/s01.mp3 --kind vo
  python3 audio_qc.py audio/amb_fire.wav --kind ambient
  python3 audio_qc.py output/final.mp4 --kind mix --lufs -16 --tp -1
  python3 audio_qc.py a.wav b.wav c.mp3 --out qc/audio     # 批量
  python3 audio_qc.py output/final.mp4 --kind mix --vo work/vo.wav            # 用旁白分轨定位停顿
  python3 audio_qc.py output/final.mp4 --fix-hum output/final_dehum.mp4       # 只有成片时：测稳态单音→陷波→两遍响度
  --json 输出机器可读结果；--no-png 不出图；--no-hum 关掉嗡声检查。
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
    'hum': '嗡声：持续低频单音（静态正弦铺底或电源嗡声）',
    'bed_level': '停顿处铺底过响',
    'lf_bed': '停顿处低频偏高（低频嗡/轰）',
}

# ---- 嗡声 / 间隙检查的阈值（依据见 references/audio_qc.md「判定标准」）
HUM_PCT = 20          # 每个频点取全片 20 分位：只有 ≥80% 时间都在的稳态单音能留下，旁白和旋律被压掉
HUM_FMIN, HUM_FMAX = 30, 500
HUM_REL_P0 = -20.0    # 稳态单音合计电平比主体电平高于 −20 dB 判 P0（铺底应比旁白低 15–20 dB 以上）
HUM_MIN_DBFS = -70.0
BED_REL_P0 = -15.0    # 停顿处整体电平比说话段高于 −15 dB 判 P0（铺底比旁白低 15–20 dB 的下限）
LF_REL_P0 = -25.0     # 停顿处 <150 Hz 电平比说话段高于 −25 dB 判 P0（实测：坏片 −7…−14 dB，修好 −28…−31 dB）

def tnr_threshold(f):
    """ECMA-74 附录 D 的单音噪声比判据：f<1 kHz 时 8 + 8.33·log10(1000/f) dB，否则 8 dB。"""
    return 8 + 8.33 * np.log10(1000 / f) if f < 1000 else 8.0

def hum_scan(x):
    """找持续低频单音。x: 单声道 48k。返回 dict(tones=[(Hz, TNR, dBFS)], total, body, rel, mains)。"""
    W = SR; n = len(x) // W
    if n < 5:
        return None
    win = np.hanning(W); fr = np.fft.rfftfreq(W, 1 / SR); top = int(HUM_FMAX + 60)
    S = np.empty((n, top + 1))
    for i in range(n):
        S[i] = (np.abs(np.fft.rfft(x[i * W:(i + 1) * W] * win)) ** 2)[: top + 1]
    rms = 10 * np.log10((x[: n * W].reshape(n, W) ** 2).mean(axis=1) + 1e-20)
    body = float(np.median(rms[rms >= np.percentile(rms, 60)]))
    St = np.percentile(S, HUM_PCT, axis=0) + 1e-20; fr = fr[: top + 1]
    norm = (win ** 2).sum() * W / 2
    tones = []
    for b in range(HUM_FMIN, HUM_FMAX + 1):
        if St[b] < St[b - 1] or St[b] < St[b + 1]:
            continue
        m = (np.abs(fr - fr[b]) <= 50) & (np.abs(fr - fr[b]) > 3)
        tnr = 10 * np.log10(St[b - 1: b + 2].sum() / (np.median(St[m]) * 100))
        lvl = 10 * np.log10(St[b - 1: b + 2].sum() / norm)
        if tnr >= tnr_threshold(fr[b]) and lvl > HUM_MIN_DBFS:
            tones.append((int(fr[b]), round(float(tnr), 1), round(float(lvl), 1)))
    total = float(10 * np.log10(sum(10 ** (t[2] / 10) for t in tones))) if tones else -120.0
    mains = [t[0] for t in tones if any(abs(t[0] - h * k) <= 1 for h in (50, 60) for k in range(1, 9))]
    return dict(tones=tones, total=round(total, 1), body=round(body, 1), rel=round(total - body, 1), mains=mains)

def _band_db(frames, lo, hi):
    F = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1)) ** 2
    f = np.fft.rfftfreq(frames.shape[1], 1 / SR); sel = (f >= lo) & (f < hi)
    return 10 * np.log10(2 * F[:, sel].sum(axis=1) / (np.hanning(frames.shape[1]) ** 2).sum() / frames.shape[1] + 1e-20)

def gap_scan(x, vo=None):
    """停顿处铺底电平。vo 给了就用旁白分轨定停顿（<−60 dBFS 为停顿，>−40 为说话），
    否则用 1–4 kHz 语音频带自动分（≥ 90 分位 −15 dB 为说话，≤ 90 分位 −28 dB 为停顿）。"""
    f = SR // 10; n = len(x) // f
    if n < 50:
        return None
    fr = x[: n * f].reshape(n, f)
    tot = 10 * np.log10((fr ** 2).mean(axis=1) + 1e-20)
    lf = _band_db(fr, 20, 150)
    if vo is not None:
        k = min(n, len(vo) // f); ev = 10 * np.log10((vo[: k * f].reshape(k, f) ** 2).mean(axis=1) + 1e-20)
        gap = np.zeros(n, bool); sp = np.zeros(n, bool); gap[:k] = ev < -60; sp[:k] = ev > -40; mode = 'vo'
    else:
        band = _band_db(fr, 1000, 4000); p90 = np.percentile(band, 90)
        sp = band >= p90 - 15; gap = (band <= p90 - 28) & (tot > -90); mode = 'auto'
    if gap.sum() < 20 or sp.sum() < 50:
        return dict(mode=mode, skipped=True, gap_s=gap.sum() / 10, speech_s=sp.sum() / 10)
    speech = float(np.median(tot[sp])); g = float(np.median(tot[gap])); glf = float(np.median(lf[gap]))
    return dict(mode=mode, skipped=False, gap_s=round(gap.sum() / 10, 1), speech_s=round(sp.sum() / 10, 1),
                speech=round(speech, 1), gap=round(g, 1), gap_lf=round(glf, 1),
                bed_rel=round(g - speech, 1), lf_rel=round(glf - speech, 1))

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
        if len(seg) < SR * 0.05: rms_db.append(-120); hf_r.append(0); sustained.append(0); continue
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
    # 嗡声 / 间隙
    if not args.no_hum:
        hs = hum_scan(x); res['hum'] = hs
        if hs and hs['tones']:
            tl = '、'.join(f"{t[0]} Hz" for t in hs['tones'][:8])
            msg = f"持续低频单音 {tl}（合计 {hs['total']:.1f} dBFS，比主体 {hs['body']:.1f} dBFS {hs['rel']:+.1f} dB）"
            if hs['mains']:
                msg += f"；其中 {hs['mains']} 落在 50/60 Hz 谐波上，像电源嗡声"
            if hs['rel'] > HUM_REL_P0:
                hits.append((0, dur, 'hum', msg + f'，超过 {HUM_REL_P0:.0f} dB 上限'))
            else:
                res.setdefault('notes', []).append('P1 ' + msg + '：电平低于上限，抽听确认')
    vo = None
    if getattr(args, 'vo', None):
        vo = decode(args.vo, probe(args.vo)['ch']).mean(axis=1).astype(np.float64)
    if kind in ('mix', 'auto') or vo is not None:
        gs = gap_scan(x, vo); res['gaps'] = gs
        if gs and not gs['skipped']:
            if gs['bed_rel'] > BED_REL_P0:
                hits.append((0, dur, 'bed_level', f"停顿处 {gs['gap']:.1f} dBFS，只比说话段 {gs['speech']:.1f} dBFS 低 {-gs['bed_rel']:.1f} dB（要求 ≥{-BED_REL_P0:.0f} dB；停顿判定：{gs['mode']}）"))
            if gs['lf_rel'] > LF_REL_P0:
                hits.append((0, dur, 'lf_bed', f"停顿处 <150 Hz {gs['gap_lf']:.1f} dBFS，只比说话段低 {-gs['lf_rel']:.1f} dB（要求 ≥{-LF_REL_P0:.0f} dB）"))
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

def dehum_chain(hs, gs=None):
    """根据 hum_scan / gap_scan 结果生成补救滤镜链：高通 + 每个稳态单音一个陷波。"""
    parts = []
    if hs and hs['tones']:
        parts.append('highpass=f=90:poles=2')
        for f0, _, lvl in hs['tones']:
            g = float(np.clip((hs['body'] - 35) - lvl, -40, -6))
            parts.append(f'equalizer=f={f0}:t=q:w=6:g={g:.0f}')
    if gs and not gs.get('skipped') and gs['lf_rel'] > LF_REL_P0:
        parts += ['highpass=f=120:poles=2', 'highpass=f=120:poles=2']
    return ','.join(parts)

def _loudnorm_measure(path, pre=''):
    af = (pre + ',' if pre else '') + 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json'
    t = sh(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-vn', '-af', af, '-f', 'null', '-']).stderr
    return json.loads(t[t.rindex('{'): t.rindex('}') + 1])

def fix_hum(src, dst, args):
    info = probe(src)
    if not info:
        print(f'hyperlens: {src} 没有音轨', file=sys.stderr); return 1
    x = decode(src, info['ch']).mean(axis=1).astype(np.float64)
    hs = hum_scan(x); gs = gap_scan(x)
    chain = dehum_chain(hs, gs)
    gtxt = '跳过（没有足够的说话/停顿）' if (not gs or gs.get('skipped')) else f"停顿比说话低 {-gs['bed_rel']:.1f} dB，停顿 <150 Hz 比说话低 {-gs['lf_rel']:.1f} dB"
    print(f'稳态单音 (Hz, TNR dB, dBFS)：{hs["tones"] if hs and hs["tones"] else "无"}；停顿：{gtxt}')
    if hs and hs['tones'] and hs['rel'] > -3:
        print('注意：稳态单音几乎就是全部内容（铺底本身就是主体），陷波后响度归一会把它重新拉高；这种片子要换铺底重混，补救只能当临时版。')
    if not chain:
        print('没有测到持续低频单音或停顿低频偏高，不需要补救。'); return 0
    lufs = args.lufs if args.lufs is not None else -16.0
    tp = min(args.tp if args.tp is not None else -1.5, -1.0)
    print(f'补救滤镜链：{chain}')
    if args.dry_run:
        return 0
    import tempfile
    with tempfile.TemporaryDirectory(prefix='hyperlens-dehum-') as td:
        a = os.path.join(td, 'a.wav')
        if sh(['ffmpeg', '-v', 'error', '-y', '-i', src, '-vn', '-af', chain, '-ar', str(SR), '-c:a', 'pcm_f32le', a]).returncode:
            print('hyperlens: 补救滤镜链执行失败', file=sys.stderr); return 1
        m = _loudnorm_measure(a); gain = lufs - float(m['input_i'])
        if float(m['input_tp']) + gain > tp - 0.3:   # 线性增益会顶破真峰值：先限幅再量，保证第二遍是线性模式
            lim = float(np.clip(10 ** ((tp - gain - 1.0) / 20), 0.0625, 1.0)); b = os.path.join(td, 'b.wav')
            sh(['ffmpeg', '-v', 'error', '-y', '-i', a, '-af', f'alimiter=limit={lim:.4f}:level=0:latency=1:release=80', '-c:a', 'pcm_f32le', b])
            a = b; m = _loudnorm_measure(a)
        ln = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
              f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        has_v = bool(json.loads(sh(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=index', '-of', 'json', src]).stdout or '{}').get('streams'))
        cmd = ['ffmpeg', '-v', 'error', '-y']
        if has_v:
            cmd += ['-i', src, '-i', a, '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-af', ln, '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', dst]
        else:
            cmd += ['-i', a, '-af', ln, '-ar', '48000', dst]
        r = sh(cmd)
        if r.returncode:
            print(f'hyperlens: 写出失败：{r.stderr.strip()}', file=sys.stderr); return 1
    y = decode(dst, probe(dst)['ch']).mean(axis=1).astype(np.float64)
    h2 = hum_scan(y); g2 = gap_scan(y)
    still = (h2 and h2['tones'] and h2['rel'] > HUM_REL_P0) or (g2 and not g2.get('skipped') and (g2['lf_rel'] > LF_REL_P0 or g2['bed_rel'] > BED_REL_P0))
    print(f'已写出 {dst}。复查：python3 {sys.argv[0]} {dst} --kind mix')
    if still:
        print('hyperlens: 补救后仍不达标（嗡声或停顿铺底仍超限），需要回到分轨换铺底/降电平后重混', file=sys.stderr)
        return 2
    return 0

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
    ap.add_argument('--vo', help='旁白分轨（vo.wav 等，和被检文件同一时间轴），用于准确找停顿')
    ap.add_argument('--no-hum', action='store_true', help='不做嗡声检查')
    ap.add_argument('--fix-hum', metavar='OUT', help='只有成片时的补救：测出稳态单音→高通+陷波→两遍线性响度归一，写到 OUT（视频流直接拷贝）')
    ap.add_argument('--dry-run', action='store_true', help='配合 --fix-hum：只打印滤镜链不写文件')
    ap.add_argument('--out', help='频谱图目录（默认 <文件目录>/qc）')
    ap.add_argument('--no-png', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    if args.fix_hum:
        if len(args.files) != 1:
            sys.exit('hyperlens: --fix-hum 一次只处理一个文件')
        sys.exit(fix_hum(args.files[0], args.fix_hum, args))
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
            for nte in r.get('notes', []): print(f'  [提示] {nte}')
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
