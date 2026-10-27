#!/usr/bin/env python3
"""检索公开的「代码出片」案例库（只读索引，返回原帖链接；不转存提示词全文）。

数据源（运行时下载，不随 skill 分发）：
  A. yihui-dev/awesome-opus5-5-videos  data/videos.json（仓库 MIT；提示词版权归原作者）
  B. joeseesun/opus-video-prompts      cases.json（仓库未附许可证；仅读取索引字段）

用法：
  python3 case_search.py 产品 发布 --limit 10
  python3 case_search.py explainer --category explainer --tech canvas
  python3 case_search.py 线稿 --source B
  python3 case_search.py --stats
输出：作者、类别、技术标签、原帖链接、提示词前 120 字摘要（仅供判断是否值得点开原帖）。
"""
import argparse, json, os, sys, time, urllib.request
from collections import Counter
from pathlib import Path

SRC = {
    "A": "https://raw.githubusercontent.com/yihui-dev/awesome-opus5-5-videos/main/data/videos.json",
    "B": "https://raw.githubusercontent.com/joeseesun/opus-video-prompts/main/cases.json",
}
CACHE = Path(os.environ.get("CASE_CACHE", Path.home() / ".cache" / "video-studio-cases"))


def load(key):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{key}.json"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        try:
            with urllib.request.urlopen(SRC[key], timeout=30) as r:
                f.write_bytes(r.read())
        except Exception as e:
            if not f.exists():
                sys.exit(f"下载失败 {SRC[key]}：{e}")
    return json.loads(f.read_text())


def norm(key, x):
    if key == "A":
        return {"src": "A", "author": x.get("author"), "category": x.get("category"),
                "tech": x.get("tech_tags", []), "url": x.get("post_url"),
                "remake": x.get("skillry_url"), "text": x.get("prompt") or ""}
    return {"src": "B", "author": x.get("author"), "category": x.get("category"),
            "tech": [], "url": x.get("url"), "remake": None,
            "text": f"{x.get('title','')} · {x.get('how','')} · {x.get('cost','')}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("terms", nargs="*", help="关键词（全部命中才算，不区分大小写）")
    ap.add_argument("--source", choices=["A", "B", "all"], default="all")
    ap.add_argument("--category")
    ap.add_argument("--tech")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    keys = ["A", "B"] if a.source == "all" else [a.source]
    rows = [norm(k, x) for k in keys for x in load(k)]
    if a.stats:
        print("条目数:", len(rows))
        print("类别:", Counter(r["category"] for r in rows).most_common())
        print("技术:", Counter(t for r in rows for t in r["tech"]).most_common(15))
        return
    def hit(r):
        blob = " ".join([r["text"], r["category"] or "", " ".join(r["tech"]), r["author"] or ""]).lower()
        return (all(t.lower() in blob for t in a.terms)
                and (not a.category or (r["category"] or "").lower() == a.category.lower())
                and (not a.tech or a.tech.lower() in [t.lower() for t in r["tech"]]))
    res = [r for r in rows if hit(r)][: a.limit]
    if a.json:
        print(json.dumps([{**r, "text": r["text"][:120]} for r in res], ensure_ascii=False, indent=1)); return
    for r in res:
        print(f"[{r['src']}] {r['author']} · {r['category']} · {','.join(r['tech']) or '-'}\n    {r['url']}"
              + (f"\n    重制预览: {r['remake']}" if r['remake'] else "")
              + f"\n    摘要: {r['text'][:120].replace(chr(10), ' ')}")
    print(f"共命中 {len(res)} 条（上限 {a.limit}）。提示词全文与版权以原帖为准。")


if __name__ == "__main__":
    main()
