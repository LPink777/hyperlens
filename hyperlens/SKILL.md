---
name: Hyperlens
description: 通用视频策划与制作流程，适用于任意题材的短视频、广告、科普、解说、产品发布、功能演示、教程、动态图形和图片/视频混剪，横屏与竖屏（9:16）均可。用户说“做个视频”“生成视频”“文生视频/图生视频”“把这篇稿子做成短视频”“分镜到成片”“动态图形/代码出片”“一镜到底”“配字幕/配乐”“竖屏短视频”时使用。先用一张选择卡确认时长、画幅、风格、参数和交付物，再负责方案与分镜、画风锁、逐镜后端选择（含 HTML 动态合成渲染）、自动合成、烧录中文字幕、审片和技术质检。
---

# Hyperlens

以 **镜头为单位**制作视频，不绑定题材或某个生成器。AI 视频、AI 图片、已有素材、自己渲染的图形卡片、HTML 动态合成和三维渲染都只是镜头来源，最终进入同一个 `project.json`、同一条合成与质检流程。

## 总流程

`需求确认（选择卡） → 一件事与三个方案 → SPEC 与画风锁 → 镜头表（含物/动作/镜头/承接）→ lint → 后端路由 → 静帧/首帧确认 → 试拍 1–2 镜 → 镜头生产 → 逐镜验收 → 音频素材级验收 → project.json 检查 → 合成（字幕与声音）→ 成片音频验收 → 审片 + 技术质检 → 按镜头迭代`

- 用户给出视频题目或稿子后，**不要直接开做**，先完成第 1 步。
- 不要在没有分镜时直接生成整片；不要只看命令退出码就宣称成片合格。

## 0. 环境自检（每个新环境做一次）

```bash
python3 scripts/video_pipeline.py doctor
```

检查 ffmpeg/ffprobe、可用的 H.264 编码器（自动在 libx264 → libopenh264 → 硬件编码器中选第一个真能编码的）、所需滤镜、中文字体，以及 HTML 渲染所需的 Playwright。

- 缺 ffmpeg：安装 `ffmpeg`；缺中文字体：安装 `fonts-noto-cjk`，或用 `VIDEO_STUDIO_FONT_REGULAR / VIDEO_STUDIO_FONT_BOLD` 指向字体文件；强制编码器：`VIDEO_STUDIO_H264_ENCODER`。
- 要用 HTML 动态合成时，一次性安装到稳定 venv（`html_render.py` 会自动找到并切换过去，无需激活）：

```bash
python3 -m venv ~/.venvs/video-studio
~/.venvs/video-studio/bin/pip install playwright
~/.venvs/video-studio/bin/python -m playwright install chromium
```

  venv 在别处时设 `VIDEO_STUDIO_PYTHON=/path/to/bin/python`。

## 1. 需求确认：先发一张选择卡（必做）

收到视频题目后，第一步是给用户发 **一张多步骤选择卡**，用户全部选完后才进入分镜和制作。

规则：
- 一张卡 **最多 8 个问题**，一次问完，不要零散追问。
- 前 5 个固定：**时长、画幅、风格、参数（分辨率/帧率）、交付物**；其余从推荐附加项中挑与题目最相关的：旁白/配音、字幕、背景音乐、封面、语言、品牌元素（Logo/账号名/配色）、素材来源（是否允许 AI 生成）、发布平台。
- 每题 **3–4 个具体选项**，按题目定制（例如工具测评题的风格选项写“录屏+标注”“信息卡片”“口播+字幕”，不要写空泛的“简约/大气”）；每题标出推荐项，并允许“其他（自己填）”。
- 如果环境提供选择卡/提问卡工具就用它；没有就用编号列表一次列出，让用户回复编号。
- 用户说“按推荐/你定”时，直接采用每题推荐项并在回复里复述最终选择。
- 选项中涉及 AI 生成视频/图片的，要注明“会消耗生成额度”；不要编造具体价格。

### 建议默认选项集（按题目调整后放进选择卡）

| 问题 | 选项 A（常用推荐） | 选项 B | 选项 C | 选项 D |
|---|---|---|---|---|
| 时长 | 15–20 秒 | 30 秒 | 45–60 秒 | 其他 |
| 画幅 | 竖屏 9:16（手机信息流/短视频） | 横屏 16:9（网页/长视频） | 方形 1:1 | 4:5 |
| 风格 | 信息卡片 + 动态文字（可复现、零生成） | 录屏/截图 + 标注 | AI 生成实拍感镜头 + 字幕 | 混合 |
| 参数 | 720×1280 / 24fps（草稿、体积小） | 1080×1920 / 30fps（正式发布） | 1280×720 / 24fps | 1920×1080 / 30fps |
| 交付物 | MP4 成片 | MP4 + SRT 字幕 | MP4 + SRT + 封面图 | 全套（含 project.json、分镜表、素材清单） |
| 旁白/配音 | 无旁白，靠字幕 | 用户自己录音 | 合成配音（需确认可用工具） | — |
| 字幕 | 烧录中文字幕 | 外挂 SRT | 中英双语 | 无字幕 |
| 背景音乐 | 无音乐 | 生成的简单音调铺底 | 用户提供已授权音乐 | — |

其他可替换的附加项：封面（首帧/单独设计/不需要）、语言（简体中文/英文/双语）、品牌元素（账号名水印/片尾卡/不加）、素材来源（只用可复现图形/允许 AI 生成/用户提供素材）。

确认完后用一句话复述最终简报：目标、受众、平台、时长、画幅、风格、参数、交付物、声音与字幕、必须出现的信息。涉及真实人物、品牌、新闻或数据时先核实来源，不把生成画面当成真实记录。

## 2. 方案、SPEC 与镜头表

**先定方向（15 秒以上、要讲一件事的片子）**
- 写出“一件事”：这支片让观众记住的一句话；用一个具体例子或一份真实数据贯穿全片。
- 写三个彼此不同的方案（可存为 `STORYLINES.md`），推荐项放第一，让用户挑一个；用户说“直接做”就取推荐项。再找 1–2 部标杆作品写“学什么/不拿什么”。详见 `references/storyboard_spec.md`。
- 用 SPEC 把形容词换成数字（LOOK / MOTION / SHOTS / BANNED / QA），并在镜头表前写**画风锁三行**（画风、色板、角色）。详见 `references/style_lock.md`。
- 产品发布、功能演示、“别像 PPT”的需求：按 `references/one_take.md` 先出三个中心画面创意，再写“在动 / 静止 / 承接”节拍表。

**镜头表**
- 每个镜头写清：`id / 目的 / 画面 / 时长 / 来源 / 运动 / 声音 / 字幕 / 连续性`，另加四列：**物**（名词）、**动作**（物自己的动词）、**镜头**（停/缓推/拉远/横移/快推/砸入/切/一镜到底）、**承接**（跨到下一镜继续存在的东西）。模板：`assets/storyboard.template.md`。
- 总时长 = 各镜头时长之和；一个镜头只承担一个主要动作或信息点。
- 开头 3 秒是具体的小场景而不是标题；结尾落在主物的动作上；文字主导的镜头 ≤ 30%；屏上大字每镜 ≤ 8 字；镜头时长要有变化，留出静止的呼吸段。
- 字幕以定稿文案为准，不凭空补充事实；竖屏每条字幕建议 ≤ 2 行（约 16 个汉字/行 @720 宽）。
- 生产前检查镜头表，红灯先改表：

```bash
python3 scripts/storyboard_lint.py 镜头表.md --target <总秒数> [--code]
```

  `--code` 用于代码动画路线（单镜 2–6 秒为绿），不加则按 AI 视频镜头标准。

分镜表发给用户看一眼再开做（尤其是要用 AI 生成时）。

## 3. 选择镜头后端

| 镜头需求 | 推荐后端 | 细节 |
|---|---|---|
| 已有视频、图片、录屏、截图 | 直接整理素材写入清单；真实素材是主画面 | — |
| 新的实拍感/概念镜头 | `GenerateVideo`（文生/图生）；首帧或参考图用 `GenerateImage` | `references/backend_routing.md` |
| 静态标题、信息卡、代码/命令卡片 | 清单里的 `title` 镜头，或 PIL/HTML 截图出 PNG 作 `image` 镜头 | `references/manifest_schema.md` |
| 动态图形、动态文字、UI/产品演示、线稿解说、歌词 MV | HTML 合成 + `scripts/html_render.py`（确定性逐帧渲染） | `references/html_motion.md` |
| **顺序镜头成片 + 其中有动态图形镜头** | `html_render.py` 把每个 HTML 镜头渲染成 MP4 → 在 `project.json` 写成 `type: video` → `video_pipeline.py` 统一拼接、字幕、音乐 | `references/backend_routing.md`「两个脚本怎么配合」 |
| 一镜到底发布片、功能演示、承接式转场 | HTML 合成 + 一镜到底方法 | `references/one_take.md` |
| 固定画风系列、风格短片 | 风格卡 + 任意后端，全片同一风格前缀 | `references/style_lock.md` |
| 已有 React 团队、参数化批量出片 | Remotion（注意 Remotion License） | `references/remotion.md` |
| 需要 GSAP 时间轴、组件库、可视化编辑 | HyperFrames（Apache-2.0） | `references/html_motion.md` |
| 可编辑三维场景 | Blender 等三维后端渲染成视频片段 | — |
| 找参考案例与提示词写法 | 案例检索（只读、只给链接） `scripts/case_search.py` | `references/case_library.md`、`references/prompt_library.md` |

**生成工具规则**：`GenerateVideo` / `GenerateImage` 会消耗用户的生成额度。批量生成前（或一次生成多个镜头前）先告诉用户要生成几个镜头、每个大概多长，等用户确认再调用；不要编造价格；未实际调用不得声称已生成。画面里不要依赖模型写字，文字一律后期用标题卡/字幕/HTML 叠加。

## 4. 生产素材

项目目录：

```text
<project>/
├── project.json
├── STORYLINES.md / 镜头表.md   # 方案与镜头表
├── shots/     # 镜头视频（生成、已有或 html_render 渲染）
├── html/      # HTML 合成镜头源文件
├── images/    # 图形卡片、首帧、参考图、截图
├── audio/     # 配音、音乐、铺底音
├── scripts/   # 本项目的卡片渲染脚本等（保证可复现）
├── frames/    # 静帧与质检抽帧
└── output/    # final.mp4 / final.srt
```

- **HTML 镜头**：从 `assets/comp.template.html` 起步，遵守合成契约（画面只由 t 决定，暴露 `window.__seek(t)`）。先出静帧对照镜头表，再做确定性自检，最后渲染；尺寸与 `project.json` 的输出一致：

```bash
python3 scripts/html_render.py html/s01.html --out frames/s01/ --stills 0.5,1.5,2.5
python3 scripts/html_render.py html/s01.html --out /tmp/x --check-determinism
python3 scripts/html_render.py html/s01.html --out shots/s01.mp4 --size 720x1280 --fps 24 [--workers 4] [--shutter 4]
```

  单帧位移 > 80px 的快速运动开 `--shutter`。字幕不要画进 HTML，交给合成器烧录。
- **AI 生成镜头**：全片逐字复用同一段风格前缀；先确认 1–3 张关键帧，再图生视频；保留提示词与版本，失败只重做该镜头。
- **逐镜验收（必做）**：AI 生成或外部来源的每个镜头，进合成前都要单独过检验门，详见 `references/shot_qc.md`。
  1. 批量生成前先试拍 1–2 个最难的镜头（手部精细操作、转身正脸、复杂动作、多人），发给用户看过再批量。
  2. 技术自检并生成审片提示：`python3 scripts/shot_qc.py shots/sXX.mp4 --expect "期望画面" --duration N --width W --height H --fps F`
  3. 把镜头和审片提示交给一个没参与生成的审阅者（看视频的 agent）：描述实际画面、符合度 0–10、逐项缺陷与秒数、可用区间。
  4. 判定：符合度 ≥ 7 且无 P0 缺陷 → 通过；P0 只在局部且可用区间 ≥ 所需 60% → 剪用该区间；否则改策略重做（复杂动作先锁关键帧再图生视频、手部只拍小幅动作、转身改拍背影或剪影），每镜最多重做 2 次，仍不过就换镜头设计或后端。
  5. 在 `shots/QC.md` 记录版本、得分、缺陷、可用区间和处理方式。
- **音频素材级验收（必做）**：每段音频生成或合成后马上全程检查，不等到成片：每条旁白、每段环境音/音效、每段音乐、要保留的镜头原声、每个中间混音轨。详见 `references/audio_qc.md`。
  1. `python3 scripts/audio_qc.py <音频或视频> --kind vo|ambient|music|mix`（可批量）。脚本逐窗扫描并标出底噪、嘶嘶声、噼啪/静电/爆音、click、削波、响度突跳的时间点，生成频谱图；有问题 exit 1。
  2. 被标出的时间点逐个抽听并看局部频谱图；是瑕疵就只重做这段素材，过了再进混音；是故意的声音就在 QC 记录里写明。
  3. AI 视频镜头的原声默认静音，确要用时先单独导出音轨走这一步。
- **图形卡片**：按输出分辨率画，**底部约 25% 留空给字幕**，四周留 ≥ 6% 安全边距；竖屏避开平台右侧按钮和底部文案区。
- 音乐、人物照片、商标、已有视频必须确认使用权；没有授权音乐时用无音乐或 ffmpeg 生成的简单音调铺底。

## 5. 建立清单并合成

```bash
python3 scripts/video_pipeline.py init <project>/project.json --title "片名" --aspect 9:16
python3 scripts/video_pipeline.py check <project>/project.json --json
python3 scripts/video_pipeline.py subtitles <project>/project.json   # 可选：先看 SRT 断行
python3 scripts/video_pipeline.py build <project>/project.json       # 输出 output/final.mp4 + final.srt
```

- 支持 `video` / `image` / `title` 三类镜头；`output.aspect` 可用 `16:9`、`9:16`（720×1280）、`9:16-hd`（1080×1920）、`1:1`、`4:5`。
- 字幕和标题卡用 libass 渲染（按字体族名选中简体中文字形），字号/边距单位是**输出像素**；自动中文断行（不拆英文单词、优先在标点处断行、避免单字孤行）。
- 图片默认 `fit: cover`，可设 `contain`；`motion: zoom_in / zoom_out` 做缓慢推拉。
- 字段见 `references/manifest_schema.md`，命令见 `references/api_reference.md`，模板 `assets/project.template.json`。

**混音规范**（自写混音脚本时同样适用，细节见 `references/audio_qc.md`）：所有轨先统一 48 kHz 和声道布局；各轨先调增益留 headroom（铺底比旁白低 15–20 dB），`amix` 用 `normalize=0`，后接 `alimiter` 兜底；响度用 loudnorm 两遍法（第二遍带测量值、`linear=true`），不用单遍动态模式；旁白每段首尾 10–30 ms 淡入淡出；环境音循环或拼接用 `acrossfade`，段间电平用包络过渡，不用阶跃开关；合成噪声类音效（火、雨等）不要用 `gt(random(1),…)` 这类硬开关随机门，每个脉冲要有起落包络。

**成片音频验收（必做）**：每次导出成片后马上跑，和素材级验收一样必做、不能互相替代：

```bash
python3 scripts/audio_qc.py output/final.mp4 --kind mix --lufs -16 --tp -1 --out output/qc
```

要求削波样本数 0、真峰值 ≤ −1 dBTP、积分响度在目标 ±1 LU 内、没有噼啪/嘶嘶/底噪/突跳标记。被标出的时间点要抽听，回到素材级找出是哪条轨或哪一步混音造成的，修源头后重建再查。

## 6. 审片、质检与迭代

**先审片**（技术检查之前）：
- 每个节拍帧用陌生人的口吻写一句话描述，并点名每个物；需要解释才认得出的是 P0。
- 按顺序看是否一镜引出一镜；出现的默认套路写进 SPEC 的 BANNED 并修掉。
- 每个切点前后 0.2 秒抽帧，查空帧、跳变、主体出画；按手机宽度看可读性；中文无方块字，字幕不越界、不遮主体、无孤字断行。
- 最后用正常速度带声音完整看一遍。条件允许时派一个没参与制作的 agent 只看成片挑问题。一轮集中修完再复查。
- 对照 `references/storyboard_spec.md` 里的“做坏方式”清单。

```bash
ffmpeg -ss 5 -i output/final.mp4 -frames:v 1 frames/t05.png
```

**再做技术检查**：

```bash
python3 scripts/validate_video.py <project>/output/final.mp4 \
  --duration 20 --width 720 --height 1280 --vertical --fps 24 --require-audio
python3 scripts/audio_qc.py <project>/output/final.mp4 --kind mix --out <project>/output/qc
```

技术检查通过不等于成片合格。只修改出问题的镜头或字段，重新 `build` 再抽帧复查。完整清单见 `references/quality_control.md`。

## 交付

列出实际存在的文件路径：成片 MP4，按选择卡附上 SRT、封面、`project.json`、镜头表和素材来源说明，并报告审片/质检结果和仍未解决的问题。不展示不存在的规划产物；未经用户明确同意不发布、不外发。

## 资源

- `scripts/video_pipeline.py`：环境自检、项目初始化、检查、字幕、顺序合成（自动选编码器与中文字体）。
- `scripts/validate_video.py`：技术检查（编码、尺寸、竖屏、帧率、时长、音轨、fast-start、完整解码）。
- `scripts/html_render.py`：HTML 合成 → MP4 确定性逐帧渲染（并行、快门运动模糊、静帧、确定性自检、混音）。
- `scripts/storyboard_lint.py`：镜头表红黄绿检查。
- `scripts/shot_qc.py`：逐镜技术自检（尺寸/帧率/时长/黑帧/冻结）、抽帧拼图、生成审片提示。
- `scripts/audio_qc.py`：通用音频验收（单个素材或成片都能用）：LUFS、真峰值、削波计数，逐窗标出底噪/嘶嘶/噼啪静电/click/突跳的时间点，出频谱图，有问题 exit 1。
- `scripts/case_search.py`：公开案例库只读检索（返回原帖链接）。
- `assets/project.template.json`：竖屏混合素材项目模板。
- `assets/comp.template.html`：HTML 合成模板（缓动、弹簧、种子随机、镜头关键帧、`?play` / `?t=` 预览）。
- `assets/storyboard.template.md`：扩展镜头表示例。
- `references/storyboard_spec.md`：一件事 → 三个方案 → SPEC → 镜头表 → 审片 → 做坏方式。
- `references/style_lock.md`：画风锁、形容词→数字、风格卡模板、默认禁用清单。
- `references/one_take.md`：一镜到底与承接、节拍表、操作员式运镜。
- `references/html_motion.md`：HTML 合成契约、渲染器用法、可读性数字、HyperFrames/Remotion 选型。
- `references/remotion.md`：Remotion 路线与许可提醒。
- `references/prompt_library.md`：提示词写法规律、技术路线、原创中文模板。
- `references/case_library.md`：何时查案例、怎么查、怎么用（只学结构不抄）。
- `references/backend_routing.md`：后端选择、生成额度确认、两个脚本的衔接。
- `references/prompt_playbook.md`：单镜头提示词方法。
- `references/manifest_schema.md` / `references/api_reference.md`：清单字段与命令。
- `references/shot_qc.md`：逐镜验收：试拍、缺陷清单（P0/P1）、通过/剪用/重做判定与重做策略。
- `references/audio_qc.md`：音频两级验收（素材级 + 成片级）、判定标准、混音规范（48k、headroom、alimiter、loudnorm 两遍法、淡入淡出、acrossfade、不用硬开关随机门）和示例案例。
- `references/quality_control.md`：画面、声音、内容和技术质检。
- `references/sources.md`：方法来源与许可（对外分发时保留）。
