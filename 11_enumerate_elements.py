"""11_enumerate_elements.py — コーパスをボトムアップ分解して要素を enum

48件の .keii.txt を 1 行ずつ「行カテゴリ」に分類し、
各カテゴリ・サブ要素・順序・共起を網羅集計する。

行カテゴリ（line_category）:
  HEAD            見出し（第１　手続の経緯 / １　手続の経緯）
  INTRO           本願由来説明（本願は…次のとおりである。）
  CHRONO_A        Type A 経過行（付け：）
  CHRONO_B        Type B 経過行（　　：）
  CHRONO_C        送達情報行（括弧書き）
  ALIAS_TOUSHIN   当審拒絶理由通知の別称定義
  ALIAS_IKEN      意見書の別称定義
  PRIO_DEF        「以下、…『優先日』という。」（独立段落）
  DIV_HEAD        多世代分割の小見出し（出願分割の経緯／本願の手続の経緯）
  DIV_CHAIN       多世代分割の出願鎖（最先の出願／第N世代分割／本願）
  ANALYTICAL      起案者の分析的記述（稀）
  EMPTY           空行
  OTHER           上記いずれにも該当しない

出力:
  inventory/element_catalog.tsv     各案件×行ごとの分類結果
  inventory/element_skeleton.tsv    案件ごとの骨格（カテゴリ列の列挙）
  inventory/element_summary.md      集計レポート

CLI:
  python 11_enumerate_elements.py
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE / "corpus"
INV_DIR = HERE / "inventory"

# === 行カテゴリの正規表現（優先順） ===

# 見出し: 第１　手続の経緯 / １　手続の経緯 / 第一  手続の経緯
RE_HEAD = re.compile(r"^[\s　]*(?:第[一二三四五六七八九十1-9０-９]+[　 ]*)?[1-9０-９]?[　 ]*手続.?の経緯[　 ]*$")

# 本願由来（INTRO）: 「本願は、…(次|以下)のとおりである。」
RE_INTRO = re.compile(r"本願は[、，].*?(?:次|以下)のとおりである。?")

# 多世代分割の小見出し
RE_DIV_HEAD = re.compile(r"^[\s　]*[1-9０-９(（][)）]?[　 ]*(?:出願分割の経緯の概略|本願の出願後の手続の経緯の概略|本願の手続の経緯の概略)")

# 多世代分割の小INTRO「本願の出願後の手続の経緯の概略は、次のとおりである。」
RE_DIV_INTRO = re.compile(r"本願の(?:出願後の)?手続の経緯の概略は、次のとおりである。?")

# 50条の2付記が独立行になる例
RE_FIFTYNO2_NOTE = re.compile(r"^[\s　]*[（(][^）)]*?(?:特許法)?[第\s　]*[５5][０0]条の[２2][^）)]*?[）)]")

# 審判請求書の別称定義（手続補正書（方式）で内容補充された場合の特殊文脈）
RE_ALIAS_SHINPAN = re.compile(r"審判請求書を(?:、)?以下[、，]?(?:単に)?(?:「審判請求書」|『審判請求書』)という")

# 多世代分割の鎖
RE_DIV_CHAIN = re.compile(
    r"^[\s　]*(?:最先の出願|第[一二三四五六七八九十0-9０-９]+世代分割|本願|親出願|祖父出願)"
    r"[　 ]*[:：]"
)

# 元号: 文字列単位 alternation。空白を含む形式にも対応 (「令和　４年」)
RE_NENGOU_DATE = r"(?:(?:令和|平成|昭和|大正)[\s　]*(?:[\d０-９]{1,2}|元)年|同年|同月|同日)"

# 経過 Type A: 「…日付け：●●」
#  - 通常形: 元号 + 任意 + 日付け + ：
#  - 同日形: 「同日付け：」（同日 が「日」を含むため特例）
RE_CHRONO_A = re.compile(
    rf"^[\s　]*(?:"
    rf"{RE_NENGOU_DATE}.*?日付け[\s　]*[:：]"  # 通常
    rf"|同日付け[\s　]*[:：]"                    # 同日
    rf")"
)

# 経過 Type B: 「…日　　：●●」（付けなし、コロン直前空白）
RE_CHRONO_B = re.compile(
    rf"^[\s　]*(?:"
    rf"{RE_NENGOU_DATE}.*?日[\s　]{{1,8}}[:：]"
    rf"|同日[\s　]{{1,8}}[:：]"
    rf")"
)

# 送達情報: 「（…日　　：原査定の謄本の送達）」
#  - 元号 or 「同年/同月/同日」付き、もしくは月だけ（「（１１月　７日…）」省略形）
RE_CHRONO_C = re.compile(
    rf"^[\s　]*[（(][\s　]*(?:{RE_NENGOU_DATE}|[\d０-９]{{1,2}}月)"
    rf".*?[:：].*?[）)][\s　]*$"
)

# 当審拒絶理由通知の別称定義: 「●●●を以下『当審拒絶理由通知』という。」
RE_ALIAS_TOUSHIN = re.compile(r"(?:なお|また|以下)?[、，]?.*当審.*拒絶理由.*?(?:を以下|を、以下|、以下|、単に).*?(?:「(.+?)」|『(.+?)』).*?という。?")

# 意見書の別称定義
RE_ALIAS_IKEN = re.compile(r"(?:なお|また|以下)?[、，]?.*意見書を(?:以下|、以下|単に).*?(?:「(.+?)」|『(.+?)』).*?という。?")

# 「以下、●年●月●日を『優先日』という。」（独立段落型）
RE_PRIO_DEF = re.compile(r"以下[、，][^「」]*?を[、，]?「優先日」という。?")

# 起案者分析（自由記述・「そこで」「まず、次ぐ」「２　審判請求書において…」等）
RE_ANALYTICAL = re.compile(
    r"^[\s　]*("
    r"[2-9０-９]+[　 ]+(?:[^：]+?)(?:において|について|から|では|として)"  # 番号付き
    r"|そこで[、，]"                                                       # 「そこで、…」
    r"|まず[、，]"                                                         # 「まず、…」
    r"|請求人(?:は|が)[、，]"                                              # 「請求人は、…」
    r"|当合議体(?:は|が)[、，]"                                            # 「当合議体は、…」
    r")"
)


def classify_line(line: str) -> str:
    """主分類のみ返す（行内に複数カテゴリが共存する場合の副ラベルは別関数）。"""
    if not line.strip():
        return "EMPTY"
    # 優先順位の高い順に判定
    if RE_HEAD.search(line):
        return "HEAD"
    if RE_FIFTYNO2_NOTE.search(line):
        return "FIFTYNO2_NOTE"
    if RE_DIV_HEAD.search(line):
        return "DIV_HEAD"
    if RE_DIV_CHAIN.search(line):
        return "DIV_CHAIN"
    # 本願は…で始まる行は INTRO 優先（末尾に PRIO_DEF や ALIAS_* が含まれていても）
    if RE_INTRO.search(line):
        return "INTRO"
    if RE_DIV_INTRO.search(line):
        return "DIV_INTRO"
    # ALIAS は CHRONO より先に判定（行内に日付を含むため）
    if RE_ALIAS_SHINPAN.search(line):
        return "ALIAS_SHINPAN"
    if RE_ALIAS_TOUSHIN.search(line):
        return "ALIAS_TOUSHIN"
    if RE_ALIAS_IKEN.search(line):
        return "ALIAS_IKEN"
    if RE_PRIO_DEF.search(line):
        return "PRIO_DEF"
    if RE_CHRONO_C.search(line):
        return "CHRONO_C"
    if RE_CHRONO_A.search(line):
        return "CHRONO_A"
    if RE_CHRONO_B.search(line):
        return "CHRONO_B"
    if RE_ANALYTICAL.search(line):
        return "ANALYTICAL"
    return "OTHER"


def collect_sub_labels(line: str, primary: str) -> list[str]:
    """同一行に共存する副カテゴリを返す（INTROと同行のPRIO_DEFなど）。"""
    subs: list[str] = []
    if primary == "INTRO":
        if RE_PRIO_DEF.search(line):
            subs.append("inline_PRIO_DEF")
        if RE_ALIAS_TOUSHIN.search(line):
            subs.append("inline_ALIAS_TOUSHIN")
        if RE_ALIAS_IKEN.search(line):
            subs.append("inline_ALIAS_IKEN")
    return subs


# === INTRO 内のサブ要素抽出 ===

INTRO_SUB_RULES = [
    ("apptype_pct", r"国際出願日とする"),
    ("apptype_pct_japanese", r"を国際出願日とする日本語特許出願"),
    ("apptype_pct_foreign", r"を国際出願日とする外国語特許出願"),
    ("apptype_foreign_paper", r"の外国語書面出願"),
    ("paris_priority", r"パリ条約による優先権主張"),
    ("paris_example_priority", r"パリ条約の例による優先権主張"),
    ("kokunai_priority", r"（優先権主張[　 ]"),  # パリ・国内どちらでもこのパターンで括弧開く
    ("paris_priority_external", r"パリ条約による優先権主張外国庁受理"),
    ("divisional", r"の一部を.*新たな(?:特許|外国語書面)出願"),
    ("multigen_div_law44", r"特許法４４条１項の規定による特許出願"),
    ("phrase_no_shutsugan", r"の出願であって"),
    ("phrase_no_tokkyo_shutsugan", r"の特許出願であって"),
    ("paren_position_after_filingdate", r"日（パリ条約"),  # パターンA寄り
    ("paren_position_after_deatte", r"であって（パリ条約"),  # パターンB寄り
    ("priority_in_intro_with_naoshrink", r"日（以下「優先日」という。）"),  # ネスト型
    ("priority_in_intro_naotail", r"次のとおりである。なお[、，][^「」]*?「優先日」という"),
    ("translation_line_in_intro", r"翻訳文の提出"),  # 通常は別行だが稀に統合
]


def extract_intro_subs(intro_text: str) -> dict[str, bool]:
    return {name: bool(re.search(pat, intro_text)) for name, pat in INTRO_SUB_RULES}


# === CHRONO 内のサブ要素抽出 ===

CHRONO_DOC_RULES = [
    ("拒絶理由通知書", r"拒絶理由通知書"),
    ("拒絶理由_最初", r"拒絶理由通知書（最初）"),
    ("拒絶理由_最後", r"拒絶理由通知書（最後）"),
    ("拒絶理由_50条の2", r"特許法５０条の２.*拒絶理由通知書"),
    ("拒絶査定", r"拒絶査定"),
    ("補正の却下", r"補正の却下"),
    ("意見書", r"意見書"),
    ("手続補正書", r"手続補正書"),
    ("審判請求書", r"審判請求書"),
    ("前置報告書", r"前置報告書"),
    ("上申書", r"上申書"),
    ("応対記録", r"応対記録"),
    ("面接", r"面接"),
    ("翻訳文の提出", r"翻訳文の提出"),
    ("国内書面の提出", r"国内書面の提出"),
    ("謄本の送達", r"謄本の送達"),
    ("誤訳訂正書", r"誤訳訂正"),
    ("conn_dokuten", r"、"),
    ("conn_oyobi", r"及び"),
    ("conn_narabini", r"並びに"),
    ("genshasai_alias", r"以下「原査定」"),
]


def extract_chrono_docs(line: str) -> list[str]:
    return [name for name, pat in CHRONO_DOC_RULES if re.search(pat, line)]


def parse_keii_file(text: str) -> list[dict]:
    """各行を分類して構造化リストに。"""
    rows: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        cat = classify_line(line)
        row = {"lineno": i, "category": cat, "text": line,
               "sub_labels": collect_sub_labels(line, cat)}
        if cat == "INTRO":
            row["subs"] = extract_intro_subs(line)
        elif cat in ("CHRONO_A", "CHRONO_B", "CHRONO_C"):
            row["docs"] = extract_chrono_docs(line)
        rows.append(row)
    return rows


def main() -> None:
    INV_DIR.mkdir(exist_ok=True)
    files = sorted(CORPUS_DIR.glob("*.keii.txt"))
    print(f"corpus files: {len(files)}")

    catalog_rows: list[list] = []
    skeleton_rows: list[list] = []
    cat_counter: Counter[str] = Counter()
    intro_subs_counter: Counter[str] = Counter()
    intro_subs_examples: dict[str, list[str]] = defaultdict(list)
    chrono_doc_counter: Counter[str] = Counter()
    chrono_doc_examples: dict[str, list[str]] = defaultdict(list)
    other_lines: list[tuple[str, int, str]] = []
    skeleton_signatures: Counter[str] = Counter()
    skeleton_owners: dict[str, list[str]] = defaultdict(list)

    for fp in files:
        case_id = fp.stem.split("__")[0]
        text = fp.read_text(encoding="utf-8")
        rows = parse_keii_file(text)
        # コーパス1件分の骨格 = カテゴリ列を順に並べたもの（EMPTYは除く）
        skeleton = [r["category"] for r in rows if r["category"] != "EMPTY"]
        skeleton_signatures[",".join(skeleton)] += 1
        skeleton_owners[",".join(skeleton)].append(case_id)

        for r in rows:
            cat_counter[r["category"]] += 1
            if r["category"] == "INTRO" and "subs" in r:
                for k, v in r["subs"].items():
                    if v:
                        intro_subs_counter[k] += 1
                        if len(intro_subs_examples[k]) < 3:
                            intro_subs_examples[k].append(f"{case_id}: {r['text'][:80]}")
            if r["category"] in ("CHRONO_A", "CHRONO_B", "CHRONO_C") and "docs" in r:
                for d in r["docs"]:
                    chrono_doc_counter[d] += 1
                    if len(chrono_doc_examples[d]) < 3:
                        chrono_doc_examples[d].append(f"{case_id}: {r['text'][:80]}")
            if r["category"] == "OTHER":
                other_lines.append((case_id, r["lineno"], r["text"]))
            catalog_rows.append([case_id, r["lineno"], r["category"], r["text"]])
        skeleton_rows.append([case_id, len(skeleton), "→".join(skeleton)])

    # --- 出力 1: catalog tsv ---
    out_catalog = INV_DIR / "element_catalog.tsv"
    with out_catalog.open("w", encoding="utf-8") as f:
        f.write("case_id\tlineno\tcategory\ttext\n")
        for r in catalog_rows:
            f.write("\t".join(str(c) for c in r) + "\n")

    # --- 出力 2: skeleton tsv ---
    out_skel = INV_DIR / "element_skeleton.tsv"
    with out_skel.open("w", encoding="utf-8") as f:
        f.write("case_id\tn_lines\tskeleton\n")
        for r in skeleton_rows:
            f.write("\t".join(str(c) for c in r) + "\n")

    # --- 出力 3: summary md ---
    summary: list[str] = []
    summary.append("# コーパス要素 enum レポート\n")
    summary.append(f"対象: {len(files)} 件\n")
    summary.append("## 1. 行カテゴリ集計\n")
    summary.append("| カテゴリ | 件数 |\n|---|---|")
    for k, v in cat_counter.most_common():
        summary.append(f"| {k} | {v} |")
    summary.append("")

    summary.append("## 2. INTRO 内サブ要素\n")
    summary.append("| 要素 | 件数 | 例 |\n|---|---|---|")
    for k, v in intro_subs_counter.most_common():
        ex = intro_subs_examples[k][0] if intro_subs_examples[k] else ""
        summary.append(f"| {k} | {v} | `{ex[:90]}` |")
    summary.append("")

    summary.append("## 3. CHRONO 行に含まれる書類・接続語\n")
    summary.append("| 書類/接続 | 件数 |\n|---|---|")
    for k, v in chrono_doc_counter.most_common():
        summary.append(f"| {k} | {v} |")
    summary.append("")

    summary.append("## 4. 案件骨格パターン（unique skeletons）\n")
    summary.append(f"unique 骨格パターン数: {len(skeleton_signatures)}（48件中）\n")
    summary.append("### 出現上位 10 骨格\n")
    for sig, cnt in skeleton_signatures.most_common(10):
        owners = skeleton_owners[sig][:3]
        summary.append(f"- **{cnt}件**: `{sig[:200]}`  例: {','.join(owners)}{'...' if len(skeleton_owners[sig])>3 else ''}")
    summary.append("")

    summary.append("## 5. OTHER カテゴリ全件（手動確認用）\n")
    summary.append(f"件数: {len(other_lines)}\n")
    for case_id, ln, txt in other_lines[:60]:
        summary.append(f"- {case_id} L{ln}: {txt[:120]}")
    if len(other_lines) > 60:
        summary.append(f"  ... and {len(other_lines)-60} more")

    out_summary = INV_DIR / "element_summary.md"
    out_summary.write_text("\n".join(summary), encoding="utf-8")

    print(f"\n=== summary ===")
    print(f"line categories:")
    for k, v in cat_counter.most_common():
        print(f"  {k:18s} {v}")
    print(f"unique skeletons: {len(skeleton_signatures)}")
    print(f"OTHER lines: {len(other_lines)}")
    print(f"\nout: {out_catalog}")
    print(f"out: {out_skel}")
    print(f"out: {out_summary}")


if __name__ == "__main__":
    main()
