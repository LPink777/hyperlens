# Remotion 路线（React 工程化视频、参数化批量出片）

适用：团队已有 React/TypeScript 能力；同一模板要换数据批量出片（多语言、多 SKU、周报）；需要在 Studio 里可视化微调时间轴。
不适用：一次性短片且不想装 Node 生态 → 用 `references/html_motion.md` 的 HTML 路线。

> 来源：Remotion 官方 Agent Skills（remotion-dev/skills，镜像自 remotion-dev/remotion 的 packages/skills）。该仓库未单独附许可证，Remotion 本体使用自有的 **Remotion License**：个人、≤3 名员工的营利公司、非营利组织可免费（含商用）使用；更大的营利公司需购买公司许可。本文只用自己的话概括做法，不复制原文。**为企业客户出片前先确认许可。**

## 1. 起步与预览

```bash
npx create-video@latest            # 或 bun create video；创建时可选择安装官方 agent skills
npx remotion studio                # 先开 Studio 预览，边写边看；用户说「做视频」时默认给预览，不急着渲染
npx remotion render <合成ID> out/video.mp4
npx remotion still <合成ID> out/frame.png --frame=45
npx remotion render <合成ID> out/frames --frames=0,30,90 --image-format=png   # 抽帧审片
```
官方 skills 安装：`npx skills add remotion-dev/skills`。

## 2. 写法要点（概括）

- **动画只由帧号驱动**：用 `useCurrentFrame()` + `interpolate()`（显式帧区间，必要时 `extrapolateLeft/Right: "clamp"`），缓动用 `Easing.bezier()` / 弹簧。CSS `transition`/`animation` 与 Tailwind 动画类**渲染不出来**，必须改写。
- **秒换帧用 fps**：在组件里从 `useVideoConfig()` 取 `fps`，写成 `from={2 * fps}`、`durationInFrames={4 * fps}`，改帧率时不会错位。
- `useCurrentFrame()` 放在最小的需要它的组件里，避免整场景每帧重渲染。
- **结构**：`<Composition>` 注册宽高、fps、时长和默认参数；`<Sequence>` 给一组图层共享起点/裁剪；`<Series>`/`TransitionSeries` 串场景——`Transition`（淡入淡出、滑动、擦除）会让前后两场重叠从而缩短总时长，`Overlay`（如漏光）叠在切点上不改时长。
- **可编辑性**：插值直接写在元素的 style 上、优先用独立的 `scale / translate / rotate` 属性而不是拼 transform 字符串，Studio 才能可视化改关键帧；用户在代码里做的改动视为有意，不要覆盖。
- **动态时长**：用 `calculateMetadata` 按配音/素材实际时长决定合成时长（先逐场景生成配音文件放进 `public/`，用 `staticFile()` 引用）。
- **字幕**：可导入 SRT 或用转写生成逐词时间戳，再做逐词高亮字幕。
- 其它官方专题：音频可视化、静音检测、Lottie、3D（@remotion/three）、地图、透明视频导出、字体（Google Fonts / 本地字体）、文字测量防溢出。

## 3. 与 hyperlens 的衔接

- Remotion 渲染出的 MP4 作为 `type: video` 镜头放进 `project.json`；或整片由 Remotion 出、再用 `validate_video.py` 质检。
- 镜头表、画风锁、审片流程照常适用（`storyboard_spec.md`、`style_lock.md`）。
- 已有 Remotion 工程想迁到纯 HTML：HyperFrames 提供 `remotion-to-hyperframes` 迁移技能（Apache-2.0）。

## 4. 注意

- 本盒子环境需要 Node 与 Chromium（Remotion 会自行下载 headless shell），首次安装较慢；本集成**未在本机实测 Remotion 渲染**。
- 配音示例默认用 ElevenLabs，需要 API key 与费用；可换任意能产出音频文件的 TTS，开工前向用户说明费用。
