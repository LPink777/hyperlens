#!/usr/bin/env python3
"""全片缩略图看板：在关键节点各截一帧，拼成带时间标注的看板 + 手机尺寸版，并打印审阅提示（通用）。

用法:
  python3 storyboard_board.py --project project.json [--aspect 9:16] [--preview] [--video 指定视频] [--key s03,s07]
  python3 storyboard_board.py output/final.mp4 --cuts 5.87,9.87,15.87 [--ids s01,s02,s03,s04]
  python3 storyboard_board.py output/final.mp4 --cuts-json work/timeline.json     # {"cuts": [[id,start,end],...]}

取帧：第 0 / 1 / 2 秒、每镜中点、关键镜头再加 25% 与 75%、每个切点（转场中间帧）、最后一帧。
输出（默认 frames/board/）：board.jpg（看板）、board_phone.jpg（每格按手机竖屏显示宽度 360px，查字是否可读）、
board.json（每格的序号、时间、镜头、类型）。
--project 时切点由 project.json 的镜头时长算出；默认读 output.path，--preview 读 build --preview 的低清预览。
看板不代替完整观看；按审阅提示只挑最严重的 3 个问题，标清第几秒和怎么改，只重做/重渲这几段。
"""
import argparse, json, math, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

QUESTIONS = [
    "缩到手机大小（看 board_phone.jpg），屏上字和字幕还看得清吗？哪一格看不清？",
    "界面、数据、截图、Logo 是不是真的、有来源？有没有编造的界面或数字？",
    "转场格（类型=转场）有没有糊、重影、半透明叠加看不懂、主体出画或黑场？",
    "前 2 秒（0s/1s/2s 三格）有没有钩子：一个具体的物在做事、一个问题或反差，而不是标题、Logo 或黑场？",
    "最后一帧能不能直接当封面：主体清楚、构图完整、不是黑场或半个转场？",
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)])
    if r.returncode != 0:
        sys.exit(f"storyboard_board: 无法读取 {path}")
    d = json.loads(r.stdout); s = d["streams"][0]
    n, den = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(n) / float(den or 1), float(d["format"]["duration"])


def font_file():
    sys.path.insert(0, str(HERE))
    try:
        from video_pipeline import find_font
        return find_font(bold=True)
    except Exception:
        return None


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}"


def from_project(args):
    sys.path.insert(0, str(HERE))
    from video_pipeline import load_project, validate_project, resolve
    project = load_project(args.project, args.aspect, args.preview)
    report = validate_project(project)
    if report["errors"]:
        sys.exit("storyboard_board: project.json 有错误：" + "; ".join(report["errors"]))
    video = Path(args.video) if args.video else resolve(project["_base"], project["output"]["path"])
    if not video.is_file():
        hint = "--preview " if args.preview else ""
        sys.exit(f"storyboard_board: 还没有 {video}。先出低清预览：python3 scripts/video_pipeline.py build {args.project} --preview"
                 + (f" --aspect {args.aspect}" if args.aspect else "") + f"，再跑本脚本加 {hint or '--preview '}")
    keys = {s.get("id") for s in project["shots"] if s.get("key") or s.get("关键") in ("是", True)}
    shots, t = [], 0.0
    for s in report["shots"]:
        shots.append((s["id"], t, t + s["duration"])); t += s["duration"]
    out = args.out or str(project["_base"] / "frames" / "board" / (project["output"].get("variant") or "main").replace(":", "x"))
    return video, shots, keys, out


def from_video(args):
    video = Path(args.video_pos)
    _, _, _, dur = probe(video)
    if args.cuts_json:
        d = json.loads(Path(args.cuts_json).read_text(encoding="utf-8"))
        rows = d.get("cuts", d) if isinstance(d, dict) else d
        shots = []
        for i, r in enumerate(rows):
            if isinstance(r, (list, tuple)) and len(r) >= 3:
                shots.append((str(r[0]), float(r[1]), float(r[2])))
            else:
                shots = None; break
        if shots is None:
            cuts = [float(x) for x in rows]
            b = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
            shots = [(f"s{i+1:02d}", a, c) for i, (a, c) in enumerate(zip(b, b[1:]))]
    else:
        cuts = [float(x) for x in (args.cuts or "").split(",") if x.strip()]
        b = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
        ids = args.ids.split(",") if args.ids else [f"s{i+1:02d}" for i in range(len(b) - 1)]
        shots = [(ids[i] if i < len(ids) else f"s{i+1:02d}", a, c) for i, (a, c) in enumerate(zip(b, b[1:]))]
    return video, shots, set(), args.out or "frames/board"


def plan_points(shots, keys, duration, fps):
    pts = []
    def shot_at(t):
        for sid, a, b in shots:
            if a <= t < b:
                return sid
        return shots[-1][0]
    for t, kind in ((0.0, "开头0s"), (1.0, "开头1s"), (2.0, "开头2s")):
        if t < duration:
            pts.append((t, kind, shot_at(t)))
    for i, (sid, a, b) in enumerate(shots):
        is_key = sid in keys
        pts.append(((a + b) / 2, "关键中点" if is_key else "中点", sid))
        if is_key:
            pts += [(a + (b - a) * 0.25, "关键25%", sid), (a + (b - a) * 0.75, "关键75%", sid)]
        if i > 0:
            pts.append((a, "转场", f"{shots[i-1][0]}→{sid}"))
    pts.append((max(0.0, duration - 1.5 / fps), "最后一帧", shots[-1][0]))
    pts.sort(key=lambda p: p[0])
    out = []
    for p in pts:  # 0.05 秒内去重，保留更重要的类型
        if out and abs(p[0] - out[-1][0]) < 0.05:
            if p[1] in ("转场", "最后一帧", "开头0s"):
                out[-1] = p
            continue
        out.append(p)
    return out


def render(video, points, out_dir, w, h, cell_w, font, cols, tag):
    cell_h = round(cell_w * h / w / 2) * 2
    bar = max(22, round(cell_w * 0.065))
    tmp = Path(tempfile.mkdtemp(prefix="board-"))
    for i, (t, kind, sid) in enumerate(points, 1):
        label = tmp / f"{i:03d}.txt"
        label.write_text(f"#{i:02d} {fmt(t)} {sid} {kind}", encoding="utf-8")
        draw = (f",pad={cell_w}:{cell_h + bar}:0:{bar}:color=0x111111,"
                f"drawtext=expansion=none:textfile='{label}':x=8:y=({bar}-th)/2:fontsize={round(bar*0.62)}:fontcolor=white"
                + (f":fontfile='{font}'" if font else ""))
        r = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                 f"scale={cell_w}:{cell_h}" + draw, str(tmp / f"{i:03d}.png")])
        if r.returncode != 0:
            # 标注失败（无 drawtext/字体）时退回无标注
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                 f"scale={cell_w}:{cell_h},pad={cell_w}:{cell_h + bar}:0:{bar}:color=0x111111", str(tmp / f"{i:03d}.png")])
    rows = math.ceil(len(points) / cols)
    dst = Path(out_dir) / f"{tag}.jpg"
    r = run(["ffmpeg", "-y", "-v", "error", "-framerate", "1", "-i", str(tmp / "%03d.png"), "-vf",
             f"tile={cols}x{rows}:padding=6:margin=6:color=white", "-frames:v", "1", "-q:v", "3", str(dst)])
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0:
        sys.exit(f"storyboard_board: 拼图失败 {r.stderr[:300]}")
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_pos", nargs="?", help="视频文件（不用 --project 时）")
    ap.add_argument("--project", help="project.json：切点由镜头时长算出")
    ap.add_argument("--aspect", help="配合 --project：看某个画幅变体（output.variants 的键或预设）")
    ap.add_argument("--preview", action="store_true", help="配合 --project：读 build --preview 出的低清预览")
    ap.add_argument("--video", help="配合 --project：显式指定要看的视频")
    ap.add_argument("--cuts", help="逗号分隔的切点秒数")
    ap.add_argument("--ids", help="逗号分隔的镜头 ID（与 --cuts 对应，n 个切点 = n+1 个 ID）")
    ap.add_argument("--cuts-json", help="含 cuts 的 JSON：[[id,start,end],...] 或切点秒数列表")
    ap.add_argument("--key", default="", help="逗号分隔的关键镜头 ID（多取 25%%/75%% 两帧）")
    ap.add_argument("--out", help="输出目录（默认 frames/board/<画幅>）")
    ap.add_argument("--cell", type=int, help="看板每格宽度像素（默认横屏 480 / 竖屏 300）")
    ap.add_argument("--phone-width", type=int, default=360, help="手机版每格宽度（竖屏手机显示宽度，默认 360）")
    ap.add_argument("--no-phone", action="store_true")
    a = ap.parse_args()
    if a.project:
        video, shots, keys, out = from_project(a)
    elif a.video_pos:
        video, shots, keys, out = from_video(a)
    else:
        ap.error("需要视频文件或 --project")
    keys |= {k for k in a.key.split(",") if k}
    w, h, fps, dur = probe(video)
    points = plan_points(shots, keys, dur, fps)
    Path(out).mkdir(parents=True, exist_ok=True)
    font = font_file()
    vertical = h > w
    cell = a.cell or (300 if vertical else 480)
    cols = 8 if vertical else 5
    board = render(video, points, out, w, h, cell, font, cols, "board")
    phone = None if a.no_phone else render(video, points, out, w, h, min(a.phone_width, w), font, 6 if vertical else 4, "board_phone")
    meta = {"video": str(video), "size": f"{w}x{h}", "fps": round(fps, 3), "duration": round(dur, 3), "board": str(board),
            "board_phone": str(phone) if phone else None,
            "cells": [{"n": i, "t": round(t, 3), "time": fmt(t), "shot": sid, "kind": kind} for i, (t, kind, sid) in enumerate(points, 1)],
            "questions": QUESTIONS}
    (Path(out) / "board.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"看板：{board}（{len(points)} 格）")
    if phone:
        print(f"手机版：{phone}")
    print(f"格子清单：{Path(out) / 'board.json'}")
    print("\n审阅提示（交给没参与制作的审阅者，按格号回答）：")
    for i, q in enumerate(QUESTIONS, 1):
        print(f"  {i}. {q}")
    print("  然后只挑最严重的 3 个问题，每个写：格号 / 第几秒 / 哪个镜头 / 问题 / 怎么改（重做、改字号、换帧、改转场）。"
          "\n  只重做和重渲这几段，不整片重来；改完重出预览和看板复查这 3 处。看板不代替带声音完整看一遍。")


if __name__ == "__main__":
    main()
