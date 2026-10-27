#!/usr/bin/env python3
"""HTML 合成 → MP4 的确定性逐帧渲染器（hyperlens 自带，原创实现）。

思路参考：HyperFrames（Apache-2.0，按时间轴逐帧 seek 截图）、OneTake / huashu-art-motion
（页面暴露 seek(t)，帧只由时间决定）。本文件为独立重写，未复制上述项目代码。

页面约定（composition contract）：
  window.__meta  = {duration: 秒, width: 1920, height: 1080, fps: 30}   # 可选，命令行参数优先
  window.__seek  = function(t) {...}   # 必需：把画面摆到第 t 秒；可返回 Promise
  window.__ready = Promise             # 可选：字体/图片加载完成后 resolve
禁止：Date.now()、未设种子的 Math.random()、setTimeout/requestAnimationFrame 驱动动画、CSS transition/animation。

用法：
  python3 html_render.py comp.html --out out.mp4                      # 按 __meta 渲染
  python3 html_render.py comp.html --out out.mp4 --size 1080x1920 --fps 30 --duration 3
  python3 html_render.py comp.html --stills 0.5,1.5,2.5 --out stills/ # 只抽静帧
  python3 html_render.py comp.html --out out.mp4 --workers 4 --shutter 4 --audio music.wav
  python3 html_render.py comp.html --out x --check-determinism         # 帧是否只由 t 决定
依赖：Playwright + Chromium、ffmpeg。解释器自动选择（不必手动激活 venv）：
  1. 当前 python 能 import playwright → 直接用；
  2. 否则依次尝试 $VIDEO_STUDIO_PYTHON、~/.venvs/video-studio/bin/python，找到能 import playwright 的就自动切换过去；
  3. 都没有 → 提示安装：
       python3 -m venv ~/.venvs/video-studio
       ~/.venvs/video-studio/bin/pip install playwright
       ~/.venvs/video-studio/bin/python -m playwright install chromium
编码器：libx264 → libopenh264 → 硬件 H.264 → mpeg4，逐个试编码，取第一个可用的（可用 VIDEO_STUDIO_H264_ENCODER 强制）。
"""
import argparse, asyncio, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

VENV_DEFAULT = Path.home() / ".venvs" / "video-studio" / "bin" / "python"


def ensure_playwright():
    """Re-exec under an interpreter that has Playwright, so the script works from plain `python3`."""
    try:
        import playwright  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("_VIDEO_STUDIO_REEXEC"):
        raise SystemExit("找不到 playwright。")
    for cand in [os.environ.get("VIDEO_STUDIO_PYTHON"), str(VENV_DEFAULT)]:
        if not cand or not Path(cand).exists() or os.path.abspath(cand) == os.path.abspath(sys.executable):
            continue
        ok = subprocess.run([cand, "-c", "import playwright"], capture_output=True).returncode == 0
        if ok:
            env = dict(os.environ, _VIDEO_STUDIO_REEXEC="1")
            os.execve(cand, [cand, os.path.abspath(__file__), *sys.argv[1:]], env)
    raise SystemExit(
        "找不到 Playwright。安装一次即可：\n"
        "  python3 -m venv ~/.venvs/video-studio\n"
        "  ~/.venvs/video-studio/bin/pip install playwright\n"
        "  ~/.venvs/video-studio/bin/python -m playwright install chromium\n"
        "或设置 VIDEO_STUDIO_PYTHON 指向已安装 playwright 的 python。")


def pick_encoder():
    forced = os.environ.get("VIDEO_STUDIO_H264_ENCODER")
    names = [forced] if forced else ["libx264", "libopenh264", "h264_nvenc", "h264_qsv", "h264_vaapi", "h264_videotoolbox", "mpeg4"]
    listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    for name in names:
        if f" {name} " not in listed:
            continue
        test = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=128x128:r=24",
                               "-frames:v", "3", "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True)
        if test.returncode != 0:
            continue
        if name == "libx264":
            return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
        if name == "mpeg4":
            return ["-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"]
        return ["-c:v", name, "-b:v", "8M", "-pix_fmt", "yuv420p"]
    raise SystemExit("ffmpeg 里没有可用的视频编码器。")


async def open_page(pw, url, w, h):
    browser = await pw.chromium.launch(args=["--disable-gpu-vsync", "--force-color-profile=srgb",
                                             "--hide-scrollbars", "--font-render-hinting=none"])
    page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(url, wait_until="load")
    await page.evaluate("async () => { if (window.__ready) await window.__ready; if (document.fonts) await document.fonts.ready; }")
    ok = await page.evaluate("typeof window.__seek === 'function'")
    if not ok:
        raise SystemExit("页面没有定义 window.__seek(t)，无法确定性渲染。")
    return browser, page, errors


async def seek_and_shot(page, t):
    await page.evaluate("async (t) => { const r = window.__seek(t); if (r && r.then) await r; }", t)
    return await page.screenshot(type="png", animations="disabled", caret="hide")


async def read_meta(url):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b, page, _ = await open_page(pw, url, 1280, 720)
        meta = await page.evaluate("window.__meta || {}")
        await b.close()
        return meta


async def render_range(url, w, h, fps, sub, f0, f1, out_path, enc):
    """渲染帧 [f0, f1)；sub>1 时每帧在快门内取 sub 个子帧，交给 ffmpeg tmix 平均（运动模糊）。"""
    from playwright.async_api import async_playwright
    in_rate = fps * sub
    vf = f"tmix=frames={sub},framestep={sub}," if sub > 1 else ""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(in_rate),
           "-i", "-", "-vf", f"{vf}scale={w}:{h}:flags=lanczos,format=yuv420p", "-r", str(fps), *enc, str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    async with async_playwright() as pw:
        b, page, errors = await open_page(pw, url, w, h)
        for f in range(f0, f1):
            for s in range(sub):
                # 180° 快门：子帧分布在帧时间后的半个帧间隔内
                t = f / fps + (s / sub) * (0.5 / fps if sub > 1 else 0)
                proc.stdin.write(await seek_and_shot(page, t))
        await b.close()
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg 编码失败：{out_path}")
    return errors


def worker(args):
    return asyncio.run(render_range(*args))


def main():
    ensure_playwright()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", help="WxH，例如 1080x1920")
    ap.add_argument("--fps", type=int)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--workers", type=int, default=1, help="并行浏览器数（帧只由 t 决定才安全）")
    ap.add_argument("--shutter", type=int, default=1, help="每帧子帧数，>1 开启运动模糊（耗时 ×N）")
    ap.add_argument("--audio", help="混入的音频文件（按视频时长截断）")
    ap.add_argument("--stills", help="逗号分隔的秒数，只导出 PNG 静帧到 --out 目录")
    ap.add_argument("--check-determinism", action="store_true",
                    help="正序、倒序各 seek 一遍抽样时间点，逐字节比对截图；不一致即非零退出")
    a = ap.parse_args()

    url = Path(a.html).resolve().as_uri()
    meta = asyncio.run(read_meta(url))
    w, h = (map(int, a.size.lower().split("x")) if a.size else (meta.get("width", 1920), meta.get("height", 1080)))
    fps = a.fps or int(meta.get("fps", 30))
    dur = a.duration or float(meta.get("duration", 0))

    if a.check_determinism:
        from playwright.async_api import async_playwright
        times = [round(dur * i / 7, 3) for i in range(7)] if dur > 0 else [0, 0.5, 1, 2]
        async def det():
            async with async_playwright() as pw:
                b, page, _ = await open_page(pw, url, w, h)
                fwd = {t: await seek_and_shot(page, t) for t in times}
                bad = [t for t in reversed(times) if await seek_and_shot(page, t) != fwd[t]]
                await b.close()
                return bad
        bad = asyncio.run(det())
        print(json.dumps({"deterministic": not bad, "mismatch_times": bad}, ensure_ascii=False))
        sys.exit(1 if bad else 0)

    if a.stills:
        from playwright.async_api import async_playwright
        outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
        async def shots():
            async with async_playwright() as pw:
                b, page, _ = await open_page(pw, url, w, h)
                for t in [float(x) for x in a.stills.split(",")]:
                    (outdir / f"still_{t:06.2f}.png").write_bytes(await seek_and_shot(page, t))
                await b.close()
        asyncio.run(shots()); print(f"静帧已写入 {outdir}"); return

    if dur <= 0:
        raise SystemExit("没有时长：在 window.__meta.duration 里写，或传 --duration。")
    n = round(dur * fps)
    enc = pick_encoder()
    tmp = Path(tempfile.mkdtemp(prefix="html_render_"))
    k = max(1, min(a.workers, n))
    bounds = [(i * n // k, (i + 1) * n // k) for i in range(k)]
    jobs = [(url, w, h, fps, max(1, a.shutter), f0, f1, tmp / f"seg{i:02d}.mp4", enc) for i, (f0, f1) in enumerate(bounds)]
    t0 = time.time()
    if k == 1:
        errors = worker(jobs[0])
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(k) as ex:
            errors = sum(ex.map(worker, jobs), [])
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{j[7]}'\n" for j in jobs))
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if a.audio:
        cmd += ["-i", a.audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    report = {"out": str(out), "size": f"{w}x{h}", "fps": fps, "frames": n, "duration": n / fps,
              "workers": k, "shutter": a.shutter, "seconds": round(time.time() - t0, 1), "page_errors": errors}
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        print("警告：页面有 JS 报错，检查画面。", file=sys.stderr)


if __name__ == "__main__":
    main()
