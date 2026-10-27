# 案例库：找参考、找提示词写法

> 来源：awesome-opus5-5-videos（yihui-dev，MIT；约 700 条提示词，类别 motion / 3d / explainer / interactive，技术标签 canvas / svg / threejs / shader / gsap 等，多数附 Skillry 在线重制预览）；opus-video-prompts（joeseesun，未附许可证；54 个案例 + 18 份中文整理提示词 + 技术路线表）。
> 两个仓库里的提示词版权都归原作者。本 skill **只做检索与链接**，不把提示词全文复制进成片工程或交付物。

## 1. 什么时候查

- 用户只给了一个模糊方向（「做个酷一点的发布片」），需要 2–3 个可看的参考来对齐预期；
- 选技术路线前想看同类题材别人用了什么栈、花了多少时间/成本；
- 写提示词前想看「一句话」与「长规格」两种写法的差距。

## 2. 怎么查

```bash
python3 scripts/case_search.py --stats                                  # 看分布
python3 scripts/case_search.py 产品 发布 --limit 10                      # 关键词（全部命中）
python3 scripts/case_search.py --category explainer --tech canvas       # 按类别+技术
python3 scripts/case_search.py 线稿 --source B                           # 只查中文整理库
```
首次运行会从 GitHub 下载两个 JSON 索引并缓存 7 天（`~/.cache/video-studio-cases/`）。

## 3. 怎么用查到的案例

1. 打开原帖/重制预览看成片，只记录**可观察的特征**：时长、镜头数、节奏（静止占比、切点）、画风媒介、技术栈。
2. 按 `storyboard_spec.md` 第 2 节写「学什么 / 不拿什么」；不复制角色、设计、具体镜头、logo、字体、旋律。
3. 提示词只学结构（给了哪些约束、禁用了什么、是否先要分镜），用 `prompt_library.md` 的原创模板重写。
4. 给用户看参考时附原帖链接与作者名。

## 4. 从案例里读出的共性（供估时估价）

- 产物几乎都是「代码画每一帧 → 无头浏览器截帧 → FFmpeg 合成」，擅长动态图形/手绘/科普/发布片，不擅长写实真人。
- 一句话短片可在几分钟内出；几分钟的 MV/长解说常需 30 分钟以上和多轮迭代，成本可高一到两个数量级。
- 高质量案例普遍：先要分镜或节拍表、给参考图/参考片、写禁用清单、渲染前自查关键帧。
