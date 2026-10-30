# 命令参考

## 依赖

`video_pipeline.py` 只使用 Python 标准库、FFmpeg 和 FFprobe。编码使用 H.264/AAC；字体优先使用系统 Noto Sans CJK。素材生成工具是否可用由当前环境决定，合成器本身不依赖 AI 服务。

## 环境自检

```bash
python3 scripts/video_pipeline.py doctor
```

输出 ffmpeg 路径、可选依赖（HTML 渲染用的 Playwright、story_lint 用的 jieba，缺少时只提示不报错）、自动选中的 H.264 编码器（优先 libx264，其次 libopenh264，再试硬件编码器，均做一次真实试编码）、所需滤镜和中文字体。环境变量：`VIDEO_STUDIO_H264_ENCODER`、`VIDEO_STUDIO_FONT_REGULAR`、`VIDEO_STUDIO_FONT_BOLD`、`VIDEO_STUDIO_FONT_FAMILY`。

## 故事层规划与检查

```bash
python3 scripts/story_lint.py --plan <project>/project.json     # 按 story.genre / story.duration 算步骤清单，写回 story.plan（--dry-run 只打印，--json 输出 JSON）
python3 scripts/story_lint.py --plan --genre 教程 --duration 60  # 还没有 project.json 时只打印
python3 scripts/story_lint.py <project>/                         # 检查 STORY/script/outline/镜头表/PROMPTS/QC 等，红灯非零退出
```

只用标准库；装了 jieba 时多一项“复述画面”参考（黄灯）。规则见 `references/story_pipeline.md`。

## 创建项目

```bash
python3 scripts/video_pipeline.py init <project>/project.json --title "我的视频" --aspect 9:16
```

该命令创建一个可直接构建的 3 秒标题项目，并建好目录结构（`ref/ shots/ html/ images/ audio/ scripts/ frames/board/ qc/ output/`）和 `PROGRESS.md`（来自 `assets/PROGRESS.template.md`）；已有文件不会被覆盖。需要真实视频项目时，复制 `assets/project.template.json` 并替换素材路径。

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

多画幅与预览：

```bash
python3 scripts/video_pipeline.py check <project>/project.json --aspect 9:16 --json   # 某个画幅变体的检查（含 warnings）
python3 scripts/video_pipeline.py build <project>/project.json --aspect 9:16          # 只出 9:16
python3 scripts/video_pipeline.py build <project>/project.json --all                  # 主画幅 + output.variants 全部
python3 scripts/video_pipeline.py build <project>/project.json --all --preview        # 每个画幅的半分辨率预览（看板用）
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

## 参考片拆解、音乐卡拍、看板

```bash
python3 scripts/ref_analyze.py ref/ref.mp4 --out ref/analysis                  # 切分、平均镜头长度、代表帧拼图、色板 HEX、明度/饱和度、风格说明草稿
python3 scripts/beat_map.py analyze audio/music.wav --out audio/beats.json      # BPM、拍点、小节首拍、能量段落（numpy；--librosa 可选对照）
python3 scripts/beat_map.py snap audio/beats.json --project project.json --fps 30   # 切点对齐到最近拍点，报告偏差和建议时长
python3 scripts/storyboard_board.py --project project.json --preview            # 全片缩略图看板 + 手机尺寸版 + 审阅提示
```

## 当前合成边界

这是通用的顺序镜头合成器，不试图替代完整非线性编辑软件。复杂多层画面、任意转场、精细调色、多轨音效和关键帧动画，应先在相应后端制作镜头，再作为视频片段加入清单。
