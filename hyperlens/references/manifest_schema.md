# project.json 清单规范

所有相对路径均相对于 `project.json` 所在目录。镜头按数组顺序串联，总时长为各镜头时长之和。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `project` | string | 项目名 |
| `output.aspect` | string | 可选预设：`16:9`(1280×720)、`9:16`(720×1280)、`9:16-hd`(1080×1920)、`1:1`(1080×1080)、`4:5`(1080×1350) |
| `output.width / height` | integer | 默认 1280×720；必须为偶数；显式填写会覆盖 aspect |
| `output.fps` | integer | 默认 24 |
| `output.path` | string | 默认 `output/final.mp4`；命令行可覆盖 |
| `output.variants` | object | 可选，多画幅：键是画幅名（预设名或自定义名），值可含 `aspect` / `width` / `height` / `fps` / `path` / `subtitles`。`build --aspect <键>` 出这个画幅，`build --all` 出主画幅 + 全部变体。变体不继承主画幅的字幕字号和边距（`font_size`、`margin_v`、`margin_h`、`outline`），要么在变体里写，要么用该画幅的默认值 |
| `shots` | array | 非空镜头数组 |
| `music` | object | 可选背景音乐 |
| `subtitles` | object | 字幕设置 |
| `audio.loudnorm` | boolean | 默认 true，目标约 -16 LUFS、TP -1.5 dB |
| `story` | object | 可选；故事层配置，合成器忽略。`genre`、`duration`，可选 `reveal`、`ai_image`、`ai_video`、`silent`、`has_facts`、`has_protagonist`；`plan` 由 `story_lint.py --plan` 写回（步骤、文件、确认点、lint 开关、验收权重），不手写。见 `story_pipeline.md` |

## 视频镜头

```json
{
  "id": "main-shot",
  "type": "video",
  "source": "shots/main.mp4",
  "duration": 4.5,
  "volume": 0.8,
  "fade": 0.2,
  "subtitle": "这一时段显示的字幕"
}
```

- `duration` 可省略，此时读取源视频完整时长；设置后裁剪到该时长。
- `volume` 控制该镜原声，默认 1。
- `fade` 为淡入/淡出黑场秒数，默认 0。

## 多画幅：逐镜覆盖与裁切焦点

```json
{
  "id": "s03", "type": "video", "source": "shots/s03_16x9.mp4", "duration": 3,
  "variants": {"9:16": {"source": "shots/s03_9x16.mp4"}},
  "subtitle": "同一句字幕"
}
```

- `variants.<画幅>`：这个画幅下覆盖该镜的任意字段（`source`、`fit`、`crop_focus`、`text`、`font_size`、`motion`……）；时长和字幕通常不改，保证各画幅共用同一时间轴和剧本。
- `crop_focus: [x, y]`（0–1，默认 0.5/0.5）：`fit: cover` 时裁切窗口的位置，0 = 靠左/靠上，1 = 靠右/靠下。只在不得不裁时用，且必须过一遍该画幅的看板。
- `check --aspect <画幅>` 会对“源画面比例和输出差 >10% 又没有本画幅覆盖”的镜头给 warning（`warnings` 字段）。
- `build --preview`：同一时间轴的半分辨率草稿（`preview_*.mp4`），给 `storyboard_board.py` 出看板用。

## 图片镜头

```json
{
  "id": "still",
  "type": "image",
  "source": "images/cover.png",
  "duration": 3,
  "motion": "zoom_in",
  "subtitle": "图片说明"
}
```

- `motion`：省略 / `zoom_in` / `zoom_out`；`zoom` 为推拉幅度，默认 0.08。
- `fit`：图片默认 `cover`（等比铺满并裁切），可设 `contain`（等比缩放加黑边）；视频默认 `contain`。
- 图片默认带静音音轨，便于与其他镜头统一混合。

## 标题卡

```json
{
  "id": "opening",
  "type": "title",
  "duration": 2,
  "text": "片名",
  "background": "#101820",
  "color": "white",
  "font_size": 64,
  "subtitle": "片名"
}
```

- 标题用 libass 渲染：`font_family`（默认 Noto Sans CJK SC）、`bold`（默认 true）、`font_size`（像素，默认横屏 8.5%、竖屏 10% 的短边）、`color`（#RRGGBB）、`position`（center/top/bottom）、`margin_v`。
- `text` 中可用 `\n` 手动换行；过长会自动按画面宽度断行。标题不依赖 AI 生成文字。

## 音乐与字幕

```json
"music": {"source": "audio/music.wav", "volume": 0.22, "original_volume": 0.9},
"subtitles": {"burn_in": true, "font_size": 38, "margin_h": 36, "margin_v": 180, "box": false}
```

- `volume` 是音乐音量（可大于 1），`original_volume` 是镜头声音总音量，`fade_out` 为结尾淡出秒数（默认 1）。音乐自动循环并裁到成片长度。
- 字幕字号和边距单位均为输出像素；默认字号：竖屏约短边 5%、横屏约 4.5%；默认 `margin_v`：竖屏高度 14%、横屏 7%（避开平台底部 UI）。`box: true` 改为半透明底框。
- 字幕自动中文断行：不拆英文单词/命令，优先在标点处断行，避免单字孤行。
- `burn_in=true` 时烧录字幕；无论是否烧录，有字幕内容都会同时输出同名 `.srt`。
- 合成器是顺序镜头流程，不是多轨非线性编辑器；复杂叠化、多层画面和精确混音应在专业剪辑工具中继续完成。
