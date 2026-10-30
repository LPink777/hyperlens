---
name: Hyperlens
description: 通用视频策划与制作流程，适用于任意题材的短视频、广告、科普、解说、产品发布、功能演示、教程、剧情短片、动态图形和图片/视频混剪，横屏与竖屏（9:16）均可，可同一套素材分别排版出多画幅。用户说“做个视频”“生成视频”“文生视频/图生视频”“把这篇稿子做成短视频”“分镜到成片”“动态图形/代码出片”“一镜到底”“配字幕/配乐”“竖屏短视频”时使用。先用一张选择卡确认时长、画幅、风格、参数和交付物，再写故事与剧本并过故事验收，有参考片时先做参考片拆解，然后负责分镜、画风锁、音乐卡拍、逐镜后端选择（含 HTML 动态合成渲染）、全片缩略图看板、自动合成、烧录中文字幕、审片和技术质检。
---

# Hyperlens

以 **镜头为单位**制作视频，不绑定题材或某个生成器。AI 视频、AI 图片、已有素材、自己渲染的图形卡片、HTML 动态合成和三维渲染都只是镜头来源，最终进入同一个 `project.json`、同一条合成与质检流程。

## 总流程

`需求确认（选择卡 C0）→ 故事层 2.1–2.9（素材与事实卡 → 观众问题与目标 → 方案【①】→ 主角 →（有参考时）参考片拆解 → 世界 + SPEC/画风锁【②】→ 节拍 → 剧本 → 切分镜（含预定后端）→ story_lint + storyboard_lint → 故事验收【③】）→ 后端路由 → 参考图/首帧确认【④】→ 试拍 1–2 镜 + 批量确认【⑤】→ 镜头生产 → 逐镜验收（关键镜头保护）→ 音频素材级验收 → project.json 检查 →（有配乐时）卡拍对齐 → 低清预览 + 全片看板（每个画幅）【⑥】→ 合成（字幕与声音）→ 成片音频验收 → 审片 + 技术质检 → 按镜头迭代`

**全程确认点**（哪些有由 `story_lint.py --plan` 算出，见第 2 步）：

| # | 确认点 | 时机 | 什么时候有 |
|---|---|---|---|
| C0 | 选择卡 | 第 1 步 | 总是 |
| ① | 选方案 | 2.3 | ≥15 秒 |
| ② | 主角与世界文字卡 + 画风锁（不出图） | 2.5 | ≥45 秒 |
| ③ | 剧本 + 分镜（替代原“分镜表看一眼”） | 2.9 通过后 | 总是 |
| ④ | 参考图/首帧（合并主角和世界参考图） | 第 3 步后端路由之后 | 只在用 AI 图或 AI 视频时 |
| ⑤ | 试拍结果 + 批量生成的镜头数和时长（一条消息） | 生产前 | 只在用 AI 视频时 |
| ⑥ | 各画幅的全片看板（一条消息，逐个画幅确认） | 正式合成前 | 只在多画幅时；单画幅的看板是内部验收门，不问用户 |

- 全程最多：HTML/素材路线 4 次；AI 路线 6 次（多画幅各 +1 次⑥）；<15 秒的 HTML 片 2 次（C0 + ③）。
- 用户说“你定”：①②③ 自动按推荐并在下一条消息复述选择；④⑤ 涉及生成额度，仍要确认。

- 用户给出视频题目或稿子后，**不要直接开做**，先完成第 1 步。
- 不要在没有剧本和分镜时直接生成整片；不要只看命令退出码就宣称成片合格。
- **续做**：中途断了或在新会话里接着做，先读项目的 `PROGRESS.md` 和项目文件夹（立项单 → 风格说明 → 镜头表 → `qc/` 最新记录），从“下一步”接着做，不凭记忆重来（见第 4 步“项目文件夹与进度”）。

## 0. 环境自检（每个新环境做一次）

```bash
python3 scripts/video_pipeline.py doctor
```

检查 ffmpeg/ffprobe、可用的 H.264 编码器（自动在 libx264 → libopenh264 → 硬件编码器中选第一个真能编码的）、所需滤镜、中文字体，以及可选的 HTML 渲染用 Playwright 和故事层“复述画面”参考检查用 jieba（缺少 jieba 只提示，改为人工检查，不报错）。

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
| 背景音乐 | 无音乐 | 轻氛围铺底（无静态音、已切低频，`video_pipeline.py bed` 生成） | 用户提供已授权音乐 | — |

画幅题按需加一个选项或附加项 **“多画幅（16:9 + 9:16）”**：同一套素材、时间轴和剧本，每个画幅单独排版、单独出看板（规则见第 4 步“多画幅”）；选了它，参数题要按每个画幅分别给分辨率。

其他可替换的附加项：封面（首帧/单独设计/不需要）、语言（简体中文/英文/双语）、品牌元素（账号名水印/片尾卡/不加）、素材来源（只用可复现图形/允许 AI 生成/用户提供素材）。

确认完后用一句话复述最终简报：目标、受众、平台、时长、画幅、风格、参数、交付物、声音与字幕、必须出现的信息。涉及真实人物、品牌、新闻或数据时先核实来源，不把生成画面当成真实记录。

然后把题材和时长写进 `project.json` 的 `story` 字段（`genre`、`duration`，用 AI 图/视频时加 `ai_image` / `ai_video`，选了完全无声加 `silent`），进入第 2 步。时长选项按左闭右开归档：“15–20 秒”“30 秒”→ 15–<45 档；“45–60 秒”→ 45–<90 档。

## 2. 故事层：从素材到剧本、分镜和故事验收

完整规则、模板字段、权重表和 P0 见 `references/story_pipeline.md`（必读）；8 种结构样例、钩子库和故事层做坏方式见 `references/story_structures.md`。

**先算步骤清单**（时长档定上限，题材只删减；不要自己判断做哪几步）：

```bash
python3 scripts/story_lint.py --plan <project>/project.json   # 写回 story.plan：步骤、文件、确认点、lint 开关、验收权重、审阅预算
```

| 时长档（左闭右开） | 故事层文件 | 故事层确认 |
|---|---|---|
| <15 秒 | `STORY.md`（问题 + 钩子 + 2–4 拍剧本 + 一行主角/世界）、`镜头表.md`；不出多方案；2.9 只查钩子、结尾、真实 + 本题材 ×2 项第一项 | ③ |
| 15–<45 秒 | `STORY.md`（素材/事实卡 + BRIEF + 主角与世界卡）、`STORYLINES.md`、`script.md`（含“功能”列，替代大纲）、`镜头表.md`、`STORY_QC.md` | ①③ |
| 45–<90 秒 | `research/facts.md`、`BRIEF.md`、`STORYLINES.md`、`world.md`、`outline.md`、`script.md`、`镜头表.md`、`STORY_QC.md` | ①②③ |
| ≥90 秒 | 同上，另按“幕”分段（每幕 30–45 秒）；2.9 每幕打分再整片打分 | ①②③ |

用 AI 生成时另有 `PROMPTS.md`（按角色 ID 分节）。模板都在 `assets/`：`STORY` / `facts` / `BRIEF` / `world` / `outline` / `script` / `STORY_QC` / `storyboard` 的 `.template.md`。

**9 个小步骤**（按 `story.plan` 删减或合并）
- **2.1 素材与事实卡**：每条 F# / 内容 / 来源 / 可信度 / 用途；至少 3 条有张力的素材；屏上每个说法都追得到来源，示意数据标“示意”。剧情/MV 换成灵感卡。教程按实测规则：能在本机或沙箱里安全跑的实跑；跑不了的请用户给录屏或截图（可与确认①同一条消息问）；都没有的标 `[未实测]`。
- **2.2 观众问题与目标（BRIEF）**：观众是谁 / 观众问题 / 答案 / 揭晓方式（悬念型或结果先行型）/ 目标 / 情绪路线 / 观众带走（= 原来的“一件事”）。选择卡已确认的平台、时长等直接引用。
- **2.3 方案（比结构）**：默认 3 个，教程 2 个，<15 秒不出（回复里一句话提备选）。每个写 logline、结构模板、钩子画面 + 第一句、主角类型、冲突、转折、结尾回扣、画风一行、声音一行、可拍性风险；方案之间结构、主角类型、钩子类型至少两项不同；推荐方案找 1–2 部标杆作品写“学什么/不拿什么”，存 `STORYLINES.md`。产品发布、功能演示、一镜到底类：“画风一行”写 `references/one_take.md` 的中心画面创意，不另出三个创意。**确认①**只问选哪个。
- **2.4 主角**：1–2 个，主角不一定是人（用户画像、产品、叙述者、拟人概念、“你”、群体）。角色卡：ID / 类型、身份、欲望、障碍、转变、视觉锚点（AI 拍真人时放在手、服装、饰物上）、一致性描述（逐字写进 `PROMPTS.md`）、可拍方式。
- **参考片拆解（用户给了参考片、参考图或以前的作品时，在 2.5 之前做）**：按 `references/reference_breakdown.md` 拆出具体色号、字体风格与大小字号比、第一眼视线落点、平均镜头时长与切换节奏、动作手感、画面质感，禁止“高级感”这类虚词；视频参考先跑 `python3 scripts/ref_analyze.py <参考片> --out <project>/ref/analysis` 自动量切分、色板 HEX、明度饱和度并出拆解拼图，字体和动作手感由人或审阅者补。结果存项目里的 `风格说明.md`，并入下面的 SPEC 和画风锁。学感觉，不照搬人物、Logo 和文案。
- **2.5 世界 + SPEC/画风锁**：场景或界面状态 3–5 个，每张卡写外观或状态、光线或配色、必有元素、禁止出现的东西、首次出现的拍；推荐一个贯穿物（钩子、中点、结尾都出现，意义有变化）；广告/产品写品牌卡。写完后按 `references/storyboard_spec.md` 派生 SPEC（LOOK / MOTION / SHOTS / BANNED / QA），按 `references/style_lock.md` 派生镜头表前的画风锁三行（画风、色板、角色）。**确认②**（≥45 秒）只发文字卡和画风锁三行，不出图、不消耗额度。
- **2.6 节拍大纲**：拍 / 时间 / 功能 / 连接词（但是、所以、于是）/ 发生了什么 / 观众得到的信息 / 张力 −5…+5 / 贯穿物状态 / 音乐段落。至少一次张力下沉；每拍新信息 ≤1，全片 ≤ 时长÷10（最少 2）；1–2 个呼吸拍。一镜到底类把“在动 / 静止 / 承接”作为附加列写进这张表。有配乐时先量拍点：`python3 scripts/beat_map.py analyze audio/music.wav --out audio/beats.json`，音乐段落列直接用它的段落（前奏 / 主歌 / 高潮 / 尾声）和小节首拍时间，拍的边界尽量落在小节首拍上。15–<45 秒、产品、动态图形并入 2.7。
- **2.7 剧本**：拍 / 画面 / 旁白·屏上字 / 声音 / 时长（教程加 操作 / 屏幕结果 / 为什么；MV 写段落-意象-情绪表）。口播约 4 字/秒，每拍 >4.5 字/秒为红；旁白占比上限口播 90%、MV 0%、其余 80%；画面已经拍出来的不再说；第一句按揭晓方式写，最后一句回扣或给出行动；事实或卖点句后标 `[F#]`；推荐至少一处声音转折。
- **2.8 切分镜**：由剧本推出镜头表，每拍 ≥1 镜，每镜只属于一拍。原列保留：`时间段` 在第一列，加 **物**（名词）、**动作**（物自己的动词）、**镜头**（停/缓推/拉远/横移/快推/砸入/切/一镜到底）、**承接**；新列：拍 / 关键（钩子、转折、高潮、结尾标“是”）/ 角色 / 场景或界面状态 / 道具或元素 / 景别 / **预定后端**（AI 视频 / AI 图 / HTML / 素材 / 标题卡，按 `references/backend_routing.md` 的预定后端速查）/ 剧本行号 / 首帧编号（第 3 步后填）。模板：`assets/storyboard.template.md`。AI 难拍的动作在这一步就换成可拍的表达。
- **2.9 故事验收**：新开一个只读文字、没参与 2.1–2.8 的审阅 agent，两轮：盲读（只给剧本 + 镜头表，用陌生人口吻复述观众问题、主角和 TA 想要什么、结尾给了什么、带走的那一句）→ 对照（再给 BRIEF、事实卡、大纲和 `story` 字段，按 `story.plan.qc` 的权重打分、查 P0）。通过线：加权平均 ≥7，所有非 N/A 项 ≥5，无 P0。记录写进 `STORY_QC.md`。不通过：只重写低分拍并复审（≤2 轮）→ 回到 2.3 换方案（≤1 次，<15 秒跳过）→ 仍不过就把分数表、低分拍和复述发给用户，二选一：(a) 按最高分版本继续并在交付说明标风险；(b) 用户调整方向或补素材后重来。

**写完 2.7/2.8 后、2.9 之前跑检查，红灯先改：**

```bash
python3 scripts/story_lint.py <project>/                                   # 节拍覆盖、阅读速度、事实来源、主角一致、关键镜头保护等
python3 scripts/storyboard_lint.py <project>/镜头表.md --target <总秒数> [--code]   # 画面规则
```

`--code` 用于代码动画路线（单镜 2–6 秒为绿），不加则按 AI 视频镜头标准。黄灯（贯穿物、声音转折、旁白占比等推荐项）可以继续，但要在交付说明里写理由。

镜头表通用要求不变：总时长 = 各镜头时长之和；一个镜头只承担一个主要动作或信息点；开头 3 秒是具体小场景而不是标题；结尾落在主物的动作上；文字主导的镜头 ≤ 30%；屏上大字每镜 ≤ 8 字；镜头时长要有变化。字幕以定稿剧本为准，不凭空补充事实；竖屏每条字幕建议 ≤ 2 行（约 16 个汉字/行 @720 宽）。

**确认③**：2.9 通过后，把剧本和镜头表一起发给用户，附审阅者的一句话复述和分数，只问“故事对不对、要改哪一拍”（它替代原来的“分镜表发给用户看一眼”；用户要看故事板时按 `references/storyboard_spec.md` 第 5 节附在这里）。用户意见只改对应的拍，`script.md` 升版本号。

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

第 3 步只细化 2.8 的预定后端（选具体模型、参数、首帧）。改了关键镜头的后端或画面动作，要回 2.8 改表，并对受影响的拍做 2.9 局部复审。用 AI 图/视频时，路由定好后把主角和世界的参考图、首帧一起发给用户确认（**确认④**），再进入试拍。

**生成工具规则**：`GenerateVideo` / `GenerateImage` 会消耗用户的生成额度。批量生成前（或一次生成多个镜头前）先告诉用户要生成几个镜头、每个大概多长，等用户确认再调用；不要编造价格；未实际调用不得声称已生成。画面里不要依赖模型写字，文字一律后期用标题卡/字幕/HTML 叠加。

## 4. 生产素材

项目目录（`video_pipeline.py init` 会建好目录和 `PROGRESS.md`）：

```text
<project>/
├── PROGRESS.md    # 进度：当前阶段、确认点结果、关键决定及理由、下一步、LESSONS（模板 assets/PROGRESS.template.md）
├── project.json
├── STORY.md 或 research/facts.md · BRIEF.md   # 立项单：简报 + 事实卡（哪些信息不能动、哪些素材必须真实）
├── world.md · outline.md                      # 故事层（按 story.plan.files）
├── 风格说明.md    # 有参考时：参考片拆解结果（并入画风锁/SPEC）
├── STORYLINES.md / script.md / 镜头表.md / STORY_QC.md   # 方案、剧本、分镜表、故事验收
├── PROMPTS.md # AI 生成时：按角色 ID 分节的提示词
├── ref/       # 参考片、参考图和 ref_analyze 输出
├── shots/     # 镜头视频（生成、已有或 html_render 渲染；多画幅按 _16x9 / _9x16 后缀分开）
├── html/      # HTML 合成镜头源文件（源码）
├── images/    # 图形卡片、首帧、参考图、截图（素材）
├── audio/     # 配音、音乐、铺底音、beats.json
├── scripts/   # 本项目的卡片渲染脚本等（保证可复现）
├── frames/    # 静帧、质检抽帧、board/<画幅>/ 看板
├── qc/        # 检查记录：每轮看板问题与改动、音频和音画同步记录（shots/QC.md 仍记逐镜验收）
└── output/    # final*.mp4 / final*.srt / 封面
```

**项目文件夹与进度（必做）**：
- **立项单**就是现有的 `STORY.md`（<45 秒）或 `research/facts.md` + `BRIEF.md`，额外写清：哪些信息不能动、哪些素材必须用真实的（界面、截图、录屏、产品图、数据）；缺素材就停下来问用户，不准自己画界面、编数据。
- **`PROGRESS.md`**：每过一个确认点（C0、①–⑥）或里程碑（试拍、批量生成完、看板通过、成片导出、交付）就更新：当前做到哪一步、各确认点结果、关键决定及理由、下一步。中途断了或新会话继续时，先读它和项目文件夹再续做。
- **项目结束时**在 `PROGRESS.md` 的 LESSONS 段汇总这次学到、能复用的规矩，并列出建议回写进 skill 的条目——只提建议，等用户同意再改 skill，不自动改。

- **HTML 镜头**：从 `assets/comp.template.html` 起步，遵守合成契约（画面只由 t 决定，暴露 `window.__seek(t)`）。先出静帧对照镜头表，再做确定性自检，最后渲染；尺寸与 `project.json` 的输出一致：

```bash
python3 scripts/html_render.py html/s01.html --out frames/s01/ --stills 0.5,1.5,2.5
python3 scripts/html_render.py html/s01.html --out /tmp/x --check-determinism
python3 scripts/html_render.py html/s01.html --out shots/s01.mp4 --size 720x1280 --fps 24 [--workers 4] [--shutter 4]
```

  单帧位移 > 80px 的快速运动开 `--shutter`。字幕不要画进 HTML，交给合成器烧录。
  动作按元素大小区分：小元素（按钮、图标、开关）短促干脆，大元素（面板、弹窗、整块画面）有重量、稳稳落定，文字进得快、停得久；在 SPEC 的 MOTION 里按类别预先定义，模板有 `snap` / `settle` / `textIn` 预设和 `hold()`，时长与缓动范围见 `references/html_motion.md` 第 6 节。每段动画做完自查：停下的那一帧观众知不知道下一眼看哪？不知道就是在和核心信息抢戏。
- **AI 生成镜头**：全片逐字复用同一段风格前缀和角色一致性描述（`PROMPTS.md`）；先确认 1–3 张关键帧（确认④），再图生视频；保留提示词与版本，失败只重做该镜头。
- **逐镜验收（必做）**：AI 生成或外部来源的每个镜头，进合成前都要单独过检验门，详见 `references/shot_qc.md`。
  1. 批量生成前先试拍 1–2 个最难的镜头（手部精细操作、转身正脸、复杂动作、多人），把试拍结果和批量要生成的镜头数、时长放在同一条消息里发给用户（**确认⑤**），确认后再批量。
  2. 技术自检并生成审片提示：`python3 scripts/shot_qc.py shots/sXX.mp4 --expect "期望画面" --duration N --width W --height H --fps F`（关键镜头加 `--key`）
  3. 把镜头和审片提示交给一个没参与生成的审阅者（看视频的 agent）：描述实际画面、符合度 0–10、逐项缺陷与秒数、可用区间。
  4. 判定：符合度 ≥ 7 且无 P0 缺陷 → 通过；P0 只在局部且可用区间 ≥ 所需 60% → 剪用该区间；否则改策略重做（复杂动作先锁关键帧再图生视频、手部只拍小幅动作、转身改拍背影或剪影），每镜最多重做 2 次，仍不过就换镜头设计或后端。
  5. **关键镜头保护**：镜头表“关键=是”的镜头，重做时不能直接“改拍背影/剪影”“改拍结果而非过程”“去掉动作”；先试锁关键帧再图生视频、首尾帧都锁、换景别、换后端（HTML、实拍素材）。确实要改动作：先改 `script.md` 对应拍，对钩子、因果链、结尾和复述做 2.9 局部复审，过了再生产；审阅者判定故事意思变了，在确认⑤的试拍汇报里告诉用户，不另增确认点。
  6. 在 `shots/QC.md`（模板 `assets/QC.template.md`）记录版本、关键、得分、缺陷、动作改动、故事复核（STORY_QC 版本号）、可用区间和处理方式；`story_lint.py` 会核对关键镜头的复核记录。
- **音频素材级验收（必做）**：每段音频生成或合成后马上全程检查，不等到成片：每条旁白、每段环境音/音效、每段音乐、要保留的镜头原声、每个中间混音轨。详见 `references/audio_qc.md`。
  1. `python3 scripts/audio_qc.py <音频或视频> --kind vo|ambient|music|mix`（可批量）。脚本逐窗扫描并标出底噪、嘶嘶声、噼啪/静电/爆音、click、削波、响度突跳的时间点，并查持续低频单音（嗡声），生成频谱图；有问题 exit 1。铺底/音乐素材报“嗡声”就换素材，不要靠混音压。
  2. 被标出的时间点逐个抽听并看局部频谱图；是瑕疵就只重做这段素材，过了再进混音；是故意的声音就在 QC 记录里写明。
  3. AI 视频镜头的原声默认静音，确要用时先单独导出音轨走这一步。
- **多画幅（选了“多画幅”时）**：同一套素材、时间轴和剧本，每个画幅在源码或工程里**单独排版**，不准把横屏成片硬裁成竖屏。
  - 竖屏做减法（每屏元素更少）、加大字号、信息从上到下排；方屏更紧凑。HTML 镜头按画幅各出一份源文件或用画幅参数分支布局，各渲一版（`shots/sXX_16x9.mp4`、`shots/sXX_9x16.mp4`）。
  - AI 视频镜头要么按各画幅分别生成，要么生成时就留出安全构图（主体居中、上下左右留余量）；不得不裁时逐镜设置 `crop_focus`，并必须过该画幅的看板。
  - `project.json` 用 `output.variants` 和逐镜 `variants` 覆盖（字段见 `references/manifest_schema.md`）；`check --aspect` 会对没有本画幅版式、比例又不符的镜头给 warning。每个画幅单独出看板、单独确认（⑥）。
- **图形卡片**：按输出分辨率画，**底部约 25% 留空给字幕**，四周留 ≥ 6% 安全边距；竖屏避开平台右侧按钮和底部文案区。
- 音乐、人物照片、商标、已有视频必须确认使用权。没有授权音乐时默认**无音乐**；确实要铺底，只用 `python3 scripts/video_pipeline.py bed audio/bed.wav --duration <秒>` 生成（带通粉噪 + 慢速起伏，无稳态音、150 Hz 以下已切）。**禁止**用 `sine`/`aevalsrc` 叠和弦或单音做持续铺底：稳态低频正弦在旁白停顿处就是“嗡嗡声”（见 `references/audio_qc.md` 案例）。任何铺底都要高通 ≥120 Hz、比旁白低 ≥20 dB。

## 5. 建立清单并合成

```bash
python3 scripts/video_pipeline.py init <project>/project.json --title "片名" --aspect 9:16
python3 scripts/video_pipeline.py check <project>/project.json --json
python3 scripts/video_pipeline.py subtitles <project>/project.json   # 可选：先看 SRT 断行
python3 scripts/video_pipeline.py build <project>/project.json       # 输出 output/final.mp4 + final.srt
```

**有配乐时：卡拍（在合成前）**：切镜头、音效、画面变化用同一套时间点。
- 用 2.6 量出的 `audio/beats.json`；`python3 scripts/beat_map.py snap audio/beats.json --project <project>/project.json --fps <fps>` 报告每个切点离最近拍点（`--to beat|downbeat|half`）的偏差和建议时长，按建议改 `duration`；标“需人工”的切点换拍、改镜头时长，或在 QC 记录里写明不卡的理由。
- HTML 动态合成里，音效触发时间和 `__seek(t)` 用的关键帧时间读同一份时间表（beats.json 或项目时间表 JSON），不要各写一遍常数；动作的“落定”时刻对齐拍点。
- 合成后按 `references/audio_qc.md`「音画同步」抽查（≥3 个卡拍点 + 每个关键音效，偏差 ≤1 帧合格）。

**全片缩略图看板（正式合成前必做）**：`check` 通过后先出低清预览再出看板，规则见 `references/quality_control.md`「全片缩略图看板」：

```bash
python3 scripts/video_pipeline.py build <project>/project.json --preview [--aspect 9:16 | --all]   # 半分辨率预览 preview_*.mp4
python3 scripts/storyboard_board.py --project <project>/project.json --preview [--aspect 9:16] [--key s03,s07]
```

看板在第 0/1/2 秒、每镜中点、关键镜头、每个转场中间帧、最后一帧各截一帧，另出手机尺寸版。审阅者按 5 个问题看（手机大小字看得清吗 / 界面数据是真的吗 / 转场中间帧糊不糊 / 前 2 秒有没有钩子 / 最后一帧能不能当封面），**只挑最严重的 3 个问题**，标清第几秒、怎么改，只重做和重渲这几段，不整片重来；记进 `qc/`。多画幅时每个画幅各出一张、单独过审，并在⑥里让用户逐个画幅确认。通过后再正式 `build`（多画幅用 `--all`）。

- 支持 `video` / `image` / `title` 三类镜头；`output.aspect` 可用 `16:9`、`9:16`（720×1280）、`9:16-hd`（1080×1920）、`1:1`、`4:5`。
- 字幕和标题卡用 libass 渲染（按字体族名选中简体中文字形），字号/边距单位是**输出像素**；自动中文断行（不拆英文单词、优先在标点处断行、避免单字孤行）。
- 图片默认 `fit: cover`，可设 `contain`；`motion: zoom_in / zoom_out` 做缓慢推拉。
- 字段见 `references/manifest_schema.md`，命令见 `references/api_reference.md`，模板 `assets/project.template.json`。

**混音规范**（自写混音脚本时同样适用，细节见 `references/audio_qc.md`）：所有轨先统一 48 kHz 和声道布局；各轨先调增益留 headroom（铺底比旁白低 15–20 dB，停顿处也要低 ≥15 dB；`music.volume` 不要 >1），铺底/音乐先高通 ≥120 Hz（`video_pipeline.py` 默认 150 Hz），有旁白时对铺底做侧链压缩避让（默认开），`amix` 用 `normalize=0`，后接 `alimiter` 兜底；响度用 loudnorm 两遍法（第二遍带测量值、`linear=true`），不用单遍动态模式；旁白每段首尾 10–30 ms 淡入淡出；环境音循环或拼接用 `acrossfade`，段间电平用包络过渡，不用阶跃开关；合成噪声类音效（火、雨等）不要用 `gt(random(1),…)` 这类硬开关随机门，每个脉冲要有起落包络。

**成片音频验收（必做）**：每次导出成片后马上跑，和素材级验收一样必做、不能互相替代：

```bash
python3 scripts/audio_qc.py output/final.mp4 --kind mix --lufs -16 --tp -1 --out output/qc
```

要求削波样本数 0、真峰值 ≤ −1 dBTP、积分响度在目标 ±1 LU 内、没有噼啪/嘶嘶/底噪/突跳标记，也没有**嗡声**（持续低频单音）、**停顿处铺底过响**、**停顿处低频偏高**这三类 P0（有旁白分轨时加 `--vo <旁白.wav>` 定位停顿最准）。被标出的时间点要抽听，回到素材级找出是哪条轨或哪一步混音造成的，修源头后重建再查。只剩成片、没有分轨时，可用 `python3 scripts/audio_qc.py output/final.mp4 --fix-hum output/final_dehum.mp4`（先加 `--dry-run` 看滤镜链）做陷波补救；嗡声本身就是主要内容时补救无效，必须换铺底重建。

## 6. 审片、质检与迭代

**先审片**（技术检查之前）：
- 每个节拍帧用陌生人的口吻写一句话描述，并点名每个物；需要解释才认得出的是 P0。
- 按顺序看是否一镜引出一镜；出现的默认套路写进 SPEC 的 BANNED 并修掉。
- 每个切点前后 0.2 秒抽帧，查空帧、跳变、主体出画；按手机宽度看可读性；中文无方块字，字幕不越界、不遮主体、无孤字断行。
- 有配乐卡拍或音效时做音画同步抽查（`references/audio_qc.md`「音画同步」）。
- 最后用正常速度带声音完整看一遍。条件允许时派一个没参与制作的 agent 只看成片挑问题。一轮集中修完再复查。
- 对照 `references/storyboard_spec.md` 里的“做坏方式”清单和 `references/story_structures.md` 的故事层做坏方式；成片讲的故事要和通过 2.9 的那一版剧本一致。

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

列出实际存在的文件路径：成片 MP4，按选择卡附上 SRT、封面、`project.json`、剧本、镜头表和素材来源说明，并报告故事验收分数、`story_lint` 黄灯及理由、所有 `[未实测]` 步骤、看板与审片/质检结果和仍未解决的问题；多画幅时每个画幅分别列出。交付后更新 `PROGRESS.md`，在 LESSONS 段写下可复用的规矩，并在回复里列出建议回写进 skill 的条目（只建议，不自动改）。不展示不存在的规划产物；未经用户明确同意不发布、不外发。

## 资源

- `scripts/video_pipeline.py`：环境自检、项目初始化（建目录 + `PROGRESS.md`）、检查、字幕、顺序合成（自动选编码器与中文字体；有音乐时铺底高通 + 侧链避让 + `alimiter` + loudnorm 两遍线性）；`bed` 生成无稳态音的轻氛围铺底；多画幅 `--aspect` / `--all`（`output.variants`、逐镜 `variants`、`crop_focus`），低清预览 `--preview`。
- `scripts/validate_video.py`：技术检查（编码、尺寸、竖屏、帧率、时长、音轨、fast-start、完整解码）。
- `scripts/html_render.py`：HTML 合成 → MP4 确定性逐帧渲染（并行、快门运动模糊、静帧、确定性自检、混音）。
- `scripts/story_lint.py`：故事层规划（`--plan` 按时长档 + 题材算步骤、文件、确认点、lint 开关、验收权重，写回 `story.plan`）与检查（节拍覆盖、阅读速度、旁白占比、因果连接、年表味、步骤完整、未实测、卖点依据、事实来源、张力、贯穿物、声音转折、主角一致、品牌资产、段落对齐、关键镜头保护、结构模板）；只用标准库，jieba 可选。
- `scripts/storyboard_lint.py`：镜头表画面规则红黄绿检查（认识 2.8 新列）。
- `scripts/shot_qc.py`：逐镜技术自检（尺寸/帧率/时长/黑帧/冻结）、抽帧拼图、生成审片提示；`--key` 附关键镜头保护提示。
- `scripts/audio_qc.py`：通用音频验收（单个素材或成片都能用）：LUFS、真峰值、削波计数，逐窗标出底噪/嘶嘶/噼啪静电/click/突跳的时间点，嗡声（持续低频单音）与停顿处铺底/低频检查，出频谱图，有问题 exit 1；`--fix-hum` 对只有成片的情况做陷波补救。
- `scripts/case_search.py`：公开案例库只读检索（返回原帖链接）。
- `scripts/ref_analyze.py`：参考片/参考图拆解：镜头切分与平均镜头长度、代表帧拆解拼图、k-means 主色板 HEX + 色板图、明度/饱和度、`风格说明` 草稿（字体与动作手感留待填）。
- `scripts/beat_map.py`：音乐卡拍：`analyze` 量 BPM、拍点、小节首拍、能量段落 → `beats.json`（numpy，librosa 可选）；`snap` 把镜头切点对齐到最近拍点并报告偏差和建议时长。
- `scripts/storyboard_board.py`：全片缩略图看板（0/1/2 秒、每镜中点、关键镜头、转场中间帧、最后一帧）+ 手机尺寸版 + 5 问审阅提示；输入 `project.json`（含低清预览）或视频 + 切点。
- `assets/project.template.json`：竖屏混合素材项目模板（含 `story` 字段）。
- `assets/comp.template.html`：HTML 合成模板（缓动、弹簧、种子随机、镜头关键帧、动作预设 `snap` / `settle` / `textIn` 与 `hold()`、`?play` / `?t=` 预览）。
- `assets/PROGRESS.template.md`：项目进度文件模板（当前阶段、立项单要点、确认点、关键决定、记录、LESSONS）。
- `assets/storyboard.template.md`：扩展镜头表示例（含拍、关键、角色、预定后端等 2.8 新列）。
- `assets/STORY.template.md`、`assets/facts.template.md`、`assets/BRIEF.template.md`、`assets/world.template.md`、`assets/outline.template.md`、`assets/script.template.md`、`assets/STORY_QC.template.md`：故事层模板。
- `assets/QC.template.md`：逐镜验收记录模板（含关键 / 动作改动 / 故事复核列）。
- `references/story_pipeline.md`：故事层 2.1–2.9、全程确认点、时长分档与题材路由、验收权重与 P0、lint 规则、常量表、示例。
- `references/story_structures.md`：8 种结构模板与原创样例、钩子库、故事层做坏方式。
- `references/storyboard_spec.md`：SPEC → 镜头表 → 故事板 → 审片 → 做坏方式（一件事与三个方案已并入 story_pipeline.md）。
- `references/style_lock.md`：画风锁（由 world.md 派生）、形容词→数字、风格卡模板、默认禁用清单。
- `references/one_take.md`：一镜到底与承接（中心画面创意并入 2.3、在动/静止/承接列并入 2.6）、操作员式运镜。
- `references/html_motion.md`：HTML 合成契约、渲染器用法、可读性数字、HyperFrames/Remotion 选型、动作按元素大小区分（时长与缓动经验值、下一眼自查）。
- `references/reference_breakdown.md`：参考片拆解 → `风格说明.md`（拆什么、写数字不写虚词、学感觉不照搬、并入画风锁）。
- `references/remotion.md`：Remotion 路线与许可提醒。
- `references/prompt_library.md`：提示词写法规律、技术路线、原创中文模板。
- `references/case_library.md`：何时查案例、怎么查、怎么用（只学结构不抄）。
- `references/backend_routing.md`：预定后端速查（2.8）、后端选择、生成额度确认、两个脚本的衔接。
- `references/prompt_playbook.md`：单镜头提示词方法。
- `references/manifest_schema.md` / `references/api_reference.md`：清单字段与命令。
- `references/shot_qc.md`：逐镜验收：试拍、缺陷清单（P0/P1）、通过/剪用/重做判定与重做策略、关键镜头保护、QC.md 列。
- `references/audio_qc.md`：音频两级验收（素材级 + 成片级）、判定标准、混音规范（48k、headroom、铺底高通 ≥120 Hz、侧链避让、alimiter、loudnorm 两遍法、淡入淡出、acrossfade、不用硬开关随机门、不用静态正弦铺底）、嗡声/停顿检查、音画同步检查和示例案例（含“嗡嗡铺底”案例）。
- `references/quality_control.md`：全片缩略图看板（5 个问题、只修最严重 3 处）、画面、声音（含音画同步）、内容（引用故事验收结果）和技术质检。
- `references/sources.md`：方法来源与许可（对外分发时保留）。
