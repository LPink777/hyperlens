# 命令参考

## 依赖

`video_pipeline.py` 只使用 Python 标准库、FFmpeg 和 FFprobe。编码使用 H.264/AAC；字体优先使用系统 Noto Sans CJK。素材生成工具是否可用由当前环境决定，合成器本身不依赖 AI 服务。

## 环境自检

```bash
python3 scripts/video_pipeline.py doctor
```

输出 ffmpeg 路径、自动选中的 H.264 编码器（优先 libx264，其次 libopenh264，再试硬件编码器，均做一次真实试编码）、所需滤镜和中文字体。环境变量：`VIDEO_STUDIO_H264_ENCODER`、`VIDEO_STUDIO_FONT_REGULAR`、`VIDEO_STUDIO_FONT_BOLD`、`VIDEO_STUDIO_FONT_FAMILY`。

## 创建项目

```bash
python3 scripts/video_pipeline.py init <project>/project.json --title "我的视频" --aspect 9:16
```

该命令创建一个可直接构建的 3 秒标题项目；已有文件不会被覆盖。需要真实视频项目时，复制 `assets/project.template.json` 并替换素材路径。

## 检查项目

```bash
python3 scripts/video_pipeline.py check <project>/project.json --json
```

检查镜头 ID、类型、源文件、时长和音乐路径，输出镜头清单与预计总时长。视频未写 `duration` 时会读取完整源视频时长。

## 字幕

```bash
python3 scripts/video_pipeline.py subtitles <project>/project.json
```

根据每个镜头的 `subtitle` 和时长生成 `subtitles.srt`。可用 `--output PATH` 指定其他位置。

## 合成

```bash
python3 scripts/video_pipeline.py build <project>/project.json \
  --output <project>/output/final.mp4
```

合成步骤：逐镜规范化 → 统一帧率和画幅 → 镜头音轨处理 → 顺序拼接 → 可选烧录字幕 → 可选音乐混合与响度规范化 → fast-start MP4 → 同名 SRT。

## 技术验证

```bash
python3 scripts/validate_video.py final.mp4 \
  --duration 15 --fps 24 --require-audio
```

可选精确尺寸：

```bash
python3 scripts/validate_video.py vertical.mp4 \
  --width 720 --height 1280 --vertical --min-edge 720
```

- `--duration` 不填时只要求正时长。
- `--require-audio` 只用于应该有声音的交付。
- `--vertical` 要求高 > 宽；`--min-bytes` 默认 10000（纯标题卡用 libx264 编码体积很小，属正常）。
- 低分辨率测试可设 `--min-edge 360`，避免把草稿尺寸误判为编码失败。

## 当前合成边界

这是通用的顺序镜头合成器，不试图替代完整非线性编辑软件。复杂多层画面、任意转场、精细调色、多轨音效和关键帧动画，应先在相应后端制作镜头，再作为视频片段加入清单。
