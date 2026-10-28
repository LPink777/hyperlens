#!/usr/bin/env python3
"""故事层规划与检查（第 2 步 2.1–2.9，原创实现，只用 Python 标准库；jieba 可选）。

两种用法：
  1) 算步骤清单（时长档定上限、题材只删减），写回 project.json 的 story.plan：
       python3 story_lint.py --plan <project>/project.json [--dry-run] [--json]
       python3 story_lint.py --plan --genre 教程 --duration 60          # 没有 project.json 时只打印
  2) 检查故事层文件（STORY.md / script.md / outline.md / 镜头表.md / PROMPTS.md / shots/QC.md …）：
       python3 story_lint.py <project 目录或 project.json> [--genre 科普 --duration 90] [--json]

project.json 的 story 字段（都可选，命令行参数优先）：
  genre      科普 / 产品 / 广告 / 教程 / 剧情 / 口播 / 动态图形 / MV（也认 science/product/ad/tutorial/drama/talk/motion/mv）
  duration   成片秒数（不写时用 shots 的 duration 之和）
  ai_image / ai_video   是否用 AI 图 / AI 视频（决定确认④⑤和“主角一致”检查）
  silent     选择卡选了“完全无声”（关闭“声音转折”）
  has_facts  剧情/MV 片中是否出现真实事实（决定“真实”是否 N/A）
  has_protagonist      动态图形/MV 是否有拟人元素、贯穿图形或人物（决定“主角”是否 N/A）
  reveal     悬念型 / 结果先行型（不写按题材默认）
红灯非零退出；黄灯要在交付说明里写理由。规则说明见 references/story_pipeline.md。
"""
import argparse, json, math, re, sys
from pathlib import Path

# ---------------------------------------------------------------- 常量（与 story_pipeline.md 常量表一致）
WRITE_SPEED = 4.0          # 字/秒，写作速度
READ_RED = 4.5             # 字/秒，每拍阅读速度红线
NARRATION_LIMIT = {"talk": 0.90, "mv": 0.0}   # 其余 0.80
NARRATION_DEFAULT = 0.80
CAUSAL_EMPTY_MAX = 0.50
CHRONO_MAX = 0.30
ALIGN_TOL = 0.5
ACT_MIN, ACT_MAX = 30, 45
TIERS = [(0, 15, "<15"), (15, 45, "15-<45"), (45, 90, "45-<90"), (90, 10 ** 9, ">=90")]

GENRES = {
    "science": ("科普/历史", ["科普", "历史", "科普/历史", "科普历史", "science", "history"]),
    "product": ("产品发布/功能演示", ["产品", "产品发布", "功能演示", "产品发布/功能演示", "一镜到底", "product", "demo"]),
    "ad": ("广告/品牌", ["广告", "品牌", "广告/品牌", "ad", "brand"]),
    "tutorial": ("教程", ["教程", "tutorial", "howto"]),
    "drama": ("剧情短片/叙事", ["剧情", "剧情短片", "叙事", "剧情短片/叙事", "drama", "narrative"]),
    "talk": ("口播/解说", ["口播", "解说", "口播/解说", "talk", "explainer"]),
    "motion": ("动态图形/信息卡", ["动态图形", "信息卡", "动态图形/信息卡", "motion", "infographic"]),
    "mv": ("MV/氛围片", ["mv", "MV", "氛围", "氛围片", "MV/氛围", "MV/氛围片", "mood"]),
}
REVEAL = {"science": "悬念型", "product": "结果先行型", "ad": "结果先行型", "tutorial": "结果先行型",
          "drama": "悬念型", "talk": "悬念型", "motion": "二选一（写进 BRIEF）", "mv": "情绪命题"}
CORE = ["钩子", "问题/目标", "主角", "因果链", "展示", "节奏与信息密度", "结尾", "真实", "可拍性"]
SCORING = {  # 附加项, ×2 项, 可能的 N/A（项: 条件 flag）, 改判口径
    "science": (["证据支撑", "争议标注"], ["真实", "因果链", "结尾"], {}, ""),
    "product": (["操作可见", "卖点出处"], ["真实", "可拍性", "结尾"], {}, "结尾 = 给出下一步"),
    "ad": (["品牌一致", "行动合规"], ["钩子", "品牌一致", "结尾"], {}, "结尾 = 行动号召"),
    "tutorial": (["可复现", "每步有结果", "常见坑"], ["可复现", "每步有结果", "节奏与信息密度"], {},
                 "有未实测步骤时“可复现”最高 6 分"),
    "drama": (["冲突与代价", "转折"], ["主角", "因果链", "冲突与代价"], {"真实": "has_facts"},
              "问题/目标 = 能否说出“TA 想要什么、最后怎样”"),
    "talk": (["论点清楚", "反方或误区"], ["论点清楚", "展示", "真实"], {}, ""),
    "motion": (["承接", "手机可读"], ["承接", "手机可读", "节奏与信息密度"], {"主角": "has_protagonist"}, ""),
    "mv": (["卡段落", "意象回扣"], ["卡段落", "意象回扣", "结尾"], {"真实": "has_facts", "主角": "has_protagonist"},
           "问题/目标 = 情绪命题；结尾 = 情绪落点；因果链 = 段落之间的情绪递进"),
}
P0 = {
    "all": ["事实、数据或卖点错误；没有来源的说法被当成事实写"],
    "science": ["盲读复述不出观众问题", "结尾没有回答问题"],
    "talk": ["盲读复述不出观众问题", "结尾没有回答问题"],
    "product": ["复述不出“它帮观众解决了什么”", "没有行动或下一步", "卖点没有依据", "用了禁用说法"],
    "ad": ["复述不出“它帮观众解决了什么”", "没有行动或下一步", "卖点没有依据", "用了禁用说法"],
    "tutorial": ["已实测的步骤照着做失败", "关键步骤没有屏幕结果", "步骤前后矛盾"],
    "drama": ["复述不出主角想要什么", "结尾主角没有新状态"],
    "motion": ["复述不出那个核心数字或结论"],
    "mv": ["复述不出情绪命题", "画面变化和标注的音乐段落完全对不上"],
}
STRUCTURE = {  # 结构模板要求的环节（每组任一关键词出现在“功能”列即可）
    "science": [("问题", "问题|提问|悬念|钩子"), ("证据", "证据"), ("新理解", "新理解|回答|答案|结论|揭晓")],
    "product": [("卡住/痛点", "卡住|痛点|报错|麻烦"), ("产品介入", "介入|操作|演示"), ("结果", "结果"), ("下一步", "下一步|行动|号召")],
    "ad": [("痛点", "痛点"), ("卖点", "卖点"), ("行动", "行动|号召")],
    "tutorial": [("结果", "结果"), ("步骤", "步骤|第.步"), ("常见坑", "坑|误区|报错")],
    "drama": [("欲望", "欲望|想要"), ("障碍", "障碍|阻力"), ("转折", "转折"), ("新状态", "新状态|结局|代价")],
    "talk": [("观点", "观点|钩子"), ("论点", "论点"), ("误区/反方", "误区|反方"), ("结论", "结论")],
    "motion": [("数字/问题", "数字|问题"), ("对比", "对比"), ("结论", "结论")],
    "mv": [("副歌/高潮", "副歌|高潮|情绪高点")],
}
BACKENDS = ["AI 视频", "AI 图", "HTML", "素材", "标题卡"]
YES = {"是", "y", "yes", "✓", "√", "true", "1", "关键"}

# ---------------------------------------------------------------- 通用工具


def norm_genre(g):
    if not g:
        return None
    g = str(g).strip()
    for key, (_, aliases) in GENRES.items():
        if g == key or g in aliases or g.lower() in [a.lower() for a in aliases]:
            return key
    raise SystemExit(f"story_lint: 不认识的题材 {g!r}；可选：" + " / ".join(v[0] for v in GENRES.values()))


def tier_of(sec):
    for lo, hi, name in TIERS:
        if lo <= sec < hi:
            return name
    return ">=90"


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return None


def tables(text):
    """返回 [(header, rows, start_line)]，rows 为 dict 列表。"""
    out, block, start = [], [], 0
    for i, line in enumerate((text or "").splitlines() + [""]):
        s = line.strip()
        if s.startswith("|"):
            if not block:
                start = i + 1
            block.append(s)
            continue
        if len(block) >= 2:
            cells = [[c.strip() for c in b.strip("|").split("|")] for b in block]
            hdr = cells[0]
            body = [c for c in cells[1:] if not all(re.fullmatch(r":?-*:?", x) for x in c)]
            out.append((hdr, [dict(zip(hdr, c + [""] * (len(hdr) - len(c)))) for c in body], start))
        block = []
    return out


def hcol(hdr, *names):
    """表头里第一个等于或以 name 开头的列名。"""
    for n in names:
        for h in hdr:
            if h == n:
                return h
    for n in names:
        for h in hdr:
            if h.startswith(n):
                return h
    return None


def find_table(text, must, any_of=()):
    for hdr, rows, ln in tables(text):
        if all(hcol(hdr, m) for m in must) and (not any_of or any(hcol(hdr, a) for a in any_of)):
            return hdr, rows, ln
    return None


def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", (s or "").replace("−", "-").replace("＋", "+"))
    return float(m.group()) if m else None


def tsec(s):
    s = s.strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


def span(s):
    p = re.split(r"\s*[–—~\-至]\s*", (s or "").strip())
    try:
        return tsec(p[0]), tsec(p[1])
    except (ValueError, IndexError):
        return None


TAG = re.compile(r"\[(?:F\d+|未实测|示意|卖点)[^\]]*\]")


def chars(s):
    """口播字数：中文字 + 英文单词 + 数字串（标记不计）。"""
    s = TAG.sub("", s or "")
    return len(re.findall(r"[\u4e00-\u9fff]", s)) + len(re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?", s))


def sentences(s):
    return [x.strip() for x in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?(?:\s*\[[^\]]+\])*", s or "") if chars(x)]


def is_empty(s):
    return re.sub(r"[\s—\-–/·.。]", "", s or "") in ("", "无", "空")


# ---------------------------------------------------------------- 规划


def compute_plan(genre, duration, story):
    tier = tier_of(duration)
    small, mid = tier == "<15", tier == "15-<45"
    steps = []

    def step(no, name, mode, out, note=""):
        steps.append({"step": no, "name": name, "mode": mode, "file": out, "note": note})

    facts_file = "STORY.md" if (small or mid) else "research/facts.md"
    brief_file = "STORY.md" if (small or mid) else "BRIEF.md"
    world_file = "STORY.md" if (small or mid) else "world.md"
    script_file = "STORY.md" if small else "script.md"
    # 2.1
    if genre == "mv" or genre == "drama":
        step("2.1", "灵感卡" if genre == "mv" else "素材/灵感卡", "改写" if genre == "mv" else "做", facts_file,
             "参考作品（学什么/不拿什么）、情绪关键词、歌词或主题；片中有真实事实照样写 F#")
    elif genre == "tutorial":
        step("2.1", "素材与事实卡", "做", facts_file, "按教程实测规则：能在 box 上安全跑的实跑；跑不了的请用户给录屏/截图；都没有标 [未实测]")
    else:
        step("2.1", "素材与事实卡", "合并" if small else "做", facts_file, "每条 F# / 内容 / 来源 / 可信度 / 用途；至少 3 条有张力的素材")
    # 2.2
    step("2.2", "观众问题与目标（BRIEF）", "合并" if (small or mid) else "做", brief_file,
         "揭晓方式：" + (story.get("reveal") or REVEAL[genre]))
    # 2.3
    if small:
        step("2.3", "方案", "省略", "", "不出多方案；回复里用一句话提一个备选")
        plans = 1
    else:
        plans = 2 if genre == "tutorial" else 3
        note = f"出 {plans} 个方案"
        if genre == "product":
            note += "；“画风一行”换成 one_take.md 的中心画面创意，不另出三个创意"
        step("2.3", "方案（比结构）", "做", "STORYLINES.md", note)
    # 2.4 主角
    p_note = {"product": "写成用户画像 + 产品卡", "tutorial": "一行：观众“你”", "talk": "写成叙述者",
              "motion": "贯穿图形，或省略", "mv": "可省（意象可无人物）"}.get(genre, "主角 1–2 个，通用角色卡")
    p_mode = "改写" if genre in ("product", "tutorial", "talk") else ("可省" if genre in ("motion", "mv") else "做")
    if small:
        step("2.4", "主角", "一行", "STORY.md", "一行主角，同时写进画风锁“角色”行；" + p_note)
    elif mid:
        step("2.4", "主角", "合并", "STORY.md", "与 2.5 合成一张“主角与世界卡”；" + p_note)
    else:
        step("2.4", "主角", p_mode, world_file, p_note)
    # 2.5 世界
    w_note = {"science": "可简写成场景 + 贯穿物", "ad": "以品牌资产为主（品牌卡）", "talk": "写字卡样式和 B-roll 清单"}.get(
        genre, "场景或界面状态 3–5 个，复用优先")
    w_note += "；末尾派生 SPEC 与画风锁三行"
    if small:
        step("2.5", "世界", "一行", "STORY.md", "一行世界，同时写进画风锁")
    elif mid:
        step("2.5", "世界 + SPEC/画风锁", "合并", "STORY.md", "主角与世界卡；" + w_note)
    else:
        step("2.5", "世界 + SPEC/画风锁", "简写" if genre == "science" else ("改写" if genre in ("ad", "talk") else "做"),
             world_file, w_note + "；确认②只发文字卡，不出图")
    # 2.6 节拍
    merge_outline = small or mid or genre in ("product", "motion")
    if merge_outline:
        step("2.6", "节拍大纲", "并入 2.7", script_file, "剧本表加“功能”“连接词”“张力”“贯穿物状态”列" + ("（2–4 拍）" if small else ""))
    else:
        step("2.6", "节拍大纲", "做", "outline.md", "连接词 / 张力 / 贯穿物状态 / 音乐段落 列必填")
    # 2.7 剧本
    s_note = {"mv": "写成“段落-意象-情绪”表，不写旁白", "tutorial": "每一步写 操作 / 屏幕结果 / 为什么；未实测标 [未实测]"}.get(
        genre, "事实或卖点句后标 [F#]")
    step("2.7", "剧本", "改写" if genre == "mv" else "做", script_file, s_note)
    step("2.8", "切分镜（含预定后端）", "做", "镜头表.md", "新列：拍/关键/角色/场景或界面状态/道具或元素/景别/预定后端/剧本行号/首帧编号")
    step("2.9", "故事验收", "简版" if small else ("分幕 + 整片" if tier == ">=90" else "完整"),
         "STORY.md" if small else "STORY_QC.md", "盲读 → 对照两轮；独立只读审阅 agent")

    files = sorted({s["file"] for s in steps if s["file"]}, key=lambda f: [
        "research/facts.md", "STORY.md", "BRIEF.md", "STORYLINES.md", "world.md", "outline.md", "script.md",
        "镜头表.md", "STORY_QC.md"].index(f))
    ai_img, ai_vid = bool(story.get("ai_image")), bool(story.get("ai_video"))
    if ai_img or ai_vid:
        files.append("PROMPTS.md")

    conf = [{"id": "C0", "name": "选择卡", "when": "第 1 步"}]
    if not small:
        conf.append({"id": "①", "name": "选方案", "when": "2.3"})
    if tier in ("45-<90", ">=90"):
        conf.append({"id": "②", "name": "主角与世界文字卡 + 画风锁（不出图）", "when": "2.5"})
    conf.append({"id": "③", "name": "剧本 + 分镜（替代原“分镜表看一眼”）", "when": "2.9 通过后"})
    if ai_img or ai_vid:
        conf.append({"id": "④", "name": "参考图/首帧", "when": "第 3 步后端路由之后"})
    if ai_vid:
        conf.append({"id": "⑤", "name": "试拍结果 + 批量生成镜头数和时长", "when": "生产前"})

    narr = genre != "mv" and not story.get("no_narration")
    lint = {
        "节拍覆盖": True, "阅读速度": narr, "旁白占比": True,
        "因果连接": genre in ("drama", "science", "product", "ad", "talk"),
        "年表味": genre == "science", "步骤完整": genre in ("tutorial", "product"),
        "未实测": genre == "tutorial", "卖点依据": genre in ("product", "ad"),
        "事实来源": genre in ("science", "talk") or bool(story.get("has_facts") and genre in ("drama", "mv")),
        "张力变化": not small, "贯穿物": not small, "声音转折": not story.get("silent"),
        "主角一致": bool(ai_img or ai_vid) and not (genre == "mv" and not story.get("has_protagonist", False)),
        "品牌资产": genre in ("ad", "product"), "段落对齐": genre == "mv" or bool(story.get("music_sections")),
        "关键镜头保护": True, "结构模板": True, "信息密度": True,
    }

    addons, double, na_rules, note = SCORING[genre]
    na = [item for item, flag in na_rules.items() if not story.get(flag, False)]
    items = CORE + addons
    if small:
        first = next(d for d in double)
        items = [x for x in ["钩子", "结尾", "真实", first] if x in CORE + addons]
        items = list(dict.fromkeys(items))
    weights = {i: (2 if i in double else 1) for i in items if i not in na}
    reviews = 3 if small else 4
    acts = math.ceil(duration / ACT_MAX) if tier == ">=90" else 0
    return {
        "genre": genre, "genre_label": GENRES[genre][0], "duration": duration, "tier": tier,
        "reveal": story.get("reveal") or REVEAL[genre], "plans": plans, "acts": acts,
        "steps": steps, "files": files, "confirmations": conf,
        "confirmations_max": len(conf), "lint": lint,
        "qc": {"items": list(weights), "weights": weights, "na": na, "note": note,
               "pass": "加权平均 ≥ 7，所有非 N/A 项 ≥ 5，无 P0",
               "p0": P0["all"] + P0.get(genre, [])},
        "review_budget": {"whole": reviews, "per_act": 2 if acts else 0,
                          "rewrite_rounds": 2, "back_to_2_3": 0 if small else 1},
        "constants": {"write_speed": WRITE_SPEED, "read_red": READ_RED,
                      "narration_limit": NARRATION_LIMIT.get(genre, NARRATION_DEFAULT),
                      "info_points_max": max(2, int(duration // 10)), "breathing_beats": "0–2" if small else "1–2",
                      "act_len": [ACT_MIN, ACT_MAX] if acts else None, "align_tol": ALIGN_TOL},
    }


# ---------------------------------------------------------------- 检查


class Out:
    def __init__(self):
        self.items = []

    def red(self, k, m):
        self.items.append(("红", k, m))

    def yellow(self, k, m):
        self.items.append(("黄", k, m))

    def info(self, k, m):
        self.items.append(("提示", k, m))


def load_story_files(base, plan):
    names = {"facts": ["research/facts.md", "facts.md"], "story": ["STORY.md"], "brief": ["BRIEF.md"],
             "storylines": ["STORYLINES.md"], "world": ["world.md"], "outline": ["outline.md"],
             "script": ["script.md"], "shots": ["镜头表.md", "storyboard.md"], "prompts": ["PROMPTS.md"],
             "qc": ["shots/QC.md", "QC.md"], "story_qc": ["STORY_QC.md"]}
    got = {}
    for k, cands in names.items():
        for c in cands:
            t = read(base / c)
            if t is not None:
                got[k] = (c, t)
                break
    return got


def lint(base, plan, story):
    o = Out()
    g = plan["genre"]
    sw = plan["lint"]
    f = load_story_files(base, plan)
    txt = lambda k: f.get(k, (None, ""))[1]

    for name in plan["files"]:
        key = {"research/facts.md": "facts", "STORY.md": "story", "BRIEF.md": "brief", "STORYLINES.md": "storylines",
               "world.md": "world", "outline.md": "outline", "script.md": "script", "镜头表.md": "shots",
               "STORY_QC.md": "story_qc", "PROMPTS.md": "prompts"}[name]
        if key not in f:
            (o.red if key == "shots" else o.yellow)("文件", f"缺 {name}（本档位/题材应有，见 story.plan.files）")

    # ---- 节拍表与剧本
    beat_src = None
    for k in ("outline", "script", "story"):
        t = find_table(txt(k), ["拍"], ["功能", "连接词", "张力", "发生了什么"])
        if t:
            beat_src = (k, t)
            break
    script_t = None
    for k in ("script", "story"):
        t = find_table(txt(k), ["拍"], ["画面", "旁白", "操作", "意象"])
        if t:
            script_t = (k, t)
            break
    if not beat_src and script_t:
        beat_src = script_t
    beats = []
    if beat_src:
        hdr, rows, _ = beat_src[1]
        bc = hcol(hdr, "拍")
        for r in rows:
            b = r.get(bc, "").strip()
            if b and b not in beats:
                beats.append(b)
    else:
        o.red("节拍", "没找到节拍表/剧本表（需含“拍”列，以及“功能/连接词/张力/画面/旁白”之一）")

    # 每拍时长
    bdur = {}
    if script_t:
        hdr, rows, _ = script_t[1]
        bc, dc = hcol(hdr, "拍"), hcol(hdr, "时长", "预计时长")
        for r in rows:
            d = num(r.get(dc, "")) if dc else None
            if d:
                bdur[r[bc]] = bdur.get(r[bc], 0) + d
    if beat_src and not bdur:
        hdr, rows, _ = beat_src[1]
        bc, tc = hcol(hdr, "拍"), hcol(hdr, "时间")
        for r in rows:
            sp = span(r.get(tc, "")) if tc else None
            if sp:
                bdur[r[bc]] = bdur.get(r[bc], 0) + sp[1] - sp[0]

    # ---- 节拍覆盖（镜头表“拍”列）
    shots_t = find_table(txt("shots"), [], ["时间段", "时长"])
    if shots_t and sw["节拍覆盖"]:
        hdr, rows, _ = shots_t
        bc = hcol(hdr, "拍")
        if not bc:
            o.red("节拍覆盖", "镜头表没有“拍”列，无法核对每拍是否有镜头（2.8 新增列）")
        elif beats:
            used = [r.get(bc, "").strip() for r in rows]
            for i, b in enumerate(used, 1):
                if not b:
                    o.red("节拍覆盖", f"镜头表第 {i} 行没填“拍”")
                elif b not in beats:
                    o.red("节拍覆盖", f"镜头表第 {i} 行引用的拍「{b}」在节拍表/剧本里不存在")
            for b in beats:
                if b not in used:
                    o.red("节拍覆盖", f"拍「{b}」没有任何镜头")
        for col in ("关键", "预定后端"):
            if not hcol(hdr, col):
                o.yellow("镜头表", f"镜头表缺“{col}”列（2.8 新增列）")
        kc, be = hcol(hdr, "关键"), hcol(hdr, "预定后端")
        if kc and not any(r.get(kc, "").strip().lower() in YES for r in rows):
            o.yellow("镜头表", "没有任何镜头标为关键（钩子、转折、高潮、结尾）")
        if be:
            for i, r in enumerate(rows, 1):
                v = r.get(be, "").strip()
                if not v:
                    o.red("镜头表", f"第 {i} 行没填“预定后端”")
                elif not any(v.replace(" ", "").upper().startswith(x.replace(" ", "").upper()) for x in BACKENDS):
                    o.yellow("镜头表", f"第 {i} 行预定后端「{v}」不在速查表里（{'/'.join(BACKENDS)}）")
    elif not shots_t:
        o.red("节拍覆盖", "没解析到镜头表（表头需含“时间段”或“时长”）")

    # ---- 旁白相关
    total = plan["duration"]
    narr_by_beat, all_narr, sents = {}, 0, []
    if script_t:
        hdr, rows, _ = script_t[1]
        bc = hcol(hdr, "拍")
        nc = hcol(hdr, "旁白", "旁白·台词·屏上字", "台词")
        for r in rows:
            n = r.get(nc, "") if nc else ""
            narr_by_beat[r[bc]] = narr_by_beat.get(r[bc], "") + n
            all_narr += chars(n)
            sents += sentences(n)
        if sw["阅读速度"]:
            for b, n in narr_by_beat.items():
                d = bdur.get(b)
                if d and chars(n) / d > READ_RED:
                    o.red("阅读速度", f"拍「{b}」旁白 {chars(n)} 字 / {d:g}s = {chars(n)/d:.2f} 字/秒 > {READ_RED}")
                elif chars(n) and not d:
                    o.yellow("阅读速度", f"拍「{b}」没有时长，无法算阅读速度")
        lim = plan["constants"]["narration_limit"]
        ratio = (all_narr / WRITE_SPEED) / total if total else 0
        if sw["旁白占比"] and ratio > lim + 1e-9:
            o.yellow("旁白占比", f"旁白约 {all_narr} 字 ≈ {all_narr/WRITE_SPEED:.1f}s，占 {ratio:.0%}，超过 {lim:.0%}（留出无旁白的呼吸）")

    # ---- 因果连接
    if sw["因果连接"] and beat_src:
        hdr, rows, _ = beat_src[1]
        cc = hcol(hdr, "连接词")
        if not cc:
            o.yellow("因果连接", "节拍表没有“连接词”列")
        else:
            seq = [r.get(cc, "").strip() for r in rows][1:]
            bad = [x for x in seq if is_empty(x) or x in ("然后", "接着", "再")]
            if seq and len(bad) / len(seq) > CAUSAL_EMPTY_MAX:
                o.yellow("因果连接", f"{len(bad)}/{len(seq)} 拍的连接词为空或“然后”：改成“但是/所以/于是”")

    # ---- 年表味
    if sw["年表味"] and sents:
        chrono = re.compile(r"^(?:约|大约|距今|至少|在)?\s*(?:公元前?)?\s*[\d一二三四五六七八九十百千万两]+(?:\.\d+)?\s*(?:多)?\s*(?:万|千|百|亿)?\s*(?:年|世纪|年代)")
        hit = [s for s in sents if chrono.match(TAG.sub("", s).strip())]
        if len(hit) / len(sents) > CHRONO_MAX:
            o.yellow("年表味", f"{len(hit)}/{len(sents)} 句旁白以年代开头（> {CHRONO_MAX:.0%}）：改用问题和因果推进")

    # ---- 步骤完整 / 未实测
    whole = "\n".join(txt(k) for k in ("script", "story"))
    if sw["步骤完整"] and script_t:
        hdr, rows, _ = script_t[1]
        oc, rc = hcol(hdr, "操作"), hcol(hdr, "屏幕结果", "屏幕上的结果")
        if not oc or not rc:
            (o.red if g == "tutorial" else o.yellow)("步骤完整", "剧本缺“操作”或“屏幕结果”列")
        else:
            for r in rows:
                if not is_empty(r.get(oc)) and is_empty(r.get(rc)):
                    o.red("步骤完整", f"拍「{r[hcol(hdr, '拍')]}」有操作没有屏幕结果")
            if g == "tutorial":
                wc = hcol(hdr, "为什么")
                if not wc:
                    o.yellow("步骤完整", "教程剧本缺“为什么”列")
    if sw["未实测"]:
        n = len(re.findall(r"\[未实测\]", whole))
        if n:
            o.yellow("未实测", f"{n} 处 [未实测]：交付说明必须列出；“可复现”最高 6 分")

    # ---- 事实来源 / 卖点依据
    fact_ids = set(re.findall(r"\bF\d+\b", txt("facts") + "\n" + (txt("story") if not txt("facts") else "")))
    defined = set()
    for k in ("facts", "story"):
        for hdr, rows, _ in tables(txt(k)):
            c0 = hdr[0] if hdr else None
            for r in rows:
                m = re.match(r"^(F\d+)$", r.get(c0, "").strip())
                if m:
                    defined.add(m.group(1))
    defined = defined or fact_ids
    claim = re.compile(r"\d|[一二三四五六七八九十百千万亿两]+(?:万|千|百|亿)?\s*(?:年|倍|%|％|秒|分钟|小时|次|步|种|个)|最|第一|唯一|首个|首次|更快|更省|更少|提升|减少|降低|节省|比.{0,6}(?:快|多|少)")
    if (sw["事实来源"] or sw["卖点依据"]) and script_t:
        hdr, rows, _ = script_t[1]
        cols = [c for c in (hcol(hdr, "旁白", "台词"), hcol(hdr, "屏上字")) if c]
        kind = "卖点依据" if sw["卖点依据"] else "事实来源"
        for r in rows:
            for c in cols:
                for s in sentences(r.get(c, "")):
                    body = TAG.sub("", s)
                    if "[示意]" in s:
                        continue
                    if claim.search(body) and not re.search(r"\[F\d+", s):
                        o.red(kind, f"拍「{r[hcol(hdr, '拍')]}」{c}「{body.strip()[:24]}」像事实/卖点句，缺 [F#]")
        for ref in sorted(set(re.findall(r"\[(F\d+)", whole))):
            if ref not in defined:
                o.red(kind, f"{ref} 在事实卡里不存在")

    # ---- 张力变化
    if sw["张力变化"] and beat_src:
        hdr, rows, _ = beat_src[1]
        tc = hcol(hdr, "张力")
        vals = [num(r.get(tc, "")) for r in rows] if tc else []
        vals = [v for v in vals if v is not None]
        if not tc:
            o.yellow("张力变化", "节拍表没有“张力”列")
        elif len(vals) >= 2:
            d = [b - a for a, b in zip(vals, vals[1:])]
            if len(set(vals)) == 1 or all(x >= 0 for x in d) or all(x <= 0 for x in d):
                o.yellow("张力变化", f"张力 {vals} 全程单调或不变：至少安排一次下沉（卡住/失败/反差）")
            if any(v < -5 or v > 5 for v in vals):
                o.yellow("张力变化", "张力超出 −5…+5 刻度")

    # ---- 贯穿物
    if sw["贯穿物"] and beat_src:
        hdr, rows, _ = beat_src[1]
        tc = hcol(hdr, "贯穿物状态", "贯穿物")
        if not tc:
            o.yellow("贯穿物", "没有“贯穿物状态”列（推荐项；不用时在交付说明写理由）")
        elif rows:
            idx = {"第一拍": 0, "中点": len(rows) // 2, "最后一拍": len(rows) - 1}
            miss = [k for k, i in idx.items() if is_empty(rows[i].get(tc))]
            if miss:
                o.yellow("贯穿物", f"贯穿物没出现在：{'、'.join(miss)}")

    # ---- 声音转折
    if sw["声音转折"] and script_t:
        hdr, rows, _ = script_t[1]
        sc = hcol(hdr, "声音")
        if not sc:
            o.yellow("声音转折", "剧本没有“声音”栏")
        elif not any(re.search(r"静音|静|停顿|骤停|切断|戛然|音效|环境声|转折|停|断", r.get(sc, "")) for r in rows):
            o.yellow("声音转折", "没有任何一拍用声音做转折（静音、停顿、音效、环境声）")

    # ---- 主角一致
    if sw["主角一致"] and shots_t:
        hdr, rows, _ = shots_t
        rc = hcol(hdr, "角色")
        cards = {}
        for k in ("world", "story"):
            cur = None
            for line in txt(k).splitlines():
                m = re.match(r"^\s*[-*]?\s*ID\s*(?:/\s*类型)?\s*[：:]\s*([^\s/：:]+)", line)
                if m:
                    cur = m.group(1)
                    cards.setdefault(cur, "")
                    continue
                m = re.match(r"^\s*[-*]?\s*一致性描述\s*[：:]\s*(.+)$", line)
                if m and cur:
                    cards[cur] = m.group(1).strip()
        prompts = txt("prompts")
        sections = {}
        cur = None
        for line in prompts.splitlines():
            m = re.match(r"^#{2,4}\s*([^\s（(：:]+)", line)
            if m:
                cur = m.group(1)
                sections[cur] = ""
            elif cur:
                sections[cur] += line + "\n"
        if not rc:
            o.yellow("主角一致", "镜头表没有“角色”列")
        else:
            ids = set()
            for r in rows:
                for x in re.split(r"[、,，/;；\s]+", r.get(rc, "")):
                    if x and not is_empty(x):
                        ids.add(x)
            squash = lambda s: re.sub(r"\s+", "", s)
            for cid in sorted(ids):
                if cid not in cards:
                    o.red("主角一致", f"镜头表角色「{cid}」没有角色卡（world.md / STORY.md 里的“ID / 类型：”）")
                    continue
                if not cards[cid]:
                    o.red("主角一致", f"角色 {cid} 的卡里没写“一致性描述”")
                elif cid not in sections:
                    o.red("主角一致", f"PROMPTS.md 没有角色 {cid} 的分节（## {cid}）")
                elif squash(cards[cid]) not in squash(sections[cid]):
                    o.red("主角一致", f"PROMPTS.md 的 {cid} 分节没有逐字复用一致性描述")

    # ---- 品牌资产
    if sw["品牌资产"]:
        w = txt("world") or txt("story")
        m = re.search(r"^(#+)\s*.*品牌卡.*$", w, re.M)
        if not m:
            o.info("品牌资产", "没找到“品牌卡”小节，跳过色值/字体核对")
        else:
            lvl = len(m.group(1))
            rest = w[m.end():]
            stop = re.search(rf"^#{{1,{lvl}}}\s", rest, re.M)
            card = rest[:stop.start()] if stop else rest
            colors = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", card)}
            fm = re.search(r"字体\s*[：:]\s*(.+)", card)
            fonts = {x.strip().strip("\"'") for x in re.split(r"[、,，/]", fm.group(1))} if fm else set()
            used = {}
            for p in [base / "镜头表.md"] + [p for p in base.rglob("*.html") if not {"output", "work", "frames", "node_modules"} & set(p.parts)]:
                t = read(p) or ""
                for c in re.findall(r"#[0-9a-fA-F]{6}\b", t):
                    used.setdefault(c.lower(), p.name)
                for fam in re.findall(r"font-family\s*:\s*([^;\"}]+)", t):
                    for x in fam.split(","):
                        x = x.strip().strip("\"'")
                        if x and x not in fonts and x.lower() not in ("sans-serif", "serif", "monospace", "system-ui", "inherit"):
                            o.yellow("品牌资产", f"{p.name} 用了品牌卡外的字体「{x}」")
            for c, where in sorted(used.items()):
                if colors and c not in colors:
                    o.yellow("品牌资产", f"{where} 用了品牌卡外的色值 {c}")

    # ---- 段落对齐
    if beat_src:
        hdr, rows, _ = beat_src[1]
        mc, tc = hcol(hdr, "音乐段落"), hcol(hdr, "时间")
        if mc and tc and (sw["段落对齐"] or any(num(r.get(mc, "")) is not None for r in rows)):
            for r in rows:
                sp, ms = span(r.get(tc, "")), num(r.get(mc, ""))
                if sp and ms is not None and abs(sp[0] - ms) > ALIGN_TOL:
                    o.yellow("段落对齐", f"拍「{r[hcol(hdr, '拍')]}」起点 {sp[0]:g}s 与音乐段落 {ms:g}s 偏差 > {ALIGN_TOL}s")
        elif sw["段落对齐"]:
            o.yellow("段落对齐", "节拍表没有“音乐段落”或“时间”列（MV 必填，手工标注起止秒数）")

    # ---- 结构模板
    if sw["结构模板"] and beat_src:
        hdr, rows, _ = beat_src[1]
        fc = hcol(hdr, "功能", "段落")
        if not fc:
            o.yellow("结构模板", "节拍表/剧本没有“功能”列")
        else:
            funcs = " ".join(r.get(fc, "") for r in rows)
            miss = [name for name, pat in STRUCTURE[g] if not re.search(pat, funcs)]
            if miss:
                (o.info if plan["tier"] == "<15" else o.yellow)("结构模板", f"功能列缺少 {GENRES[g][0]} 模板要求的环节：{'、'.join(miss)}")

    # ---- 信息密度
    if sw["信息密度"] and beat_src:
        hdr, rows, _ = beat_src[1]
        ic = hcol(hdr, "观众得到的信息", "信息点", "信息")
        if ic:
            pts = [r for r in rows if not is_empty(r.get(ic))]
            cap = plan["constants"]["info_points_max"]
            if len(pts) > cap:
                o.yellow("信息密度", f"信息点 {len(pts)} 个 > 上限 {cap}（时长 ÷ 10，最少 2）")
            breath = len(rows) - len(pts)
            if plan["tier"] != "<15" and breath < 1:
                o.yellow("信息密度", "没有呼吸拍（不给新信息的一拍）")

    # ---- 关键镜头保护
    qc = txt("qc")
    if qc and sw["关键镜头保护"]:
        t = find_table(qc, ["关键", "动作改动"])
        versions = set(re.findall(r"v\d+(?:\.\d+)?", txt("story_qc"), re.I))
        if t:
            hdr, rows, _ = t
            kc, ac, sc = hcol(hdr, "关键"), hcol(hdr, "动作改动"), hcol(hdr, "故事复核")
            idc = hdr[0]
            for r in rows:
                if r.get(kc, "").strip().lower() in YES and r.get(ac, "").strip().startswith("是"):
                    ref = r.get(sc, "").strip() if sc else ""
                    v = re.findall(r"v\d+(?:\.\d+)?", ref, re.I)
                    if not ref:
                        o.red("关键镜头保护", f"{r.get(idc)}：关键镜头改了动作，但没有故事复核记录")
                    elif not v or not any(x.lower() in {y.lower() for y in versions} for x in v):
                        o.red("关键镜头保护", f"{r.get(idc)}：故事复核「{ref}」指向的 STORY_QC 版本不存在")
        else:
            o.yellow("关键镜头保护", "shots/QC.md 没有“关键/动作改动/故事复核”列（旧格式），无法核对关键镜头")
            for hdr, rows, _ in tables(qc):
                for r in rows:
                    line = " ".join(r.values())
                    if re.search(r"改为(?:剪影|背影)|改拍|不拍|去掉动作|改成结果", line):
                        o.yellow("关键镜头保护", f"{r.get(hdr[0])}：疑似改了动作（{re.search(r'改为(?:剪影|背影)[^）)|]*|改拍[^）)|]*|不拍[^）)|]*|去掉动作|改成结果', line).group()}），需按 2.9 局部复审")

    # ---- 复述画面（可选 jieba）
    try:
        import jieba.posseg as pseg  # noqa
        if script_t:
            hdr, rows, _ = script_t[1]
            vc, nc = hcol(hdr, "画面"), hcol(hdr, "旁白", "台词")
            if vc and nc:
                for r in rows:
                    a = {w for w, p in pseg.cut(r.get(vc, "")) if p.startswith("n") and len(w) > 1}
                    b = {w for w, p in pseg.cut(TAG.sub("", r.get(nc, ""))) if p.startswith("n") and len(w) > 1}
                    if a and b and len(a & b) / len(b) > 0.5:
                        o.yellow("复述画面（参考）", f"拍「{r[hcol(hdr, '拍')]}」旁白名词与画面重合 {len(a & b)}/{len(b)}：{'、'.join(sorted(a & b))}")
    except ImportError:
        o.info("复述画面", "未安装 jieba：复述画面改为 2.9 审阅者纯人工检查")
    return o, f


# ---------------------------------------------------------------- 入口


def load_project(arg):
    if arg is None:
        return None, {}, None
    p = Path(arg)
    if p.is_dir():
        base = p
        p = p / "project.json"
    else:
        base = p.parent
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    return (p if data is not None else None), (data or {}), base


def resolve(args, data, base):
    story = dict(data.get("story") or {})
    genre = norm_genre(args.genre or story.get("genre"))
    if not genre:
        raise SystemExit("story_lint: 需要题材：project.json 的 story.genre 或 --genre")
    dur = args.duration or story.get("duration")
    if not dur and data.get("shots"):
        try:
            dur = sum(float(s.get("duration", 0)) for s in data["shots"])
        except (TypeError, ValueError):
            dur = None
    if not dur and base:
        t = read(base / "镜头表.md")
        ends = [span(r.get(hcol(h, "时间段") or "", "")) for h, rows, _ in tables(t or "") for r in rows if hcol(h, "时间段")]
        ends = [e[1] for e in ends if e]
        dur = max(ends) if ends else None
    if not dur:
        raise SystemExit("story_lint: 需要时长：story.duration、--duration 或 shots 的 duration")
    return genre, float(dur), story


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", help="项目目录或 project.json")
    ap.add_argument("--plan", action="store_true", help="只算步骤清单并写回 story.plan")
    ap.add_argument("--dry-run", action="store_true", help="--plan 时不写回 project.json")
    ap.add_argument("--genre")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    pj, data, base = load_project(a.project)
    genre, dur, story = resolve(a, data, base)
    plan = compute_plan(genre, dur, story)

    if a.plan:
        if pj and not a.dry_run:
            data.setdefault("story", {})
            data["story"].setdefault("genre", genre)
            data["story"].setdefault("duration", dur)
            data["story"]["plan"] = plan
            pj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if a.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(f"题材 {plan['genre_label']}｜时长 {dur:g}s → 档位 {plan['tier']}｜揭晓方式 {plan['reveal']}"
                  + (f"｜分 {plan['acts']} 幕" if plan['acts'] else ""))
            for s in plan["steps"]:
                print(f"  {s['step']} {s['name']:<16} [{s['mode']}] → {s['file'] or '—'}  {s['note']}")
            print("文件：" + "、".join(plan["files"]))
            print("确认点：" + " → ".join(f"{c['id']} {c['name']}" for c in plan["confirmations"])
                  + f"（共 {plan['confirmations_max']} 次）")
            print("lint 开：" + "、".join(k for k, v in plan["lint"].items() if v))
            print("lint 关：" + ("、".join(k for k, v in plan["lint"].items() if not v) or "无"))
            q = plan["qc"]
            print("验收项（权重）：" + "、".join(f"{k}×{v}" for k, v in q["weights"].items())
                  + (f"；N/A：{'、'.join(q['na'])}" if q["na"] else "") + (f"；口径：{q['note']}" if q["note"] else ""))
            print("P0：" + "；".join(q["p0"]))
            rb = plan["review_budget"]
            print(f"审阅调用上限：整片 {rb['whole']} 次" + (f"，另每幕 ≤{rb['per_act']} 次" if rb["per_act"] else "")
                  + f"；重写复审 ≤{rb['rewrite_rounds']} 轮；回到 2.3 ≤{rb['back_to_2_3']} 次")
            if pj and not a.dry_run:
                print(f"已写回 {pj} 的 story.plan")
        return 0

    if base is None:
        raise SystemExit("story_lint: 检查模式需要项目目录或 project.json")
    o, found = lint(base, plan, story)
    if a.json:
        print(json.dumps({"tier": plan["tier"], "genre": genre, "files": {k: v[0] for k, v in found.items()},
                          "results": [{"level": lv, "check": k, "message": m} for lv, k, m in o.items]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{plan['genre_label']}｜{dur:g}s｜档位 {plan['tier']}｜读到：" + "、".join(v[0] for v in found.values()))
        for lv, k, m in o.items:
            print(f"[{lv}] {k}：{m}")
        reds = sum(lv == "红" for lv, _, _ in o.items)
        yel = sum(lv == "黄" for lv, _, _ in o.items)
        print("结果：" + ("红灯，先改故事层文件" if reds else ("黄灯，可进 2.9，但要在交付说明里写理由" if yel else "绿灯")))
    return 1 if any(lv == "红" for lv, _, _ in o.items) else 0


if __name__ == "__main__":
    sys.exit(main())
