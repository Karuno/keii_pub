"""15_skeleton_analysis.py — 案件骨格パターン分析

inventory/element_skeleton.tsv（11_enumerate_elements.py 出力）を分析し、
35 unique skeletons から:
  1. canonical 並び順（ノーマライズ後）の同定
  2. 揺れ点の同定（PRIO_DEF の位置・ALIAS の有無 等）
  3. 出願種別との関連付け（INTRO subs と骨格の関連）
  4. 多世代分割など特殊骨格の独立分類

出力:
  inventory/skeleton_analysis.md
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
INV_DIR = HERE / "inventory"
SKEL_TSV = INV_DIR / "element_skeleton.tsv"
CATALOG_TSV = INV_DIR / "element_catalog.tsv"


def load_skeletons() -> list[tuple[str, list[str]]]:
    """[(case_id, [category, category, ...])]"""
    out: list[tuple[str, list[str]]] = []
    for line in SKEL_TSV.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        case_id = cols[0]
        skel = cols[2].split("→") if cols[2] else []
        out.append((case_id, skel))
    return out


def load_intro_subs() -> dict[str, list[str]]:
    """case_id → 該当する intro subs。catalog から取り出す。"""
    out: dict[str, list[str]] = defaultdict(list)
    # 実装簡略化のため、INTRO 行のテキストから直接 keyword を引く
    # ここでは catalog.tsv の category="INTRO" 行の text に対して特徴を引く
    import re
    PATTERNS = [
        ("paris", r"パリ条約"),
        ("kokunai_yusen", r"優先権主張"),
        ("pct", r"国際出願日"),
        ("foreign_paper", r"外国語書面出願"),
        ("divisional", r"の一部を.*新たな(?:特許|外国語書面)出願"),
        ("multigen", r"特許法４４条１項|第[二三四五六七八九十２３４５６７８９]世代"),
        ("toshin_alias", r"当審拒絶理由"),
        ("naotail_yusenbi", r"次のとおりである。なお"),
        ("naoshrink_yusenbi", r"日（以下「優先日」"),
    ]
    for line in CATALOG_TSV.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 4 or cols[2] != "INTRO":
            continue
        case_id, _ln, _cat, text = cols[0], cols[1], cols[2], cols[3]
        for name, pat in PATTERNS:
            if re.search(pat, text):
                out[case_id].append(name)
    return dict(out)


def normalize_skeleton(skel: list[str]) -> str:
    """骨格を粗く正規化（CHRONO_A/B/C を CHRONO に統合し、長さに丸める）。"""
    out: list[str] = []
    chrono_run = 0
    for c in skel:
        if c in ("CHRONO_A", "CHRONO_B", "CHRONO_C"):
            chrono_run += 1
        else:
            if chrono_run > 0:
                out.append(f"CHRONO×{chrono_run}")
                chrono_run = 0
            out.append(c)
    if chrono_run > 0:
        out.append(f"CHRONO×{chrono_run}")
    return "→".join(out)


def main() -> None:
    INV_DIR.mkdir(exist_ok=True)
    cases = load_skeletons()
    intro_subs = load_intro_subs()

    # === 1. canonical patterns ===
    raw_sigs = Counter("→".join(s) for _, s in cases)
    norm_sigs = Counter(normalize_skeleton(s) for _, s in cases)

    # === 2. 出願種別 → 骨格 マッピング ===
    by_intro_pattern: dict[str, Counter] = defaultdict(Counter)
    for case_id, skel in cases:
        # 主特徴で分類
        subs = intro_subs.get(case_id, [])
        if "multigen" in subs:
            key = "多世代分割"
        elif "divisional" in subs:
            key = "分割（基本形）"
        elif "pct" in subs and "paris" in subs:
            key = "パリ＋PCT"
        elif "pct" in subs and "kokunai_yusen" in subs:
            key = "国内優先＋PCT"
        elif "foreign_paper" in subs:
            key = "外国語書面出願"
        elif "paris" in subs:
            key = "パリ優先のみ"
        elif "kokunai_yusen" in subs:
            key = "国内優先のみ"
        else:
            key = "通常出願"
        by_intro_pattern[key][normalize_skeleton(skel)] += 1

    # === 3. 揺れ点の集計 ===
    has_prio_def_independent = 0
    has_prio_def_inline = 0
    has_alias_toshin = 0
    has_alias_iken = 0
    has_div_head = 0
    has_analytical = 0
    has_fifty_no_2 = 0

    for case_id, skel in cases:
        for c in skel:
            if c == "PRIO_DEF":
                has_prio_def_independent += 1
                break
        subs = intro_subs.get(case_id, [])
        if "naotail_yusenbi" in subs or "naoshrink_yusenbi" in subs:
            has_prio_def_inline += 1
        if "ALIAS_TOSHIN" in skel or "toshin_alias" in subs:
            has_alias_toshin += 1
        if "ALIAS_IKEN" in skel:
            has_alias_iken += 1
        if "DIV_HEAD" in skel:
            has_div_head += 1
        if "ANALYTICAL" in skel:
            has_analytical += 1
        if "FIFTYNO2_NOTE" in skel:
            has_fifty_no_2 += 1

    # === 出力 ===
    summary: list[str] = []
    summary.append("# 案件骨格パターン分析\n")
    summary.append(f"対象: {len(cases)} 件\n")

    summary.append("## 1. 正規化骨格（CHRONO_A/B/C → CHRONO×n）\n")
    summary.append(f"unique 骨格: raw={len(raw_sigs)} / normalized={len(norm_sigs)}\n")
    summary.append("### 正規化骨格 上位（出現順）\n")
    summary.append("| 件数 | 骨格 |\n|---:|---|")
    for k, v in norm_sigs.most_common(15):
        summary.append(f"| {v} | `{k}` |")
    summary.append("")

    summary.append("## 2. 出願種別×骨格\n")
    for cat, sigs in sorted(by_intro_pattern.items(), key=lambda x: -sum(x[1].values())):
        n = sum(sigs.values())
        summary.append(f"### {cat} ({n} 件)\n")
        summary.append("| 件数 | 骨格 |\n|---:|---|")
        for sig, cnt in sigs.most_common(5):
            summary.append(f"| {cnt} | `{sig}` |")
        summary.append("")

    summary.append("## 3. 揺れ点の集計\n")
    summary.append(f"- 独立段落 PRIO_DEF（HEAD/INTRO 後にPRIO_DEF行）: {has_prio_def_independent} 件")
    summary.append(f"- INTRO 行末「なお接続」or「括弧内ネスト」: {has_prio_def_inline} 件")
    summary.append(f"- ALIAS_TOUSHIN（当審拒絶理由通知）あり: {has_alias_toshin} 件")
    summary.append(f"- ALIAS_IKEN: {has_alias_iken} 件")
    summary.append(f"- DIV_HEAD（多世代分割小見出し）: {has_div_head} 件")
    summary.append(f"- ANALYTICAL（起案者の自由分析）: {has_analytical} 件")
    summary.append(f"- FIFTYNO2_NOTE（50条の2 別行）: {has_fifty_no_2} 件")
    summary.append("")

    summary.append("## 4. canonical 順序の提案\n")
    summary.append("```")
    summary.append("HEAD                                    [必須]")
    summary.append("[多世代分割なら DIV_HEAD]")
    summary.append("INTRO                                   [必須]")
    summary.append("[多世代分割なら DIV_CHAIN×n + DIV_HEAD2 + DIV_INTRO]")
    summary.append("CHRONO×n                                [本体: Type A/B/C 混在の時系列]")
    summary.append("[ALIAS_TOUSHIN][ALIAS_IKEN]             [当審拒絶理由のある案件のみ末尾]")
    summary.append("[PRIO_DEF（独立段落型）または INTRO 内なお接続/括弧内ネスト]")
    summary.append("[ANALYTICAL（特殊・起案者裁量）]")
    summary.append("```\n")
    summary.append("優先日定義の位置は3スタイル併存:")
    summary.append("- A: INTRO 末尾「なお接続」（P1-history.md v0.4 推奨）")
    summary.append("- B: INTRO 内括弧ネスト「日（以下「優先日」という。）」")
    summary.append("- C: CHRONO 群末尾の独立段落\n")
    summary.append("→ 生成器は (A) を default、user 指示により切替可能とする")

    out_md = INV_DIR / "skeleton_analysis.md"
    out_md.write_text("\n".join(summary), encoding="utf-8")
    print(f"out: {out_md}")
    print(f"unique normalized: {len(norm_sigs)}")
    print(f"unique raw: {len(raw_sigs)}")


if __name__ == "__main__":
    main()
