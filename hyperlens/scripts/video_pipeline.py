#!/usr/bin/env python3
"""Topic-agnostic video assembly: titles, images, videos, subtitles and music.

Only Python's standard library, ffmpeg and ffprobe are required.

Environment overrides:
  VIDEO_STUDIO_H264_ENCODER   force an encoder (libx264, libopenh264, h264_nvenc ...)
  VIDEO_STUDIO_FONT_REGULAR   path to a CJK regular font file
  VIDEO_STUDIO_FONT_BOLD      path to a CJK bold font file
  VIDEO_STUDIO_FONT_FAMILY    font family name for subtitles (default: Noto Sans CJK SC)
"""
import argparse
import functools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FONT_FAMILY = os.environ.get("VIDEO_STUDIO_FONT_FAMILY", "Noto Sans CJK SC")
_REGULAR_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_BOLD_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]

ASPECT_PRESETS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "9:16-hd": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

# Preferred order. Software encoders first because hardware ones are often
# listed by ffmpeg but unusable without a GPU/driver.
_ENCODER_PREFERENCE = ["libx264", "libopenh264", "h264_nvenc", "h264_qsv", "h264_vaapi", "h264_videotoolbox", "h264_v4l2m2m"]


def _fc_match(pattern):
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    return out if out and Path(out).is_file() else None


def find_font(bold=False):
    env = os.environ.get("VIDEO_STUDIO_FONT_BOLD" if bold else "VIDEO_STUDIO_FONT_REGULAR")
    if env and Path(env).is_file():
        return env
    for candidate in (_BOLD_CANDIDATES if bold else []) + _REGULAR_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = _fc_match(f"{FONT_FAMILY}:style={'Bold' if bold else 'Regular'}") or _fc_match("sans-serif:lang=zh-cn")
    if found:
        return found
    raise RuntimeError("no CJK font found; install fonts-noto-cjk (Debian/Ubuntu) or set VIDEO_STUDIO_FONT_REGULAR")


@functools.lru_cache(maxsize=1)
def pick_h264_encoder():
    forced = os.environ.get("VIDEO_STUDIO_H264_ENCODER")
    if forced:
        return forced
    listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    for name in _ENCODER_PREFERENCE:
        if f" {name} " not in listed:
            continue
        test = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=128x128:r=24",
             "-frames:v", "3", "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        if test.returncode == 0:
            return name
    raise RuntimeError("no working H.264 encoder found in ffmpeg (tried: " + ", ".join(_ENCODER_PREFERENCE) + ")")


def encoder_args(bitrate="8M"):
    name = pick_h264_encoder()
    if name == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", name, "-b:v", bitrate, "-pix_fmt", "yuv420p"]


def run(command):
    subprocess.run(command, check=True)


def load_project(path):
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as handle:
        project = json.load(handle)
    if not isinstance(project.get("shots"), list) or not project["shots"]:
        raise ValueError("project.json requires a non-empty shots array")
    project.setdefault("project", path.stem)
    out = project.setdefault("output", {})
    preset = out.get("aspect")
    if preset:
        if preset not in ASPECT_PRESETS:
            raise ValueError(f"unknown output.aspect {preset}; use one of {', '.join(ASPECT_PRESETS)}")
        out.setdefault("width", ASPECT_PRESETS[preset][0])
        out.setdefault("height", ASPECT_PRESETS[preset][1])
    out.setdefault("width", 1280)
    out.setdefault("height", 720)
    out.setdefault("fps", 24)
    out.setdefault("path", "output/final.mp4")
    if out["width"] % 2 or out["height"] % 2:
        raise ValueError("output width/height must be even numbers for yuv420p H.264")
    vertical = out["height"] > out["width"]
    short_edge = min(out["width"], out["height"])
    subs = project.setdefault("subtitles", {})
    subs.setdefault("burn_in", False)
    # font_size / margins are in output pixels (an ASS file is generated at output resolution).
    subs.setdefault("font_size", max(20, round(short_edge * (0.05 if vertical else 0.045))))
    subs.setdefault("margin_v", round(out["height"] * (0.14 if vertical else 0.07)))
    subs.setdefault("margin_h", round(out["width"] * 0.07))
    subs.setdefault("outline", max(2, round(subs["font_size"] * 0.08)))
    subs.setdefault("box", False)
    project.setdefault("audio", {})
    project["audio"].setdefault("loudnorm", True)
    project["_manifest_path"] = path
    project["_base"] = path.parent
    return project


def resolve(base, value):
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def shot_duration(shot, base):
    kind = shot.get("type", "video")
    if "duration" in shot:
        duration = float(shot["duration"])
    elif kind == "video":
        info = probe(resolve(base, shot["source"]))
        duration = float(info["format"]["duration"])
    else:
        duration = 3.0
    if not 0 < duration <= 600:
        raise ValueError(f"invalid shot duration: {duration}")
    return duration


def validate_project(project, require_sources=True):
    base = project["_base"]
    errors, shots = [], []
    total = 0.0
    ids = set()
    for index, shot in enumerate(project["shots"], 1):
        kind = shot.get("type", "video")
        shot_id = shot.get("id", f"shot-{index:03d}")
        if shot_id in ids:
            errors.append(f"duplicate shot id: {shot_id}")
        ids.add(shot_id)
        if kind not in {"video", "image", "title"}:
            errors.append(f"{shot_id}: unsupported type {kind}")
            continue
        if kind in {"video", "image"}:
            if not shot.get("source"):
                errors.append(f"{shot_id}: source is required")
                continue
            source = resolve(base, shot["source"])
            if require_sources and not source.is_file():
                errors.append(f"{shot_id}: missing source {source}")
                continue
        try:
            duration = shot_duration(shot, base)
        except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
            errors.append(f"{shot_id}: {exc}")
            continue
        shots.append({"id": shot_id, "type": kind, "duration": duration, "subtitle": shot.get("subtitle", "")})
        total += duration
    music = project.get("music")
    if music and require_sources and not resolve(base, music["source"]).is_file():
        errors.append(f"music: missing source {resolve(base, music['source'])}")
    return {"project": project["project"], "duration": round(total, 3), "shots": shots, "errors": errors}


def srt_time(seconds):
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def ass_time(seconds):
    cs = int(round(seconds * 100))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _char_width(ch):
    # CJK / full-width ≈ 1 em, ASCII ≈ 0.55 em
    return 1.0 if ord(ch) > 0x2E7F or ch in "，。！？；：、（）“”‘’《》" else 0.55


_BREAK_AFTER = set("，。！？；：、）”’》,.!?;:/ ")
_NO_LINE_START = set("，。！？；：、）”’》,.!?;:")


def _tokens(paragraph):
    tokens, buf = [], ""
    for ch in paragraph:  # keep ASCII words/flags together, e.g. "--computer-use"
        if ord(ch) < 128 and not ch.isspace():
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    return tokens


def _greedy(tokens, limit):
    lines, line, width, last_break = [], [], 0.0, -1
    for tok in tokens:
        w = sum(_char_width(c) for c in tok)
        if line and width + w > limit and tok not in _NO_LINE_START:
            # prefer breaking after punctuation/space if it is not too early in the line
            if last_break >= 0 and sum(_char_width(c) for t in line[:last_break + 1] for c in t) >= limit * 0.6:
                carry = line[last_break + 1:]
                line = line[:last_break + 1]
            else:
                carry = []
            lines.append("".join(line).strip())
            line = list(carry) + ([] if (tok.isspace() and not carry) else [tok])
            width = sum(_char_width(c) for t in line for c in t)
            last_break = -1
            for i, t in enumerate(line):
                if t[-1] in _BREAK_AFTER:
                    last_break = i
            continue
        line.append(tok)
        width += w
        if tok[-1] in _BREAK_AFTER:
            last_break = len(line) - 1
    if line:
        lines.append("".join(line).strip())
    return [l for l in lines if l]


def wrap_text(text, max_em):
    """CJK-aware wrapping: each line <= max_em ems, lines balanced (no 1-char orphans).

    Keeps explicit newlines. ASCII words are never split.
    """
    result = []
    for paragraph in str(text).split("\n"):
        tokens = _tokens(paragraph)
        total = sum(_char_width(c) for t in tokens for c in t)
        if total <= max_em:
            result.append(paragraph.strip())
            continue
        best = None
        for n in (math.ceil(total / max_em), math.ceil(total / max_em) + 1):
            best_score = None
            target = total / n
            # candidates from "balanced" to "hard limit"; prefer breaks at punctuation, then balance
            while target <= max_em + 1e-6:
                candidate = _greedy(tokens, min(target + 0.5, max_em))
                if len(candidate) <= n:
                    widths = [sum(_char_width(c) for c in l) for l in candidate]
                    punct = sum(1 for l in candidate[:-1] if l[-1] in _BREAK_AFTER)
                    score = punct * 10 - (max(widths) - min(widths))
                    if best_score is None or score > best_score:
                        best, best_score = candidate, score
                target += 0.5
            if best:
                break
        lines = best
        result.extend(lines or _greedy(tokens, max_em))
    return [l for l in result if l] or [""]


def subtitle_cues(project):
    report = validate_project(project)
    if report["errors"]:
        raise ValueError("; ".join(report["errors"]))
    cues, cursor = [], 0.0
    for shot in report["shots"]:
        end = cursor + shot["duration"]
        text = str(shot["subtitle"]).strip()
        if text:
            cues.append((cursor, end, text))
        cursor = end
    return cues


def create_srt(project, path=None):
    subs, out = project["subtitles"], project["output"]
    max_em = max(4, (out["width"] - 2 * subs["margin_h"]) / subs["font_size"])
    lines, index = [], 1
    for start, end, text in subtitle_cues(project):
        lines.extend([str(index), f"{srt_time(start)} --> {srt_time(end)}", "\n".join(wrap_text(text, max_em)), ""])
        index += 1
    path = Path(path) if path else project["_base"] / "subtitles.srt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_color(value, default="&H00FFFFFF"):
    if not value:
        return default
    value = {"white": "FFFFFF", "black": "000000", "yellow": "FFD400"}.get(str(value).lower(), str(value)).lstrip("#")
    if len(value) == 6:
        r, g, b = value[0:2], value[2:4], value[4:6]
        return f"&H00{b}{g}{r}".upper()
    return default


def create_ass(project, path):
    subs, out = project["subtitles"], project["output"]
    size = int(subs["font_size"])
    max_em = max(4, (out["width"] - 2 * subs["margin_h"]) / size)
    border_style = 3 if subs.get("box") else 1
    back = "&H80000000"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {out['width']}
PlayResY: {out['height']}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{subs.get('font_family', FONT_FAMILY)},{size},{_ass_color(subs.get('color'))},&H000000FF,&H00000000,{back},{-1 if subs.get('bold', True) else 0},0,0,0,100,100,0,0,{border_style},{subs['outline']},0,2,{subs['margin_h']},{subs['margin_h']},{subs['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for start, end, text in subtitle_cues(project):
        body = "\\N".join(l.replace("{", "(").replace("}", ")") for l in wrap_text(text, max_em))
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{body}")
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return Path(path)


def escape_filter_path(path):
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]")


def fit_filters(width, height, fit):
    if fit == "cover":
        return [f"scale={width}:{height}:force_original_aspect_ratio=increase", f"crop={width}:{height}"]
    return [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
    ]


def normalize_shot(project, shot, duration, target, temp):
    base = project["_base"]
    settings = project["output"]
    width, height, fps = settings["width"], settings["height"], settings["fps"]
    kind = shot.get("type", "video")
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    audio_index = 1
    silence = ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    if kind == "video":
        source = resolve(base, shot["source"])
        info = probe(source)
        has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
        command += ["-i", str(source)]
        if has_audio:
            audio_index = 0
        else:
            command += silence
    elif kind == "image":
        command += ["-loop", "1", "-framerate", str(fps), "-t", str(duration), "-i", str(resolve(base, shot["source"]))]
        command += silence
    else:
        background = shot.get("background", "#101820")
        command += ["-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:r={fps}:d={duration}"]
        command += silence
    filters = fit_filters(width, height, shot.get("fit", "cover" if kind == "image" else "contain"))
    filters += ["setsar=1"]
    if kind == "image" and shot.get("motion") in {"zoom_in", "zoom_out"}:
        frames = max(1, int(round(duration * fps)))
        amount = float(shot.get("zoom", 0.08))
        if shot["motion"] == "zoom_in":
            z = f"1+{amount}*on/{frames}"
        else:
            z = f"1+{amount}-{amount}*on/{frames}"
        # Upscale first to reduce zoompan jitter, then zoom around the centre.
        filters += [f"scale={width*2}:{height*2}",
                    f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"]
    filters += [f"fps={fps}", "format=yuv420p"]
    if kind == "title" and shot.get("text"):
        # Title text is rendered with libass (not drawtext): libass picks the correct
        # Simplified-Chinese face and weight inside .ttc collections by family name,
        # handles multi-line text and needs no filter-string escaping of the text.
        size = int(shot.get("font_size", math.floor(min(width, height) * (0.085 if width >= height else 0.10))))
        max_em = max(3, (width * 0.84) / size)
        lines = wrap_text(shot["text"], max_em)
        family = shot.get("font_family", FONT_FAMILY)
        bold = -1 if shot.get("bold", True) else 0
        align = {"center": 5, "top": 8, "bottom": 2}.get(shot.get("position", "center"), 5)
        margin_v = int(shot.get("margin_v", round(height * 0.08)))
        body = "\\N".join(l.replace("{", "(").replace("}", ")") for l in lines)
        ass = Path(temp) / f"title-{shot.get('id', 'x')}.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Title,{family},{size},{_ass_color(shot.get('color', '#FFFFFF'))},&H000000FF,&H00000000,&H00000000,{bold},0,0,0,100,100,0,0,1,0,0,{align},{round(width*0.08)},{round(width*0.08)},{margin_v},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,{ass_time(0)},{ass_time(duration + 1)},Title,,0,0,0,,{body}\n",
            encoding="utf-8",
        )
        fontsdir = str(Path(find_font()).parent)
        filters.append(f"subtitles=filename='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(fontsdir)}'")
    fade = float(shot.get("fade", 0))
    if fade > 0:
        fade = min(fade, duration / 2)
        filters += [f"fade=t=in:st=0:d={fade}", f"fade=t=out:st={max(0, duration-fade):.3f}:d={fade}"]
    volume = float(shot.get("volume", 1.0))
    audio_filter = f"[{audio_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={volume},apad[a]"
    command += ["-filter_complex", f"[0:v]{','.join(filters)}[v];{audio_filter}"]
    command += ["-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}", "-r", str(fps)]
    command += encoder_args("8M")
    command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(target)]
    run(command)


def build_project(project, output=None):
    report = validate_project(project)
    if report["errors"]:
        raise ValueError("\n".join(report["errors"]))
    base = project["_base"]
    output = Path(output).resolve() if output else resolve(base, project["output"]["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    srt = create_srt(project)
    has_subtitles = bool(srt.read_text(encoding="utf-8").strip())
    print(f"H.264 encoder: {pick_h264_encoder()}", flush=True)
    with tempfile.TemporaryDirectory(prefix="video-studio-") as temp:
        temp = Path(temp)
        segments = []
        for index, shot in enumerate(project["shots"], 1):
            target = temp / f"{index:03d}.mp4"
            duration = shot_duration(shot, base)
            print(f"Normalizing {index}/{len(project['shots'])}: {shot.get('id', index)}", flush=True)
            normalize_shot(project, shot, duration, target, temp)
            segments.append(target)
        concat = temp / "concat.txt"
        concat.write_text("".join(f"file '{segment}'\n" for segment in segments), encoding="utf-8")
        joined = temp / "joined.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])
        command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined)]
        music = project.get("music")
        if music:
            command += ["-stream_loop", "-1", "-i", str(resolve(base, music["source"]))]
        filters, audio_filters = [], []
        if has_subtitles and project["subtitles"]["burn_in"]:
            ass = create_ass(project, temp / "subtitles.ass")
            fontsdir = str(Path(find_font()).parent)
            filters.append(f"subtitles=filename='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(fontsdir)}'")
        total = report["duration"]
        if music:
            music_volume = float(music.get("volume", 0.25))
            original_volume = float(music.get("original_volume", 1.0))
            fade_out = float(music.get("fade_out", 1.0))
            music_chain = f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={music_volume},atrim=0:{total}"
            if fade_out > 0:
                music_chain += f",afade=t=out:st={max(0, total-fade_out):.3f}:d={fade_out}"
            audio_filters += [f"[0:a]volume={original_volume}[a0]", music_chain + "[a1]",
                              "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"]
            audio_label = "mix"
        else:
            audio_label = "0:a"
        if project["audio"].get("loudnorm", True):
            audio_filters.append(f"[{audio_label}]loudnorm=I=-16:TP=-1.5:LRA=11[a]")
        else:
            audio_filters.append(f"[{audio_label}]acopy[a]")
        filter_complex = []
        if filters:
            filter_complex.append(f"[0:v]{','.join(filters)}[v]")
        filter_complex += audio_filters
        command += ["-filter_complex", ";".join(filter_complex), "-map", "[v]" if filters else "0:v", "-map", "[a]"]
        command += encoder_args("10M") if filters else ["-c:v", "copy"]
        command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", "-movflags", "+faststart", str(output)]
        run(command)
    sidecar = output.with_suffix(".srt")
    if has_subtitles and sidecar.resolve() != srt.resolve():
        shutil.copyfile(srt, sidecar)
    print(output)
    return output


def init_project(path, title, aspect="16:9"):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    width, height = ASPECT_PRESETS[aspect]
    project = {
        "project": title,
        "output": {"width": width, "height": height, "fps": 24, "path": "output/final.mp4"},
        "shots": [{"id": "opening", "type": "title", "duration": 3, "text": title, "subtitle": title, "background": "#101820", "fade": 0.25}],
        "subtitles": {"burn_in": True},
        "audio": {"loudnorm": True},
    }
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


def doctor():
    report = {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}
    try:
        report["h264_encoder"] = pick_h264_encoder()
    except RuntimeError as exc:
        report["h264_encoder"] = f"ERROR: {exc}"
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout if report["ffmpeg"] else ""
    report["filters"] = {name: f" {name} " in filters for name in ("drawtext", "subtitles", "zoompan", "loudnorm")}
    for label, bold in (("font_regular", False), ("font_bold", True)):
        try:
            report[label] = find_font(bold)
        except RuntimeError as exc:
            report[label] = f"ERROR: {exc}"
    # optional: HTML motion-graphics renderer (scripts/html_render.py) needs Playwright + Chromium
    pw = None
    for cand in [sys.executable, os.environ.get("VIDEO_STUDIO_PYTHON"), str(Path.home() / ".venvs" / "video-studio" / "bin" / "python")]:
        if cand and Path(cand).exists() and subprocess.run([cand, "-c", "import playwright"], capture_output=True).returncode == 0:
            pw = cand
            break
    report["html_render_python"] = pw or "missing (optional): python3 -m venv ~/.venvs/video-studio && ~/.venvs/video-studio/bin/pip install playwright && ~/.venvs/video-studio/bin/python -m playwright install chromium"
    report["ok"] = all(report["filters"].values()) and not any(str(v).startswith("ERROR") for v in report.values()) and bool(report["ffmpeg"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check ffmpeg, H.264 encoder, filters and CJK fonts")
    init = sub.add_parser("init", help="create a minimal project.json")
    init.add_argument("manifest", type=Path)
    init.add_argument("--title", default="未命名视频")
    init.add_argument("--aspect", choices=sorted(ASPECT_PRESETS), default="16:9")
    for name in ("check", "subtitles", "build"):
        cmd = sub.add_parser(name)
        cmd.add_argument("manifest", type=Path)
        if name == "check":
            cmd.add_argument("--json", action="store_true")
        if name in ("subtitles", "build"):
            cmd.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "init":
        init_project(args.manifest, args.title, args.aspect)
        return 0
    project = load_project(args.manifest)
    if args.command == "check":
        report = validate_project(project)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["errors"] else 0
    if args.command == "subtitles":
        print(create_srt(project, args.output))
        return 0
    build_project(project, args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileExistsError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"hyperlens: {exc}", file=sys.stderr)
        raise SystemExit(1)
