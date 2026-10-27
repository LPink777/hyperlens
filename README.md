# Hyperlens

Hyperlens 是一份给 AI 智能体用的**通用视频策划与制作**技能（agent skill）。它不绑定题材，也不绑定某一个生成器：短视频、广告、科普、解说、产品发布、功能演示、教程、动态图形、图片/视频混剪都可以走同一条流程，横屏和竖屏（9:16）都支持。

AI 视频、AI 图片、已有素材、图形卡片、HTML 动态合成和三维渲染都只是镜头来源。最终进入同一个 `project.json`，再走同一条合成与质检流程。

## 工作流

`需求确认（选择卡） → 一件事与三个方案 → SPEC 与画风锁 → 镜头表（含物/动作/镜头/承接）→ lint → 后端路由 → 静帧/首帧确认 → 试拍 1–2 镜 → 镜头生产 → 逐镜验收 → 音频素材级验收 → project.json 检查 → 合成（字幕与声音）→ 成片音频验收 → 审片 + 技术质检 → 按镜头迭代`

完整步骤、选择卡规则和命令见 [`hyperlens/SKILL.md`](hyperlens/SKILL.md)。

## 功能

- **分镜与画风锁**：一件事、三个方案、SPEC（把形容词换成数字）、画风锁三行，以及带「物 / 动作 / 镜头 / 承接」的镜头表。`storyboard_lint.py` 做红黄绿检查。
- **后端路由**：已有素材、标题卡、AI 图生/文生视频、HTML 动态合成、一镜到底、Remotion、HyperFrames、三维渲染，按镜头选择，而不是整片绑死一种工具。
- **HTML 动态合成**：页面遵守「画面只由时间 t 决定」的契约，用 Playwright 逐帧渲染成 MP4（可并行、运动模糊、静帧、确定性自检）。
- **顺序合成**：`video_pipeline.py` 把 `video` / `image` / `title` 镜头统一画幅和帧率，烧录中文字幕、混音，输出 `output/final.mp4` 和 `final.srt`。
- **中文字幕**：libass 渲染，按字体族选取简体中文字形；自动断行（不拆英文单词、优先在标点处断开、避免单字孤行）。
- **质检**：逐镜技术自检与审片提示（`shot_qc.py`）、音频两级验收（素材级 + 成片级，`audio_qc.py`）、成片技术检查（编码、尺寸、竖屏、帧率、时长、音轨、fast-start、完整解码，`validate_video.py`）。
- **案例检索**：`case_search.py` 只读检索公开案例索引，只返回原帖链接。

## 环境要求

- `python3`（脚本只用标准库）
- `ffmpeg` 与 `ffprobe`
- 中文字体：安装 `fonts-noto-cjk`，或用环境变量 `VIDEO_STUDIO_FONT_REGULAR` / `VIDEO_STUDIO_FONT_BOLD` 指向字体文件。强制编码器可用 `VIDEO_STUDIO_H264_ENCODER`。
- **可选**，仅 HTML 动态合成需要：按 `SKILL.md` 把 Playwright 装进稳定 venv。`html_render.py` 会自动找到并切换过去，不必激活。

```bash
python3 -m venv ~/.venvs/video-studio
~/.venvs/video-studio/bin/pip install playwright
~/.venvs/video-studio/bin/python -m playwright install chromium
```

venv 在别处时设置 `VIDEO_STUDIO_PYTHON=/path/to/bin/python`。这些路径和环境变量名保持原样，与脚本一致。

## 在智能体里使用

把 `hyperlens/` 整目录放进智能体的技能目录后，在对话里直接说要做视频。智能体读取 `SKILL.md`，按上面的流程推进；你负责把题目、稿子和选择发回去。目录内部文件保持原样。

### 安装到各个智能体

一条命令克隆仓库，并装进 Claude Code 的个人技能目录（本机所有项目可用）：

```bash
git clone https://github.com/LPink777/hyperlens && mkdir -p ~/.claude/skills && cp -r hyperlens/hyperlens ~/.claude/skills/
```

`mkdir -p` 保证目标目录已存在，复制结果是 `~/.claude/skills/hyperlens/SKILL.md`。

| 智能体 | 路径 | 范围 |
|---|---|---|
| Claude Code | `~/.claude/skills/hyperlens/` | 个人，本机所有项目 |
| Claude Code | `.claude/skills/hyperlens/` | 当前项目，可提交给团队 |
| Cursor | `~/.cursor/skills/hyperlens/` 或 `~/.agents/skills/hyperlens/` | 个人，本机 |
| Cursor | 项目内 `.cursor/skills/hyperlens/` 或 `.agents/skills/hyperlens/` | 当前项目 |
| Codex | `~/.agents/skills/hyperlens/` | 个人，所有项目 |
| Codex | 仓库内 `.agents/skills/hyperlens/`（从启动目录向上直到仓库根都会扫描） | 当前仓库 |
| 其他支持 `SKILL.md` 的智能体 | 放进它自己的 skills 目录；或在对话里让它阅读本仓库的 `hyperlens/SKILL.md` | 视宿主而定 |

Cursor 的技能目录以[官方文档](https://cursor.com/docs/skills)为准：项目级是 `.cursor/skills/` 与 `.agents/skills/`，用户级是 `~/.cursor/skills/` 与 `~/.agents/skills/`。同一文档还写明，Cursor 会兼容读取 Claude 与 Codex 的目录：`.claude/skills/`、`.codex/skills/`、`~/.claude/skills/`、`~/.codex/skills/`。

Codex 的现行本地发现路径见 [Build skills](https://learn.chatgpt.com/docs/build-skills)：用户级 `$HOME/.agents/skills`，仓库级 `.agents/skills`。管理员机器上还有 `/etc/codex/skills`。

当前项目或 Codex 个人目录，在目标位置执行：

```bash
# Claude Code，仅当前项目
git clone https://github.com/LPink777/hyperlens && mkdir -p .claude/skills && cp -r hyperlens/hyperlens .claude/skills/

# Cursor，仅当前项目
git clone https://github.com/LPink777/hyperlens && mkdir -p .cursor/skills && cp -r hyperlens/hyperlens .cursor/skills/

# Codex，个人技能
git clone https://github.com/LPink777/hyperlens && mkdir -p ~/.agents/skills && cp -r hyperlens/hyperlens ~/.agents/skills/
```

### 怎么触发

请求对上 `SKILL.md` 开头的 description 时，智能体会自动加载 Hyperlens。描述里的说法包括：做个视频、生成视频、文生视频/图生视频、把这篇稿子做成短视频、分镜到成片、动态图形/代码出片、一镜到底、配字幕/配乐、竖屏短视频。

也可以点名调用：

- Claude Code：输入 `/hyperlens`。目录名会触发；frontmatter 里的 `name` 是 `Hyperlens`，`/` 菜单上也可能显示这个名字。
- Cursor：在 Agent 对话里输入 `/`，搜索该技能后选用（文档说明的手动方式是输入 `/` 并搜索技能名）。
- Codex：在 CLI 或 IDE 扩展里输入 `$hyperlens`，或运行 `/skills` 浏览后插入。
- 任何宿主：直接说「用 hyperlens skill 做一个…」。

### 示例

- 把这篇稿子做成 30 秒竖屏短视频
- 给我的产品做一个一镜到底的功能演示视频
- 用信息卡片风格做一条 20 秒科普视频
- 给这段发布说明做一条横屏解说，烧录中文字幕

### 接下来智能体会做什么

1. **先发一张选择卡**，一次问完。前 5 题固定为时长、画幅、风格、参数（分辨率/帧率）、交付物，整张卡最多 8 题。回复「按推荐」或「你定」，它会采用每题的推荐项，并复述最终选择。
2. **再出方案和镜头表**，发给你看过再开做（尤其是要用 AI 生成的时候）。
3. **按镜头生产并质检**：逐镜验收、音频素材级验收、合成，然后做成片音频验收、审片和技术质检。
4. **交付实际生成的文件**：成片 `output/final.mp4`，以及按选择卡附上的 SRT（合成输出为 `final.srt`）。选择卡里要了封面、`project.json` 或镜头表时，一并列出这些已经存在的路径。

文生视频 / 图生视频（`GenerateVideo`、`GenerateImage`）会消耗该智能体的生成额度，前提是这个智能体提供这些工具。批量生成前它会先说明要生成几个镜头、每个大概多长。智能体没有这类生成工具时，镜头改走图形卡片或 HTML 动态合成。

跑脚本需要智能体能使用 shell，并且环境里有 `python3` 和 `ffmpeg`（中文字幕还要 CJK 字体，见上一节）。每个新环境先在技能目录执行一次 `python3 scripts/video_pipeline.py doctor`。

### 手动使用脚本

不经过对话、自己在 `hyperlens/` 目录里跑时（路径均相对该目录）：

```bash
python3 scripts/video_pipeline.py doctor
python3 scripts/video_pipeline.py init <project>/project.json --title "片名" --aspect 9:16
python3 scripts/video_pipeline.py check <project>/project.json --json
python3 scripts/video_pipeline.py build <project>/project.json
```

`doctor` 检查 ffmpeg/ffprobe、H.264 编码器、滤镜、中文字体，以及可选的 Playwright。`init` 生成最小 `project.json`，`check` 检查清单，`build` 输出 `output/final.mp4` 和 `final.srt`。字幕可先单独看断行：`python3 scripts/video_pipeline.py subtitles <project>/project.json`。

## 目录结构

```text
.
├── LICENSE          # MIT，Copyright (c) 2026 LPink777
├── README.md
├── .gitignore
└── hyperlens/       # 技能本体，可整目录放进智能体 skills 目录
    ├── SKILL.md     # 工作流说明（智能体入口）
    ├── assets/      # project.json、镜头表、HTML 合成模板
    ├── references/  # 分镜、画风锁、质检、后端、许可来源
    └── scripts/     # 合成、渲染、lint、镜头/音频/成片质检、案例检索
```

## English

Hyperlens is a topic-agnostic video planning and production skill for AI agents. Shots from AI video, stills, existing media, HTML motion graphics, or a 3D render all land in one `project.json`, then go through the same assembly, Chinese subtitle burn-in, and QC pipeline.

Workflow:

`需求确认（选择卡） → 一件事与三个方案 → SPEC 与画风锁 → 镜头表（含物/动作/镜头/承接）→ lint → 后端路由 → 静帧/首帧确认 → 试拍 1–2 镜 → 镜头生产 → 逐镜验收 → 音频素材级验收 → project.json 检查 → 合成（字幕与声音）→ 成片音频验收 → 审片 + 技术质检 → 按镜头迭代`

Requires Python 3, ffmpeg/ffprobe, and a CJK font (`fonts-noto-cjk`, or `VIDEO_STUDIO_FONT_REGULAR` / `VIDEO_STUDIO_FONT_BOLD`). HTML rendering optionally uses a Playwright venv at `~/.venvs/video-studio` (override with `VIDEO_STUDIO_PYTHON`). The agent needs a shell with `python3` and `ffmpeg`; on a new machine run `python3 scripts/video_pipeline.py doctor` first.

Install by copying the inner `hyperlens/` folder into the agent's skills directory:

```bash
git clone https://github.com/LPink777/hyperlens && mkdir -p ~/.claude/skills && cp -r hyperlens/hyperlens ~/.claude/skills/
```

| Agent | Path |
|---|---|
| Claude Code, personal | `~/.claude/skills/hyperlens/` |
| Claude Code, this repo | `.claude/skills/hyperlens/` |
| Cursor, personal | `~/.cursor/skills/hyperlens/` or `~/.agents/skills/hyperlens/` |
| Cursor, this repo | `.cursor/skills/hyperlens/` or `.agents/skills/hyperlens/` |
| Codex, personal | `~/.agents/skills/hyperlens/` |
| Codex, this repo | `.agents/skills/hyperlens/` |

Cursor also reads `.claude/skills/`, `.codex/skills/`, `~/.claude/skills/`, and `~/.codex/skills/` ([docs](https://cursor.com/docs/skills)). Codex scans `.agents/skills` from the working directory up to the repo root ([docs](https://learn.chatgpt.com/docs/build-skills)). Other SKILL.md hosts: drop the folder in that host's skills directory, or ask the agent to read `hyperlens/SKILL.md`.

The agent loads the skill when the request matches its description (做个视频, 生成视频, 分镜到成片, 竖屏短视频, and the other phrases in `SKILL.md`). You can also name it: Claude Code `/hyperlens` (the `/` menu may show `Hyperlens`, the frontmatter name), Cursor `/` then search for the skill, Codex `$hyperlens` or `/skills`, or say “用 hyperlens skill 做一个…”.

Example: “把这篇稿子做成 30 秒竖屏短视频”. The agent sends a selection card first (时长 / 画幅 / 风格 / 参数 / 交付物). Reply “按推荐” to take the defaults. It then shows a storyboard, produces the shots, runs QC, and delivers `output/final.mp4` plus the SRT. AI video or image generation spends that agent's generation quota when those tools exist; otherwise shots use information cards or the HTML renderer.

Manual CLI, from the `hyperlens/` directory: `doctor`, then `init`, `check`, and `build` (see 手动使用脚本 above).

## Credits

方法来源与许可的完整记录在 [`hyperlens/references/sources.md`](hyperlens/references/sources.md)（检索日期 2026-10-27，北京时间）。对外分发时请保留该文件。下面按该文件摘要；star 数与最近推送时间以该文件为准。

| 项目 | 许可 | 本项目如何使用 |
|---|---|---|
| [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion) | MIT（花叔） | 改写其镜头表要求、画风锁、镜头语言、六种做坏方式；lint 规则为原创实现 |
| [Lemo-Opuscar](https://github.com/lemomo-ai/lemo-opuscar) | MIT（LemoLab） | 改写其 STYLE.md 结构、DIRECTOR.md 导演流程、「改成自己的风格」三分法 |
| [OneTake](https://github.com/feitangyuan/onetake) | **PolyForm Noncommercial 1.0.0（仅非商业）** | 只用自己的话总结方法论；**未复制任何代码、模板或文字** |
| [HyperFrames](https://github.com/heygen-com/hyperframes) | Apache-2.0（HeyGen） | 改写其合成契约、SPEC 模板、形容词→数字表、BANNED 清单、审片流程；渲染器为原创实现 |
| [Remotion Skills](https://github.com/remotion-dev/skills) | 该仓库未附许可。Remotion 本体使用 **Remotion License**：个人、不超过 3 名员工的营利公司、非营利组织可免费（含商用）使用；更大的营利公司需购买公司许可 | 只概括用法，不复制原文 |
| [Awesome-Opus5-5-Videos](https://github.com/yihui-dev/awesome-opus5-5-videos) | MIT（仓库）；提示词归各原作者 | 只做检索与链接（`case_search.py` 运行时下载索引） |
| [opus-video-prompts](https://github.com/joeseesun/opus-video-prompts) | **未附许可证**；提示词归各原作者 | 只总结写法规律与技术路线，提示词模板为原创 |

MIT 与 Apache-2.0 来源按「改写 + 署名」处理，本仓库未收录其原文件。本仓库自身代码与文档以根目录 [MIT 许可证](LICENSE) 发布（Copyright (c) 2026 LPink777）。上游许可不因本仓库的 MIT 许可而改变：OneTake 仍是 PolyForm Noncommercial，且这里没有复制它的代码或文字；使用 Remotion 时以 Remotion License 为准。
