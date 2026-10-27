#!/usr/bin/env python3
"""Validate any delivered video without assuming its topic or aspect ratio."""
import argparse
from fractions import Fraction
import json
import struct
import subprocess
from pathlib import Path


def top_level_atoms(path):
    """Return the order of top-level MP4 atoms (e.g. ftyp, moov, mdat)."""
    atoms = []
    with path.open("rb") as handle:
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            size, kind = struct.unpack(">I4s", header)
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
                handle.seek(size - 16, 1)
            elif size == 0:
                atoms.append(kind.decode("latin-1"))
                break
            else:
                if size < 8:
                    break
                handle.seek(size - 8, 1)
            atoms.append(kind.decode("latin-1"))
    return atoms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--duration", type=float, help="expected seconds; omit to accept any positive duration")
    parser.add_argument("--tolerance", type=float, default=0.25, help="duration tolerance in seconds")
    parser.add_argument("--fps", type=float, help="expected frame rate")
    parser.add_argument("--width", type=int, help="expected pixel width")
    parser.add_argument("--height", type=int, help="expected pixel height")
    parser.add_argument("--vertical", action="store_true", help="require height > width (9:16 etc.)")
    parser.add_argument("--min-edge", type=int, default=720, help="minimum accepted shorter side; use a lower value for drafts")
    parser.add_argument("--min-bytes", type=int, default=10_000, help="minimum file size; simple title-only clips encode very small with libx264")
    parser.add_argument("--require-audio", action="store_true")
    args = parser.parse_args()
    if not args.video.is_file():
        print(json.dumps({"file": str(args.video), "passed": False, "error": "file not found"}, ensure_ascii=False))
        return 1
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(args.video)], check=True, capture_output=True, text=True).stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration", 0))
    atoms = top_level_atoms(args.video)
    checks = {
        "file_size_ok": args.video.stat().st_size >= args.min_bytes,
        "video_present": video is not None,
        "h264_video": bool(video and video.get("codec_name") == "h264"),
        "yuv420p": bool(video and video.get("pix_fmt") == "yuv420p"),
        "positive_duration": duration > 0,
        "minimum_edge": bool(video and min(video.get("width", 0), video.get("height", 0)) >= args.min_edge),
        "fast_start": "moov" in atoms and ("mdat" not in atoms or atoms.index("moov") < atoms.index("mdat")),
    }
    if args.duration is not None:
        checks["duration_matches"] = abs(duration - args.duration) < args.tolerance
    if args.width is not None:
        checks["width_matches"] = bool(video and video.get("width") == args.width)
    if args.height is not None:
        checks["height_matches"] = bool(video and video.get("height") == args.height)
    if args.vertical:
        checks["vertical"] = bool(video and video.get("height", 0) > video.get("width", 0))
    if args.fps is not None and video:
        actual = float(Fraction(video.get("r_frame_rate", "0/1")))
        checks["fps_matches"] = abs(actual - args.fps) < 0.01
    if args.require_audio:
        checks["audio_present"] = audio is not None
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(args.video), "-f", "null", "-"], capture_output=True, text=True)
    checks["full_decode"] = decoded.returncode == 0 and not decoded.stderr.strip()
    report = {
        "file": str(args.video),
        "duration": duration,
        "size_bytes": args.video.stat().st_size,
        "top_level_atoms": atoms,
        "video": {k: video.get(k) for k in ["codec_name", "profile", "width", "height", "r_frame_rate", "pix_fmt"]} if video else None,
        "audio": {k: audio.get(k) for k in ["codec_name", "sample_rate", "channels"]} if audio else None,
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if decoded.stderr.strip():
        print(decoded.stderr.strip())
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
