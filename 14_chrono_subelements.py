"""14_chrono_subelements.py — CHRONO 行の細粒度 enum

432 行のCHRONO_A/B/C を分解し、サブ要素を網羅集計する。

集計軸:
  1. 同一日の書類結合語: 「、」 vs 「及び」 vs 「並びに」
  2. 修飾子: 「（最初）」「（最後）」「（最後）」「特許法５０条の２の通知を伴う」
  3. 同年/同月/同日 リダクションの出現
  4. 書類名の表記揺れ（提出 vs 提出書 vs 単体）
  5. インデント・接尾辞のバリエーション
  6. 拒絶査定の別称定義「（以下「原査定」という。）」の有無

入力: corpus/*.keii.txt
出力: inventory/chrono_subelements.tsv（行×サブ要素フラグ）
       inventory/chrono_subelements_summary.md
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE / "corpus"
INV_DIR = HERE / "inventory"

# === 検出ルール ===

CONNECTORS = {
    "tou": r"、",
    "oyobi": r"及び",
    "narabini": r"並びに",
}

MODIFIERS = {
    "(最初)": r"（最初）",
    "(最後)": r"（最後）",
    "（最後）": r"（最後）",  # 半角と全角揺れ
    "50_no_2": r"特許法第?\s*[5５０]\s*[0０]\s*条の\s*[2２]\s*",
    "(特許法50条の2を伴う)": r"[（(]?特許法.*[5５]\s*[0０]\s*条の\s*[2２].*伴う[）)]?",
    "(方式)": r"（方式）",
    "(自発・内容)": r"（自発・内容）",
    "(自発)": r"（自発）",  # 「（自発）」単独は稀
}

REDUCTIONS = {
    "同年": r"^[\s　]*同年",
    "同月": r"^[\s　]*同月",
    "同日": r"^[\s　]*同日",
}

DATE_PREFIX_PATTERNS = {
    "令和": r"^[\s　]*令和",
    "平成": r"^[\s　]*平成",
    "昭和": r"^[\s　]*昭和",
    "大正": r"^[\s　]*大正",
}

DOC_NAME_VARIATIONS = {
    "拒絶理由通知書": r"拒絶理由通知書",
    "拒絶査定": r"拒絶査定",
    "補正の却下の決定": r"補正の却下の決定",
    "前置報告書": r"前置報告書",
    "意見書": r"意見書",
    "手続補正書": r"手続補正書",
    "審判請求書": r"審判請求書",
    "上申書": r"上申書",
    "応対記録": r"応対記録",
    "面接": r"面接",
    "翻訳文の提出": r"翻訳文の提出",
    "国内書面の提出": r"国内書面の提出",
    "誤訳訂正書": r"誤訳訂正書",
    "原査定の謄本の送達": r"原査定の謄本の送達",
    "特許査定": r"特許査定",
}

# 末尾の「の提出」表現
SUFFIX_NO_TEISHUTU = re.compile(r"の提出$")

# 拒絶査定の別称定義
GENSASEI_ALIAS = re.compile(r"（以下「?原査定」?という。?）")

# 行頭の全角インデント（スペース数）
INDENT_PATTERN = re.compile(r"^(　*)(.*)")


def parse_line(line: str) -> dict:
    out: dict = {"text": line, "indent_chars": 0,
                 "is_paren_line": False,  # CHRONO_C 等
                 "tsuke": False, "colon_pos": -1}

    if not line.strip():
        return out

    # インデント数
    m = INDENT_PATTERN.match(line)
    out["indent_chars"] = len(m.group(1)) if m else 0

    # 「付け」あり / なし
    out["tsuke"] = "付け" in line[:30]

    # 括弧で始まる（送達情報）
    s = line.lstrip("　 ")
    out["is_paren_line"] = s.startswith("（") or s.startswith("(")

    # 接続語
    for k, pat in CONNECTORS.items():
        if re.search(pat, line):
            out[f"connector_{k}"] = 1

    # 修飾子
    for k, pat in MODIFIERS.items():
        if re.search(pat, line):
            out[f"mod_{k}"] = 1

    # リダクション
    for k, pat in REDUCTIONS.items():
        if re.search(pat, line):
            out[f"red_{k}"] = 1

    # 元号
    for k, pat in DATE_PREFIX_PATTERNS.items():
        if re.search(pat, line):
            out[f"era_{k}"] = 1

    # 書類名
    found_docs: list[str] = []
    for k, pat in DOC_NAME_VARIATIONS.items():
        if re.search(pat, line):
            found_docs.append(k)
    out["doc_names"] = ",".join(found_docs)
    out["n_docs_in_line"] = len(found_docs)

    # 「の提出」末尾
    if SUFFIX_NO_TEISHUTU.search(line.rstrip()):
        out["suffix_no_teishutu"] = 1

    # 原査定の別称
    if GENSASEI_ALIAS.search(line):
        out["genshasai_alias"] = 1

    return out


def is_chrono(line: str) -> bool:
    """この行が CHRONO 系か（簡易判定）。"""
    return bool(re.search(r"(?:令和|平成|昭和|大正|同年|同月|同日).*?[:：]", line))


def main() -> None:
    INV_DIR.mkdir(exist_ok=True)
    files = sorted(CORPUS_DIR.glob("*.keii.txt"))

    rows: list[dict] = []
    for fp in files:
        case_id = fp.stem.split("__")[0]
        text = fp.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if not is_chrono(line):
                continue
            r = parse_line(line)
            r["case_id"] = case_id
            r["lineno"] = lineno
            rows.append(r)

    # === 出力 1: TSV ===
    cols = [
        "case_id", "lineno", "indent_chars", "tsuke", "is_paren_line",
        "connector_tou", "connector_oyobi", "connector_narabini",
        "mod_(最初)", "mod_(最後)", "mod_50_no_2", "mod_(特許法50条の2を伴う)",
        "mod_(方式)", "mod_(自発・内容)", "mod_(自発)",
        "red_同年", "red_同月", "red_同日",
        "era_令和", "era_平成", "era_昭和", "era_大正",
        "n_docs_in_line", "suffix_no_teishutu", "genshasai_alias",
        "doc_names", "text",
    ]
    out_tsv = INV_DIR / "chrono_subelements.tsv"
    with out_tsv.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    # === 出力 2: summary md ===
    n = len(rows)
    summary: list[str] = []
    summary.append("# CHRONO 行 細粒度 enum レポート\n")
    summary.append(f"対象 chrono 行: {n}\n")

    # 接続語
    summary.append("## 1. 同一日結合の接続語\n")
    summary.append("| 接続語 | 件数 |\n|---|---|")
    for k in CONNECTORS:
        cnt = sum(1 for r in rows if r.get(f"connector_{k}"))
        summary.append(f"| {k} (`{CONNECTORS[k]}`) | {cnt} |")
    multi_doc_rows = [r for r in rows if r.get("n_docs_in_line", 0) >= 2]
    summary.append(f"\n複数書類を含む行: {len(multi_doc_rows)} 件")
    summary.append("")

    # 修飾子
    summary.append("## 2. 修飾子の出現\n")
    summary.append("| 修飾子 | 件数 |\n|---|---|")
    for k in MODIFIERS:
        cnt = sum(1 for r in rows if r.get(f"mod_{k}"))
        if cnt > 0:
            summary.append(f"| {k} | {cnt} |")
    summary.append("")

    # リダクション
    summary.append("## 3. 同年/同月/同日 リダクション\n")
    summary.append("| 種類 | 件数 |\n|---|---|")
    for k in REDUCTIONS:
        cnt = sum(1 for r in rows if r.get(f"red_{k}"))
        summary.append(f"| {k} | {cnt} |")
    full_era = sum(1 for r in rows if any(r.get(f"era_{e}") for e in DATE_PREFIX_PATTERNS))
    summary.append(f"\nフル元号表記（同年同月同日でない）: {full_era} 件")
    summary.append("")

    # インデント
    summary.append("## 4. 行頭インデント（全角スペース数）\n")
    indent_cnt = Counter(r["indent_chars"] for r in rows)
    summary.append("| スペース数 | 件数 |\n|---:|---:|")
    for k in sorted(indent_cnt):
        summary.append(f"| {k} | {indent_cnt[k]} |")
    summary.append("")

    # 書類名 (上位)
    summary.append("## 5. 書類名の出現上位\n")
    doc_cnt = Counter()
    for r in rows:
        for d in r.get("doc_names", "").split(","):
            if d:
                doc_cnt[d] += 1
    summary.append("| 書類名 | 件数 |\n|---|---|")
    for k, v in doc_cnt.most_common():
        summary.append(f"| {k} | {v} |")
    summary.append("")

    # 「の提出」末尾
    summary.append("## 6. 文末の表現\n")
    n_teishutu = sum(1 for r in rows if r.get("suffix_no_teishutu"))
    n_genshasai = sum(1 for r in rows if r.get("genshasai_alias"))
    summary.append(f"- 末尾「の提出」: {n_teishutu} 件")
    summary.append(f"- 「（以下「原査定」という。）」: {n_genshasai} 件\n")

    # 同一日結合の代表的組合せ
    summary.append("## 7. 同一日結合の典型パターン（doc_names ペア）\n")
    pair_cnt = Counter()
    for r in rows:
        names = sorted(r.get("doc_names", "").split(","))
        names = [n for n in names if n]
        if len(names) >= 2:
            pair_cnt[",".join(names)] += 1
    summary.append("| 組合せ | 件数 |\n|---|---|")
    for k, v in pair_cnt.most_common(20):
        summary.append(f"| `{k}` | {v} |")
    summary.append("")

    # 修飾子と書類名の関連（拒絶理由通知書 + (最後) など）
    summary.append("## 8. 修飾子の文脈\n")
    for mod_key in ["(最後)", "(最初)", "(方式)", "(自発・内容)"]:
        rs = [r for r in rows if r.get(f"mod_{mod_key}")]
        if not rs:
            continue
        docs = Counter()
        for r in rs:
            for d in r.get("doc_names", "").split(","):
                if d:
                    docs[d] += 1
        summary.append(f"### {mod_key} ({len(rs)} 件)")
        summary.append("関連書類:")
        for d, c in docs.most_common(5):
            summary.append(f"- {d}: {c} 件")
        summary.append("")

    out_md = INV_DIR / "chrono_subelements_summary.md"
    out_md.write_text("\n".join(summary), encoding="utf-8")

    print(f"chrono rows: {n}")
    print(f"out: {out_tsv}")
    print(f"out: {out_md}")


if __name__ == "__main__":
    main()
