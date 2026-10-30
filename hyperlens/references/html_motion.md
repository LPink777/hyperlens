# HTML 动态合成 → MP4（代码出片路线）

适用：动态图形、动态文字、产品/UI 演示、信息图与数据、线稿/手绘/水墨风解说、歌词 MV、片头片尾。
不适用：写实真人与实拍质感（交给 GenerateVideo 或实拍素材，再用本路线做包装和字幕）。

> 方法来源（思路已用自己的话重写）：HyperFrames（heygen-com/hyperframes，Apache-2.0）的「按时间轴逐帧 seek 渲染」契约；
> OneTake（feitangyuan/onetake，PolyForm 非商用许可，仅借鉴思路，未复制代码/文字）的「一切值都是 t 的纯函数、快门运动模糊、并行浏览器」；
> huashu-art-motion（alchaincyf，MIT）的「参数化片段、抽帧对表、门禁」；opus-video-prompts 的「确定性渲染」经验总结。

## 1. 合成契约（必须遵守）

一个镜头或整片 = 一个 HTML 文件，页面暴露：

| 接口 | 说明 |
|---|---|
| `window.__seek(t)` | 必需。把画面摆到第 `t` 秒；可返回 Promise |
| `window.__meta` | 可选。`{duration, width, height, fps}`，命令行参数优先 |
| `window.__ready` | 可选。字体、图片、模型加载完成后 resolve |

硬规则：
1. **画面只由 t 决定**：同一个 t seek 两次必须逐像素一致。不用 `Date.now()`、`setTimeout`、`requestAnimationFrame` 驱动动画，不用 CSS `transition`/`animation`、Tailwind 动画类；随机数一律用种子（模板里的 `rng(seed)`）。
2. **帧与帧之间不保存状态**：物理/粒子等模拟先以固定种子、固定步长预算好，再按 t 采样。否则并行渲染会闪。
3. **资源本地化**：字体、图片放项目目录用相对路径；渲染时不发网络请求。中文字体用开源字体（如 Noto Sans/Serif CJK、思源），加载完成后再 resolve `__ready`。
4. **变换用 transform**，不要动画 `left/top/width/height` 造成重排抖动（模板里的简单示例除外）。
5. **预览与渲染同源**：`?play` 循环预览、`?t=1.5` 定格，渲染器只调用 `__seek`。

## 2. 渲染器：`scripts/html_render.py`

```bash
# 一次性安装（稳定 venv，html_render.py 会自动找到它，无需激活）
python3 -m venv ~/.venvs/video-studio && ~/.venvs/video-studio/bin/pip install playwright
~/.venvs/video-studio/bin/python -m playwright install chromium
# 其他位置的 venv：export VIDEO_STUDIO_PYTHON=/path/to/venv/bin/python
python3 scripts/html_render.py comp.html --out shots/s01.mp4                       # 读 __meta
python3 scripts/html_render.py comp.html --out shots/s01.mp4 --size 1080x1920 --fps 30 --duration 4
python3 scripts/html_render.py comp.html --out shots/s01.mp4 --workers 4           # 并行浏览器
python3 scripts/html_render.py comp.html --out shots/s01.mp4 --shutter 4           # 运动模糊（耗时 ×4）
python3 scripts/html_render.py comp.html --out stills/ --stills 0.5,2,3.5          # 先看静帧再出片
python3 scripts/html_render.py comp.html --out x --check-determinism               # 确定性自检，失败非零退出
```

- 原理：无头 Chromium 逐帧 `__seek(t)` → PNG 截图 → 管道送 ffmpeg 编码（优先 libx264，没有则 libopenh264）。
- `--workers N`：把帧区间切成 N 段并行渲染后无损拼接；前提是第 1 节的确定性。
- `--shutter N`：每帧在半个帧间隔内取 N 个子帧，用 ffmpeg `tmix` 平均，相当于 180° 快门。**单帧位移超过约 80px 的快速运动要开**，否则会频闪成多个重影；CSS blur 不能代替。
- 输出 JSON 报告（帧数、时长、耗时、页面 JS 报错）。页面有 JS 报错时必须看图确认。
- 出来的 MP4 作为 `type: video` 镜头写进 `project.json`，再走原有的字幕、音乐、响度与质检流程。

估时：一张截图约 0.05–0.2 秒。15 秒 1080p30 单进程约 0.5–2 分钟；开快门 ×N。长片开工前先告诉用户预计时长。

## 3. 制作顺序

1. 先写分镜/SPEC（`references/storyboard_spec.md`），锁画风（`references/style_lock.md`）。
2. 从 `assets/comp.template.html` 复制起步：里面有缓动、弹簧、区间进度 `seg()`、种子随机、镜头关键帧 `camera()`、按元素大小区分的动作预设 `enter(t, start, 'snap'|'settle'|'textIn')` 和文字停留 `hold()`（第 6 节）。多画幅时每个画幅单独排版（竖屏做减法、加大字号、从上到下），按画幅各渲一版。
3. 每个镜头先出 3–6 张静帧对照分镜，再出片；不满意只重做该镜。
4. 跑 `--check-determinism`，再正式渲染。
5. 进 `project.json` 合成，跑 `validate_video.py`。

## 4. 节奏与可读性数字（起点，不是配额）

- 屏上可读文字：1080p 下字高 ≥ 28px（约短边 2.6%）；停留 ≥ 0.5 秒 + 每个词 0.3 秒；中文约「字数 ÷ 4.5 + 1.5」秒；字幕 ≥ max(1.8 秒, 语音时长 + 0.6 秒)。字多就删字，不缩字号。
- 每 2–4 秒画面上要有新事情发生；但要留出完全静止的「呼吸」段（见 `references/one_take.md`）。
- 主体在关键时刻占画面高度 ≥ 1/3（HyperFrames 建议主角占 40%）；内容在 90% 动作安全区内，关键字在 80% 标题安全区内。
- 第 0 帧就是封面：不能是黑场或只有 logo。

## 5. 可选的现成框架

| 情况 | 用什么 |
|---|---|
| 不想装 Node 框架、要完全可控 | 本节的 html_render.py + 模板 |
| 要 GSAP 时间轴、组件库、可视化编辑与多人协作 | HyperFrames：`npx hyperframes init / check / snapshot / render`（Node ≥ 22，Apache-2.0） |
| 团队已用 React、要参数化批量出片 | Remotion（见 `references/remotion.md`，注意其许可） |

HyperFrames 契约要点（用自己的话概括）：每个定时元素用 `class="clip" data-start data-duration` 声明出现区间；每个合成只建一个暂停的时间轴并注册到 `window.__timelines`；不要动画 `display/visibility`；媒体不要自己 play/seek；资源用根相对路径；渲染前跑 `check`，再用 `snapshot --review` 出联系表审片。

## 6. 动作按大小区分（经验值，起点不是配额）

元素越小，动作越短越干脆；元素越大，越要有重量、稳稳落定；文字进得快、停得久。在 SPEC 的 MOTION 行里**按元素类别**预先定好，全片同类元素用同一组参数（`assets/comp.template.html` 的 `MOTION` / `enter()` 预设：`snap` / `settle` / `textIn`）。

| 类别 | 例子 | 时长（经验值） | 缓动 | 位移 / 缩放 | 过冲 |
|---|---|---|---|---|---|
| 小元素 `snap` | 按钮、图标、开关、勾选、光标点击 | 120–220 ms（30fps 约 4–7 帧） | outCubic / outQuart | 位移 ≤ 16px，缩放 0.9→1 | 无，或 ≤ 3% |
| 大元素 `settle` | 面板、弹窗、卡片、整块画面、镜头推拉 | 450–750 ms（约 14–22 帧） | 临界阻尼（`1-(1+ωx)e^{-ωx}`）或 outQuint；弹簧阻尼比 ≥ 0.8 | 位移 40–120px 或 8–15% 画面 | 无，或 ≤ 2% 后静止 |
| 文字进场 `textIn` | 标题、标注、数字 | 进场 250–400 ms | outQuart | 位移 ≤ 24px，不缩放、不逐字乱跳 | 无 |
| 文字停留 | 同上 | ≥ 字数 ÷ 4.5 + 1.5 秒（模板 `hold()`） | 静止 | 被阅读时不动 | — |
| 退场 | 任意 | 进场时长的 60–80% | inCubic | 同进场方向的小位移 | 无 |

- 同一时刻只有一个主动作；次要元素晚 100–200 ms 启动、走 50–70% 的距离（见 `storyboard_spec.md` 第 3 节构图数字）。
- 卡拍时，动作的“落定”时刻（不是开始时刻）对齐拍点；音效触发与画面时间戳来自同一份时间表（见 SKILL.md 第 5 步“音乐卡拍”）。
- 俏皮风格可以把小元素换成阻尼 0.45–0.6 的弹簧（`style_lock.md` 第 2 节），但大元素仍然稳定落定。
- **每段动画做完自查**：动作停下的那一帧，观众知不知道下一眼该看哪？说不出来，就是动作在和核心信息抢戏——减少同时在动的元素、缩短装饰动作，或让强调色只落在一个焦点上。
