"""93_diff_categorize.py — 生成 vs corpus 差分を系統分類

batch_generate/{case}.diff.txt を読み、各差分を「流儀差異 vs バグ」で分類する。

カテゴリ:
  STYLE_no_shutsugan    「の出願」(generator) vs 「の特許出願」(corpus)
  STYLE_paren_pos       括弧位置（出願日後 vs であって後）
  STYLE_connector       「、」(generator) vs 「及び」(corpus)
  STYLE_prio_def        優先日定義スタイル（なお接続/括弧ネスト/独立段落）
  STYLE_zenchi_omit     前置報告書省略（corpus にない）
  STYLE_zenchi_include  前置報告書あり（corpus にある）
  STYLE_kyozetsu_saigo  （最後）の有無
  BUG_classify          INTRO パターン誤判定
  BUG_other             その他

出力: inventory/batch_generate/_diff_categorize.md
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
INV_DIR = HERE / "inventory"
BATCH_DIR = INV_DIR / "batch_generate"


def categorize_diff(diff_text: str) -> list[str]:
    cats: list[str] = []
    # 削除/追加行ペアを取り出す
    minus_lines = [l[1:] for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---")]
    plus_lines = [l[1:] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
    minus_blob = "\n".join(minus_lines)
    plus_blob = "\n".join(plus_lines)

    # 「の出願」 vs 「の特許出願」
    if "の出願であって" in minus_blob and "の特許出願であって" in plus_blob:
        cats.append("STYLE_no_shutsugan→tokkyo_shutsugan")
    if "の特許出願であって" in minus_blob and "の出願であって" in plus_blob:
        cats.append("STYLE_tokkyo_shutsugan→no_shutsugan")

    # 括弧位置
    if re.search(r"日（パリ条約", minus_blob) and re.search(r"であって（パリ", plus_blob):
        cats.append("STYLE_paren_pos→deatte_after")
    if re.search(r"であって（パリ", minus_blob) and re.search(r"日（パリ条約", plus_blob):
        cats.append("STYLE_paren_pos→date_after")

    # 接続語: 「、」 vs 「及び」
    if "、手続補正書の提出" in minus_blob and "及び手続補正書の提出" in plus_blob:
        cats.append("STYLE_connector_oyobi")
    if "及び手続補正書の提出" in minus_blob and "、手続補正書の提出" in plus_blob:
        cats.append("STYLE_connector_tou")

    # 優先日定義スタイル
    if "なお、" in minus_blob and "以下、" in plus_blob and "優先日" in (minus_blob + plus_blob):
        cats.append("STYLE_prio_def→C_独立段落")
    if "（以下「優先日」" in plus_blob and "なお、" in minus_blob:
        cats.append("STYLE_prio_def→B_括弧ネスト")

    # 前置報告書 省略 vs 出力
    if re.search(r"前置報告書", minus_blob) and not re.search(r"前置報告書", plus_blob):
        cats.append("STYLE_zenchi_omit_in_corpus")
    if re.search(r"前置報告書", plus_blob) and not re.search(r"前置報告書", minus_blob):
        cats.append("STYLE_zenchi_added_in_corpus")

    # 上申書 余分
    if re.search(r"上申書の提出", minus_blob) and not re.search(r"上申書", plus_blob):
        cats.append("STYLE_joushin_omit_in_corpus")

    # 「（最後）」の有無
    if re.search(r"拒絶理由通知書（最後）", minus_blob) and not re.search(r"拒絶理由通知書（最後）", plus_blob):
        cats.append("STYLE_saigo_omit_in_corpus")
    if re.search(r"拒絶理由通知書（最後）", plus_blob) and not re.search(r"拒絶理由通知書（最後）", minus_blob):
        cats.append("STYLE_saigo_added_in_corpus")

    # BUG: 参照エラー出力 = classify バグ等
    if "<<参照エラー" in minus_blob:
        cats.append("BUG_reference_error")

    # 多世代分割の判定差
    if "の一部を" in plus_blob and "次のとおりである" in plus_blob:
        # corpus が分割言及, 生成器がしてない or 別パターン
        cats.append("STYLE_or_BUG_divisional")

    # 国際出願日言及差
    if "国際出願日" in plus_blob and "国際出願日" not in minus_blob:
        cats.append("BUG_pct_mistake")

    if not cats:
        cats.append("OTHER")

    return cats


def main() -> None:
    rows: list[dict] = []
    diff_files = sorted(BATCH_DIR.glob("*.diff.txt"))
    cat_counter: Counter[str] = Counter()
    case_cats: dict[str, list[str]] = {}

    for fp in diff_files:
        case_key = fp.stem.replace(".diff", "")
        diff = fp.read_text(encoding="utf-8")
        if diff.strip() == "(identical)":
            cats = ["IDENTICAL"]
        else:
            cats = categorize_diff(diff)
        case_cats[case_key] = cats
        for c in cats:
            cat_counter[c] += 1
        rows.append({"case_key": case_key, "categories": ",".join(cats)})

    # ログ TSV
    log_path = BATCH_DIR / "_diff_categorize.tsv"
    with log_path.open("w", encoding="utf-8") as f:
        f.write("case_key\tcategories\n")
        for r in rows:
            f.write(f"{r['case_key']}\t{r['categories']}\n")

    # サマリ MD
    summary: list[str] = []
    summary.append("# 差分カテゴリ集計\n")
    summary.append(f"対象: {len(rows)} 件\n")
    summary.append("## カテゴリ別件数\n")
    summary.append("| カテゴリ | 件数 |\n|---|---|")
    for k, v in cat_counter.most_common():
        summary.append(f"| `{k}` | {v} |")
    summary.append("\n## 案件別カテゴリ\n")
    summary.append("| case_key | categories |\n|---|---|")
    for r in sorted(rows, key=lambda r: r["case_key"]):
        summary.append(f"| {r['case_key']} | {r['categories']} |")

    out_md = BATCH_DIR / "_diff_categorize.md"
    out_md.write_text("\n".join(summary), encoding="utf-8")
    print(f"out: {out_md}")
    print(f"\nカテゴリ別件数:")
    for k, v in cat_counter.most_common():
        print(f"  {k:40s} {v}")


if __name__ == "__main__":
    main()
