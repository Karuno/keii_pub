"""keii_normalize.py — 経緯テキストの比較用正規化

公報経緯と Lievito 生成経緯を同じ正規化に通してから完全一致 (Y/N) で比較する。
SequenceMatcher の ratio ではなく「正答数 / 総件数」を進化ループ評価指標とするための前処理。

吸収対象 (keii_normalization_candidates.md.txt v1 承認版):
  A群: 空白・改行系 (推定安全に吸収)
  B群: 実質同等の表記揺れ (B-2/3/4/5 を吸収)
  C-4: 願番号 空白桁埋め ↔ ゼロ埋め
  C-5: 同日複数書類の並び順差

吸収対象外 (Lievito 実バグとして個別改修):
  C-1: 書類取りこぼし (A631 等の翻訳文)
  C-2: 50条の2付記漏れ
  C-3: 複数優先権の集約漏れ
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable


# ----------------------------------------------------------------------------
# Trim: 経緯ブロックの末尾以降を切り捨てる
# ----------------------------------------------------------------------------

# 却下無しZパターンの公報は「第１ 手続の経緯」内に小見出し「2 請求人の主張」等が続き、
# 抽出スクリプトが「第２」直前まで取得するため経緯外のテキストが混入する。
# 行頭の「２ 」「３ 」等を検出して切り捨てる (「２　」(全角空白) も対象)。
_SUBSEC_HEAD_RE = re.compile(r"^[\s　]*[２-９][\s　]")

# Lievito の末尾「エクスキューズ集」を切り捨てる
_EXCUSE_MARK = "<!-- 以下、エクスキューズ集"


def _trim_keii_block(text: str) -> str:
    out: list[str] = []
    seen_head = False
    for line in text.splitlines():
        if _EXCUSE_MARK in line:
            break
        if "手続の経緯" in line and not seen_head:
            seen_head = True
            out.append(line)
            continue
        if seen_head and _SUBSEC_HEAD_RE.match(line):
            # 経緯セクション内の次の小見出し (２ 請求人の主張 等) で打ち切り
            # ただし「２ 月」「２ 日」のような日付の一部を誤検出しないよう、
            # その後に「請求人」「審判」等の典型語が無いと信頼度低い。
            # 単純化: 「数字＋空白＋日本語語句」が経緯行 (日付：書類名) か小見出しかは
            # コロン「：」の有無で判定する。
            rest = line[1:].lstrip("　 ")
            if "：" not in rest and "月" not in line[:8]:
                break
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# B-4: 西暦（和暦）→ 和暦単独
# ----------------------------------------------------------------------------

# Lievito: 「２０１７年（平成２９年）」「２０１９年（平成３１年）」
# 公報   : 「平成２９年」のみ
# 正規化 : 西暦+括弧+和暦 → 和暦 のみ
_SEIREKI_WAREKI_RE = re.compile(
    r"[０-９0-9]{4}年（(?P<wareki>(?:平成|令和|昭和|令)[０-９0-9元]+年)）"
)

def _absorb_b4_seireki(text: str) -> str:
    return _SEIREKI_WAREKI_RE.sub(lambda m: m.group("wareki"), text)


# ----------------------------------------------------------------------------
# B-3: 「特許法44条1項の規定に基づいて」の挿入有無を同視
# ----------------------------------------------------------------------------

_B3_PATTERNS = [
    re.compile(r"(?:特許法)?第?[４4]{1,2}条第?[１1]項の規定に基づ(?:いて|き)"),
    re.compile(r"(?:特許法)?第?[４4]{1,2}条第?[１1]項の規定による"),
]

def _absorb_b3_law44(text: str) -> str:
    for p in _B3_PATTERNS:
        text = p.sub("", text)
    return text


# ----------------------------------------------------------------------------
# B-2: 「とした特許出願」 ↔ 「としたもの」 同視
# ----------------------------------------------------------------------------

# Lievito: 「新たな特許出願とした特願…号の一部を…新たな特許出願とした」
# 公報   : 「新たな特許出願とした」or「としたもの」
# 正規化 : 「特許出願とした」 → 「とした」, 「としたもの」 → 「とした」, 「とした特許出願」 → 「とした」
# 簡略化: 「とした(特許出願|もの)」を「とした」に統一
_B2_PATTERNS = [
    (re.compile(r"とした特許出願であって"), "としたものであって"),
    (re.compile(r"としたものであって"), "としたものであって"),  # no-op anchor
]

def _absorb_b2_toshita(text: str) -> str:
    for pat, rep in _B2_PATTERNS:
        text = pat.sub(rep, text)
    return text


# ----------------------------------------------------------------------------
# C-4: 願番号の空白桁埋め ↔ ゼロ埋め
# ----------------------------------------------------------------------------

# 公報   : 「特願２０２０－    ６５９９号」 (空白桁埋め)
# Lievito: 「特願２０２０－００６５９９号」 (ゼロ埋め)
# 正規化 : 「特願YYYY－(空白混在の数字列)号」 → 数字のみ抽出してゼロ埋め6桁
_APPNO_RE = re.compile(
    r"特願(?P<y>[０-９0-9]{4})[－‐−\-][\s　０-９0-9]{1,15}号"
)

_HAN_TO_ZEN = str.maketrans("0123456789", "０１２３４５６７８９")
_ZEN_TO_HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def _absorb_c4_appno(text: str) -> str:
    def repl(m):
        y = m.group("y").translate(_HAN_TO_ZEN)  # 年部分も全角統一
        # ハイフン (4種類) 以降の数字列を抽出
        parts = re.split(r"[－‐−\-]", m.group(0), maxsplit=1)
        if len(parts) < 2:
            return m.group(0)
        # 全角に統一 → 半角化してパディング → 全角に戻す
        digits = re.sub(r"[^０-９0-9]", "", parts[1])
        digits_hankaku = digits.translate(_ZEN_TO_HAN)
        digits_padded = digits_hankaku.zfill(6)
        digits_zen = digits_padded.translate(_HAN_TO_ZEN)
        return f"特願{y}－{digits_zen}号"
    return _APPNO_RE.sub(repl, text)


# ----------------------------------------------------------------------------
# B-5: 優先日定義文を末尾標準形式に統一
# ----------------------------------------------------------------------------

# 公報パターン1: 「以下、...を「優先日」という。」 (末尾配置)
# 公報パターン2: 「以下、...を「優先日」という。」 (経緯ブロック冒頭直後)
# Lievito A:    「なお、...を以下「優先日」という。」 (冒頭文末尾)
# Lievito C:    「以下、...を「優先日」という。」 (CHRONO 末尾独立段落)
# 正規化       : 抽出して「優先日:<日付>」を末尾に再配置
_PRIO_DEF_RE_LIST = [
    re.compile(
        r"(?:以下、?\s*)?(?P<date>(?:平成|令和|昭和|令)[０-９0-9元]+年[０-９0-9]+月[０-９0-9]+日)\s*を「優先日」という。?"
    ),
    re.compile(
        r"なお、?\s*(?P<date>(?:平成|令和|昭和|令)[０-９0-9元]+年[０-９0-9]+月[０-９0-9]+日)\s*を以下「優先日」という。?"
    ),
]

def _absorb_b5_prio_def(text: str) -> str:
    """優先日定義行を両側で削除して比較対象外とする.

    公報・Lievito ともに表記揺れ (位置・接続詞・西暦/和暦の使い分け) が大きく、
    Paris 優先日では公報=西暦のみ・Lievito=西暦+(和暦) の非対称があるため、
    単純規則での同視が不能。比較から除外し経緯行リストの一致確認に集中する。
    """
    out: list[str] = []
    for line in text.splitlines():
        if "「優先日」" in line and "という" in line:
            continue
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# C-5: 同日複数書類の並び順差
# ----------------------------------------------------------------------------

# 経緯行は「<日付>：<書類名>」または「（<日付>：<書類名>）」形式。
# 同日付の連続行を行内テキストでソートして比較に依存しない順序にする。
_DATE_LINE_RE = re.compile(
    r"^[\s　（(]*"
    r"(?:(?:令和|平成|昭和|令)[０-９0-9元]+年|同年|同月)[\s　]*"
    r"[０-９0-9]+月[\s　]*[０-９0-9]+日"
)

def _absorb_c5_same_day_order(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        # 同日付グループの先頭を見つける
        if not _DATE_LINE_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        # この行が「<日付>：<書類名>」形式である前提で、日付文字列を抽出
        # 簡易: 行頭から「：」までを日付キーとする (空白は無視)
        def _date_key(line: str) -> str:
            m = re.match(r"^[\s　（(]*([^：:]+)[：:]", line)
            if not m:
                return line
            return re.sub(r"[\s　（()]+", "", m.group(1))
        cur_key = _date_key(lines[i])
        group = [lines[i]]
        j = i + 1
        while j < len(lines) and _DATE_LINE_RE.match(lines[j]) and _date_key(lines[j]) == cur_key:
            group.append(lines[j])
            j += 1
        # 同日グループを内容文字列でソート (正規化後の比較なので安定でよい)
        group.sort()
        out.extend(group)
        i = j
    return "\n".join(out)


# ----------------------------------------------------------------------------
# A群: 空白・改行系
# ----------------------------------------------------------------------------

def _absorb_a_whitespace(text: str) -> str:
    """A-1〜A-6 を一括処理.

    - A-1: 全角空白1個 ↔ 半角空白2個 (連続空白圧縮で同視)
    - A-2: 行頭の全角・半角空白を除去
    - A-3: 行内連続空白を半角1個に圧縮 (括弧内含む)
    - A-4: 開き括弧直後の空白を除去 (= A-3 で吸収)
    - A-5: 空行除去
    - A-6: 行末空白除去
    """
    out: list[str] = []
    for line in text.splitlines():
        # 行頭空白除去 (全角・半角混在)
        line = re.sub(r"^[\s　]+", "", line)
        # 行末空白除去
        line = line.rstrip()
        # 行内連続空白を半角1個に圧縮 (全角空白も対象)
        line = re.sub(r"[\s　]+", " ", line)
        # 開き括弧直後の空白を除去
        line = re.sub(r"([（(「『【〈《\[])[\s　]+", r"\1", line)
        # 閉じ括弧直前の空白を除去
        line = re.sub(r"[\s　]+([）)」』】〉》\]])", r"\1", line)
        # コロン前後の空白を除去 (日付：書類名 を統一)
        line = re.sub(r"[\s　]*([：:])[\s　]*", r"\1", line)
        if line:
            out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def normalize_for_compare(text: str) -> str:
    """公報経緯と Lievito 生成経緯の比較用正規化.

    順序が重要:
      1. trim   : 経緯ブロック外の post-経緯 散文・エクスキューズ集 除去
      2. B-4    : 西暦(和暦) → 和暦 (B-5 の優先日抽出前)
      3. B-3    : 44条1項の規定に基づいて 削除
      4. B-2    : とした特許出願であって → としたものであって
      5. C-4    : 願番号 空白桁埋め → ゼロ埋め
      6. B-5    : 優先日定義 抽出して末尾に再配置
      7. C-5    : 同日順序ソート
      8. A群    : 空白・改行 (最後に集約)
    """
    text = _trim_keii_block(text)
    text = _absorb_b4_seireki(text)
    text = _absorb_b3_law44(text)
    text = _absorb_b2_toshita(text)
    text = _absorb_c4_appno(text)
    text = _absorb_b5_prio_def(text)
    text = _absorb_c5_same_day_order(text)
    text = _absorb_a_whitespace(text)
    return text


# ----------------------------------------------------------------------------
# CLI (デバッグ用)
# ----------------------------------------------------------------------------

def _main():
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", nargs="?", help="正規化対象ファイル (省略時は stdin)")
    ap.add_argument("--diff", help="--diff <other> で2ファイル正規化後 diff を表示")
    args = ap.parse_args()

    def _load(path_or_none):
        if path_or_none:
            return open(path_or_none, encoding="utf-8").read()
        return sys.stdin.read()

    if args.diff:
        a = normalize_for_compare(_load(args.file))
        b = normalize_for_compare(_load(args.diff))
        import difflib
        for line in difflib.unified_diff(
            a.splitlines(), b.splitlines(),
            fromfile=args.file or "stdin", tofile=args.diff,
            lineterm=""
        ):
            print(line)
        print(f"# match: {'Y' if a == b else 'N'}", file=sys.stderr)
    else:
        sys.stdout.write(normalize_for_compare(_load(args.file)))


if __name__ == "__main__":
    _main()
