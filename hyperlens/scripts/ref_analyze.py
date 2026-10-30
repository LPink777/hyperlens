#!/usr/bin/env python3
"""参考片拆解：自动量出能量化的部分，生成风格说明草稿（通用，不依赖题材）。

用法:
  python3 ref_analyze.py ref.mp4 [--out ref/analysis] [--threshold 0.3] [--colors 6] [--frames 12]
  python3 ref_analyze.py ref1.jpg ref2.png ... --out ref/analysis        # 参考图：只做色板和明度/饱和度

视频输出：
  - 镜头切分（ffmpeg select scene 分数）：切点、每镜时长、平均/中位镜头长度、每分钟切换次数；
  - 代表帧（每镜中点，最多 --frames 张）和拆解拼图 <name>_board.jpg；
  - 主色板（k-means，HEX + 占比）和色板图 <name>_palette.png；
  - 亮度（Y 均值/对比度）、饱和度（HSV S 均值）、冷暖倾向；
  - <name>_breakdown.json + <name>_风格说明草稿.md（字体、视线落点、动作手感等留给人或审阅者填）。
只依赖 Python 标准库和 ffmpeg/ffprobe；脚本不判断“好不好看”，只给数字。
规则：学感觉，不照搬人物、Logo、文案和具体构图。
"""
import argparse, colorsys, json, math, os, random, re, subprocess, sys

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def run(cmd):
    return subprocess.run(cmd, capture_output=True)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", path])
    if r.returncode != 0:
        sys.exit(f"ref_analyze: 无法读取 {path}")
    d = json.loads(r.stdout)
    s = d["streams"][0]
    n, den = s["r_frame_rate"].split("/")
    return {"width": int(s["width"]), "height": int(s["height"]),
            "fps": round(float(n) / float(den or 1), 3),
            "duration": float(d.get("format", {}).get("duration") or 0)}


def scene_cuts(path, threshold):
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-vf",
             f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    cuts = [float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", r.stderr.decode("utf-8", "ignore"))]
    out = []
    for c in cuts:  # 合并 0.2 秒内的连续触发（闪白、快速叠化）
        if not out or c - out[-1] > 0.2:
            out.append(round(c, 3))
    return out


def rgb_pixels(path, t=None, w=96):
    cmd = ["ffmpeg", "-v", "error"]
    if t is not None:
        cmd += ["-ss", f"{t:.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale={w}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = run(cmd).stdout
    return [tuple(data[i:i + 3]) for i in range(0, len(data) - 2, 3)]


def kmeans(pixels, k, iters=12, seed=7):
    rnd = random.Random(seed)
    pts = pixels if len(pixels) <= 6000 else rnd.sample(pixels, 6000)
    # k-means++ 初始化
    centers = [rnd.choice(pts)]
    while len(centers) < k:
        d2 = [min(sum((a - b) ** 2 for a, b in zip(p, c)) for c in centers) for p in pts]
        tot = sum(d2) or 1
        r, acc = rnd.random() * tot, 0
        for p, d in zip(pts, d2):
            acc += d
            if acc >= r:
                centers.append(p); break
        else:
            centers.append(rnd.choice(pts))
    for _ in range(iters):
        groups = [[] for _ in centers]
        for p in pts:
            i = min(range(len(centers)), key=lambda j: sum((a - b) ** 2 for a, b in zip(p, centers[j])))
            groups[i].append(p)
        centers = [tuple(sum(c[x] for c in g) / len(g) for x in range(3)) if g else centers[i] for i, g in enumerate(groups)]
    total = sum(len(g) for g in groups) or 1
    pal = [{"hex": "#%02X%02X%02X" % tuple(int(round(v)) for v in c), "share": round(len(g) / total, 3),
            "rgb": [int(round(v)) for v in c]} for c, g in zip(centers, groups) if g]
    return sorted(pal, key=lambda x: -x["share"])


def tone_stats(pixels):
    ys, ss, warm = [], [], 0
    for r, g, b in pixels:
        ys.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
        _, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        ss.append(s)
        warm += 1 if r > b + 8 else (-1 if b > r + 8 else 0)
    n = len(ys) or 1
    mean = sum(ys) / n
    sd = math.sqrt(sum((y - mean) ** 2 for y in ys) / n)
    return {"luma_mean": round(mean, 1), "luma_contrast_sd": round(sd, 1), "saturation_mean": round(sum(ss) / n, 3),
            "warm_cool": round(warm / n, 3),
            "luma_label": "暗调" if mean < 85 else ("亮调" if mean > 170 else "中间调"),
            "sat_label": "低饱和" if sum(ss) / n < 0.25 else ("高饱和" if sum(ss) / n > 0.55 else "中饱和"),
            "temp_label": "偏暖" if warm / n > 0.15 else ("偏冷" if warm / n < -0.15 else "中性")}


def palette_png(palette, dst):
    w, h = 720, 160
    rows, x0 = bytearray(), []
    acc = 0
    for p in palette:
        x0.append((int(acc * w), p["rgb"]))
        acc += p["share"]
    for _ in range(h):
        row = bytearray()
        for x in range(w):
            rgb = x0[0][1]
            for start, c in x0:
                if x >= start:
                    rgb = c
            row += bytes(rgb)
        rows += row
    ppm = dst + ".ppm"
    with open(ppm, "wb") as f:
        f.write(b"P6 %d %d 255\n" % (w, h) + bytes(rows))
    run(["ffmpeg", "-y", "-v", "error", "-i", ppm, dst])
    os.remove(ppm)
    return dst


def board(path, times, dst, cols=4):
    tmp = dst + "_frames"
    os.makedirs(tmp, exist_ok=True)
    for i, t in enumerate(times):
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
             "-vf", "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2:color=black",
             os.path.join(tmp, f"{i:03d}.png")])
    rows = max(1, math.ceil(len(times) / cols))
    run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", os.path.join(tmp, "%03d.png"),
         "-vf", f"tile={cols}x{rows}:padding=4:color=white", "-frames:v", "1", dst])
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    return dst if os.path.exists(dst) else None


TODO = [
    ("字体风格", "衬线/无衬线/手写/等宽；字重；标题与正文的大小字号比（例：标题字高 9% 画面高，正文 3.5%，比约 2.6:1）"),
    ("第一眼视线落点", "每张代表帧第一眼看哪里（主体/大字/强调色）；靠什么引导（对比、大小、留白、运动）"),
    ("动作手感", "干脆（快进快停）/ 滑动（匀缓）/ 回弹（过冲后回位）；小元素与大元素是否不同；有无运动模糊"),
    ("画面质感", "颗粒、锐度、景深、暗角、光源方向、材质（纸、玻璃、胶片、屏幕录制）"),
    ("镜头语言", "常用景别和运镜；转场方式（硬切/承接/叠化）；是否卡拍"),
    ("声音", "有无旁白、音乐风格与 BPM（可用 beat_map.py 量）、音效密度"),
    ("学什么 / 不拿什么", "列 3 条要学的感觉；列明不照搬的人物、Logo、文案、具体构图"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="参考视频，或一张/多张参考图")
    ap.add_argument("--out", default="ref/analysis")
    ap.add_argument("--threshold", type=float, default=0.3, help="scene 切分阈值 0–1，越小越敏感（默认 0.3）")
    ap.add_argument("--colors", type=int, default=6)
    ap.add_argument("--frames", type=int, default=12, help="代表帧上限")
    ap.add_argument("--json", action="store_true", help="只在 stdout 打印 JSON")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    first = a.inputs[0]
    name = os.path.splitext(os.path.basename(first))[0]
    is_image = all(os.path.splitext(p)[1].lower() in IMAGE_EXT for p in a.inputs)
    res = {"inputs": a.inputs, "kind": "image" if is_image else "video"}
    pixels = []
    if is_image:
        for p in a.inputs:
            pixels += rgb_pixels(p)
        rep_times = []
    else:
        info = probe(first)
        res.update(info)
        cuts = scene_cuts(first, a.threshold)
        bounds = [0.0] + cuts + [info["duration"]]
        shots = [round(b - s, 3) for s, b in zip(bounds, bounds[1:]) if b - s > 0.05]
        srt = sorted(shots)
        res["cuts"] = cuts
        res["shot_lengths"] = shots
        res["shot_count"] = len(shots)
        res["avg_shot_len"] = round(sum(shots) / len(shots), 2) if shots else None
        res["median_shot_len"] = srt[len(srt) // 2] if srt else None
        res["cuts_per_min"] = round(len(cuts) / (info["duration"] / 60), 1) if info["duration"] else None
        mids = [round((s + b) / 2, 3) for s, b in zip(bounds, bounds[1:]) if b - s > 0.05]
        if len(mids) > a.frames:
            step = len(mids) / a.frames
            mids = [mids[int(i * step)] for i in range(a.frames)]
        rep_times = mids
        for t in rep_times:
            pixels += rgb_pixels(first, t)
        res["rep_times"] = rep_times
        res["board"] = board(first, rep_times, os.path.join(a.out, f"{name}_board.jpg"))
    pal = kmeans(pixels, a.colors)
    res["palette"] = [{"hex": p["hex"], "share": p["share"]} for p in pal]
    res["palette_png"] = palette_png(pal, os.path.join(a.out, f"{name}_palette.png"))
    res["tone"] = tone_stats(pixels)
    res["todo_for_reviewer"] = [{"item": k, "hint": v} for k, v in TODO]
    jpath = os.path.join(a.out, f"{name}_breakdown.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    t = res["tone"]
    md = [f"# 风格说明草稿：{name}", "",
          "> 由 ref_analyze.py 自动量出数字部分；带【待填】的由人或审阅者看拆解拼图后填写。学感觉，不照搬人物、Logo、文案。", "",
          "## 自动量出", ""]
    if not is_image:
        md += [f"- 源：{res['width']}×{res['height']} @ {res['fps']}fps，{res['duration']:.1f} 秒",
               f"- 镜头：{res['shot_count']} 镜，平均 {res['avg_shot_len']} 秒，中位 {res['median_shot_len']} 秒，每分钟切 {res['cuts_per_min']} 次（scene 阈值 {a.threshold}；叠化/一镜到底会被少算，看拼图复核）",
               f"- 拆解拼图：`{res['board']}`"]
    md += [f"- 主色板（占比）：" + "、".join(f"`{p['hex']}` {p['share']*100:.0f}%" for p in res["palette"]),
           f"- 色板图：`{res['palette_png']}`",
           f"- 明度：Y 均值 {t['luma_mean']}/255（{t['luma_label']}），对比度 SD {t['luma_contrast_sd']}；饱和度均值 {t['saturation_mean']}（{t['sat_label']}）；{t['temp_label']}",
           "", "## 待填（看拼图/原片后写具体值，禁止“高级感”“有质感”这类虚词）", ""]
    md += [f"- **{k}**：【待填】{v}" for k, v in TODO]
    md += ["", "## 并入画风锁", "", "- 画风：【待填，一句话】", "- 色板：从上面 HEX 里选 3–6 个，写明底色 / 墨色 / 唯一强调色",
           "- MOTION：按元素类别写时长和缓动（见 references/html_motion.md 第 6 节）", "- TIME：平均镜头长度作为节奏参考，不必一致"]
    mpath = os.path.join(a.out, f"{name}_风格说明草稿.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    res["draft_md"] = mpath
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("\n".join(line for line in md[4:] if line.startswith("- ") and "【待填" not in line and "从上面" not in line and "MOTION" not in line and "TIME" not in line))
        print(f"\nJSON：{jpath}\n草稿：{mpath}")
        print("审阅提示：看拆解拼图，补全【待填】项（字体与字号比、第一眼视线落点、动作手感、质感）；写具体数值和色号，不写虚词；列出学什么 / 不拿什么。")


if __name__ == "__main__":
    main()
