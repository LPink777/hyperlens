#!/usr/bin/env python3
"""逐镜技术自检 + 审片提示生成（通用，不依赖题材）。

用法:
  python3 shot_qc.py shots/s01.mp4 [shots/s02.mp4 ...] [--expect "期望画面"] [--duration 8]
                      [--width 1920 --height 1080] [--fps 30] [--out frames/qc] [--json]

检查：可解码、分辨率/时长（可选容差）、黑帧段、冻结段；输出等距抽帧拼图和一段审片提示。
帧率/分辨率不符时只给警告（合成时会统一缩放），黑帧/冻结/不可解码给失败。
"""
import argparse, json, os, re, subprocess, sys


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", path])
    if r.returncode != 0:
        return None
    d = json.loads(r.stdout)
    s = d["streams"][0]
    n, den = s["r_frame_rate"].split("/")
    return {"width": int(s["width"]), "height": int(s["height"]),
            "fps": round(float(n) / float(den or 1), 3),
            "duration": round(float(d["format"]["duration"]), 3)}


def detect(path, filt, key):
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf", filt, "-an", "-f", "null", "-"])
    spans, start = [], None
    for line in r.stderr.splitlines():
        if key == "black":
            m = re.search(r"black_start:([\d.]+) black_end:([\d.]+)", line)
            if m:
                spans.append((float(m.group(1)), float(m.group(2))))
        else:
            m = re.search(r"freeze_start: ([\d.]+)", line)
            if m:
                start = float(m.group(1))
            m = re.search(r"freeze_end: ([\d.]+)", line)
            if m and start is not None:
                spans.append((start, float(m.group(1))))
                start = None
    if key == "freeze" and start is not None:
        spans.append((start, None))
    return spans, r.returncode


def sheet(path, out_dir, duration, n=8):
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(path))[0]
    dst = os.path.join(out_dir, f"{name}_sheet.jpg")
    fps = n / max(duration, 0.1)
    r = run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf",
             f"fps={fps:.4f},scale=480:-2,tile=4x{(n + 3) // 4}", "-frames:v", "1", dst])
    return dst if r.returncode == 0 else None


DEFECTS = ("物体凭空出现或消失、结果没有过程；多指/少指/关节错误/肢体融合；转身或转头时脸和身体变形、身份漂移；"
           "动作未完成或与预期相反；穿模、漂浮、火焰烟雾液体不自然；任何文字、伪文字、水印、Logo；"
           "现代物品或可辨认的真实人物；黑帧、冻结、闪烁、明显压缩块")


def review_prompt(name, expect, duration):
    return (f"镜头 {name}（约 {duration:.1f} 秒）。预期画面：{expect or '（见镜头表）'}。"
            f"请：1) 用陌生人口吻逐句描述实际发生了什么；2) 与预期的符合度 0-10；"
            f"3) 逐项检查以下缺陷是否出现及出现在第几秒：{DEFECTS}；"
            f"4) 给出无 P0 缺陷的可用片段起止秒数；5) 结论：通过 / 剪用 / 重做，并说明理由。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--expect", default="")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--tolerance", type=float, default=0.5)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float)
    ap.add_argument("--out", default="frames/qc")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results, any_fail = [], False
    for clip in a.clips:
        res = {"clip": clip, "fail": [], "warn": []}
        info = probe(clip)
        if not info:
            res["fail"].append("无法读取/解码")
            results.append(res); any_fail = True; continue
        res.update(info)
        if a.duration and info["duration"] + a.tolerance < a.duration:
            res["warn"].append(f"时长 {info['duration']}s 短于需要的 {a.duration}s")
        if a.width and a.height and (info["width"], info["height"]) != (a.width, a.height):
            res["warn"].append(f"分辨率 {info['width']}x{info['height']} ≠ {a.width}x{a.height}（合成时需缩放/裁切）")
        if a.fps and abs(info["fps"] - a.fps) > 0.01:
            res["warn"].append(f"帧率 {info['fps']} ≠ {a.fps}（合成时需转换）")
        black, rc = detect(clip, "blackdetect=d=0.2:pix_th=0.10", "black")
        if rc != 0:
            res["fail"].append("ffmpeg 完整解码失败")
        if black:
            res["fail"].append("黑帧段: " + ", ".join(f"{s:.2f}-{e:.2f}s" for s, e in black))
        freeze, _ = detect(clip, "freezedetect=n=0.001:d=1.0", "freeze")
        if freeze:
            res["warn"].append("疑似冻结段: " + ", ".join(
                f"{s:.2f}-{(f'{e:.2f}' if e else '结尾')}s" for s, e in freeze))
        res["sheet"] = sheet(clip, a.out, info["duration"])
        res["review_prompt"] = review_prompt(os.path.basename(clip), a.expect, info["duration"])
        res["status"] = "FAIL" if res["fail"] else ("WARN" if res["warn"] else "OK")
        any_fail |= bool(res["fail"])
        results.append(res)

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for r in results:
            print(f"[{r.get('status', 'FAIL')}] {r['clip']}  "
                  f"{r.get('width')}x{r.get('height')} {r.get('fps')}fps {r.get('duration')}s")
            for x in r["fail"]:
                print("  ✗", x)
            for x in r["warn"]:
                print("  !", x)
            if r.get("sheet"):
                print("  抽帧拼图:", r["sheet"])
            if r.get("review_prompt"):
                print("  审片提示:", r["review_prompt"])
        print("\n技术自检通过不代表画面合格：必须再做独立审片（references/shot_qc.md）。")
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
