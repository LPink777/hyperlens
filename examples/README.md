# 故事层试跑

两支只写到剧本和分镜，不生成镜头、不合成。复制 `hyperlens/` 到智能体技能目录时不要带上这个目录。

| 目录 | 题材 | 时长 | 路线 |
|---|---|---|---|
| `01-selection-card/` | 功能演示 | 30 秒，9:16 | 全部 HTML，确认 C0、①、③ |
| `02-cognitive-revolution/` | 科普 | 90 秒，16:9，两幕 | 预定 AI 图/视频，但本试跑不调用；确认到⑤只写在 plan 里 |

在 `hyperlens/` 下重跑：

```bash
python3 scripts/story_lint.py ../examples/01-selection-card
python3 scripts/storyboard_lint.py ../examples/01-selection-card/镜头表.md --target 30 --code
python3 scripts/story_lint.py ../examples/02-cognitive-revolution
python3 scripts/storyboard_lint.py ../examples/02-cognitive-revolution/镜头表.md --target 90
```

耗时、审阅次数和因此改过的 lint 规则见 `TRIAL.md`。
