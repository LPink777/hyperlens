#!/usr/bin/env python3
"""镜头表检查（原创实现；规则综合自 huashu-art-motion[MIT] 的镜头表要求与 OneTake 的节奏经验值）。

输入：一个 Markdown 文件，表前可写「画风：」「色板：」「角色：」三行；表格表头需含「时间段」或「时长」，
可选列：物、动作、镜头、屏上字、承接。时间段写 0–3 / 0-3 / 0:03–0:07；或只写「时长」列（秒）。

用法：python3 storyboard_lint.py 镜头表.md [--target 15] [--code]
  --target  目标总时长（秒），偏差 >10% 报黄
  --code    代码动画路线（单镜 2–6 秒为绿、6–8 黄、>8 红）；否则按 AI 视频镜头（1.5–8 秒绿、>10 红）
红灯非零退出。
"""
import argparse, re, statistics, sys

PUSH = {"缓推", "快推", "砸入", "推", "推近", "push"}
ACCENT = {"快推", "砸入", "甩", "甩镜"}
TEXTY = re.compile(r"(标题|字卡|要点|金句|文字|列表|title)", re.I)
EMPTY = re.compile(r"^(空镜|纯色|黑场|渐变底|背景)?$")
BLUEPURPLE = re.compile(r"#(?:[4-9a-f][0-9a-f][0-4][0-9a-f][a-f][0-9a-f]|[0-3][0-9a-f][0-3][0-9a-f][c-f][0-9a-f])", re.I)


def tsec(s):
    s = s.strip()
    if ":" in s:
        m, sec = s.split(":", 1); return int(m) * 60 + float(sec)
    return float(s)


def parse(path):
    head, rows, hdr = {}, [], None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        m = re.match(r"^(画风|色板|角色)[：:]\s*(.*)$", line)
        if m: head[m.group(1)] = m.group(2); continue
        if not line.startswith("|"):
            hdr = None if hdr is not None else hdr; continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if hdr is None:
            if cells and cells[0] in ("时间段", "时长", "镜号", "id") and any(k in cells for k in ("时间段", "时长")):
                if rows: break          # 只读第一张镜头表
                hdr = cells
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells): continue
        rows.append(dict(zip(hdr, cells)))
    return head, rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("md"); ap.add_argument("--target", type=float); ap.add_argument("--code", action="store_true")
    a = ap.parse_args()
    head, rows = parse(a.md)
    out = []  # (level, msg)
    R = lambda m: out.append(("红", m)); Y = lambda m: out.append(("黄", m))
    for k in ("画风", "色板", "角色"):
        if not head.get(k): Y(f"表前缺「{k}：」一行（画风锁）")
    if head.get("色板") and BLUEPURPLE.search(head["色板"]) and "允许蓝紫" not in open(a.md, encoding="utf-8").read():
        Y("色板里疑似有蓝紫色值；若题材确需，加一行「允许蓝紫：理由」")
    if not rows: R("没有解析到镜头表（表头需含「时间段」或「时长」）"); return report(out)
    durs, short = [], []
    for i, r in enumerate(rows, 1):
        if "时间段" in r and r["时间段"]:
            p = re.split(r"\s*[–—~\-至]\s*", r["时间段"])
            try: d = tsec(p[1]) - tsec(p[0])
            except Exception: R(f"镜{i} 时间段无法解析：{r['时间段']}"); d = 0
        else:
            try: d = float(re.sub(r"[^\d.]", "", r.get("时长", "")) or 0)
            except ValueError: d = 0
        durs.append(d)
        lo, hi, red = (2, 6, 8) if a.code else (1.5, 8, 10)
        if d <= 0: R(f"镜{i} 时长为 0 或缺失")
        elif d > red: R(f"镜{i} {d:.1f}s 超过 {red}s，拆镜")
        elif d > hi: Y(f"镜{i} {d:.1f}s 超过 {hi}s")
        elif d < lo: short.append(i)
        obj = r.get("物", r.get("画面里的物", None))
        if obj is not None and (EMPTY.match(obj) or TEXTY.search(obj)): R(f"镜{i}「物」是空画面或文字（{obj or '空'}）：写一个能指着说「就是它」的东西")
        act = r.get("动作", r.get("它在做什么", None))
        if act is not None and (not act or re.fullmatch(r"(出现|显示|展示|淡入|逐条出现)", act)): R(f"镜{i}「动作」写成了版面词（{act or '空'}）：写物自己的动词")
        txt = r.get("屏上字", "")
        if len(re.sub(r"\s", "", txt)) > 8: Y(f"镜{i} 屏上字 {len(txt)} 字 >8：整句交给字幕")
    if len(short) > len(rows) / 3: Y(f"短于 {(2 if a.code else 1.5)}s 的镜头有 {len(short)} 个（镜{short}）：短镜只留给连击和重音")
    if a.target and abs(sum(durs) - a.target) > 0.1 * a.target: Y(f"总时长 {sum(durs):.1f}s 与目标 {a.target}s 偏差 >10%")
    if len(durs) >= 3:
        cv = statistics.pstdev(durs) / statistics.mean(durs) if statistics.mean(durs) else 0
        if cv < 0.25: Y(f"镜头时长变异系数 {cv:.2f} < 0.25：节拍器式等长镜头，像 PPT")
    cams = [r.get("镜头", "") for r in rows]
    for i in range(len(cams) - 2):
        trio = cams[i:i + 3]
        if trio[0] and len(set(trio)) == 1 and trio[0] not in ("停", "横移"): Y(f"镜{i+1}–{i+3} 连续三镜同一镜头动作「{trio[0]}」")
        if all(c in PUSH for c in trio): Y(f"镜{i+1}–{i+3} 连续三镜往里推：推进去要有来有回")
    if cams and any(cams) and sum(c in ACCENT for c in cams) > len(cams) / 3: Y("快推/砸入/甩镜超过镜头数 1/3：重音太多")
    textish = [i for i, r in enumerate(rows, 1) if TEXTY.search(r.get("物", r.get("画面", "")))]
    if 1 in textish: R("第一镜是文字/标题主导：开头 3 秒应是一个具体小场景")
    if len(rows) in textish: Y("最后一镜是字卡：结尾尽量落在主物的一个动作上")
    if len(textish) > 0.3 * len(rows): Y(f"文字主导镜头 {len(textish)}/{len(rows)} > 30%")
    if "承接" in rows[0]:
        for i, r in enumerate(rows[:-1], 1):
            if not r.get("承接"): Y(f"镜{i}→{i+1} 没写承接（幸存者）：除连击与落版外会读成换页")
    print(f"镜头数 {len(rows)}，总时长 {sum(durs):.1f}s")
    report(out)


def report(out):
    for lv, m in out: print(f"[{lv}] {m}")
    reds = sum(1 for lv, _ in out if lv == "红")
    print("结果：" + ("红灯，先改镜头表再生产" if reds else ("黄灯，可做但需在交付说明里写理由" if out else "绿灯")))
    sys.exit(1 if reds else 0)


if __name__ == "__main__":
    main()
