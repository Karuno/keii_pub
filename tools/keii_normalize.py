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
# B-7: 同日結合語「Ａ及びＢ」 ↔ 「Ａ、Ｂ」を同視 (corpus 基準で「、」に統一)
# ----------------------------------------------------------------------------

# 公報側「意見書及び手続補正書の提出」「Ａ、Ｂ及びＣの提出」 → 「Ａ、Ｂ」「Ａ、Ｂ、Ｃ」
# corpus 基準 (純正起案 docx) では「、」が多数派のため公報側を統一する.
_DOC_KW = r"意見書|手続補正書|審判請求書|上申書|誤訳訂正書|応対記録|面接|翻訳文の提出|国内書面の提出"
_OYOBI_RE = re.compile(rf"({_DOC_KW})及び({_DOC_KW})")

def _absorb_b7_oyobi(text: str) -> str:
    # 「Ａ及びＢ」を「Ａ、Ｂ」に。連結書類名がさらに連なる場合も反復で吸収
    prev = None
    while prev != text:
        prev = text
        text = _OYOBI_RE.sub(r"\1、\2", text)
    return text


# ----------------------------------------------------------------------------
# B-8: 「の特許出願であって」 ↔ 「の出願であって」を同視 (corpus 基準で「の出願」)
# ----------------------------------------------------------------------------

_B8_RE = re.compile(r"の特許出願であって")

def _absorb_b8_no_shutsugan(text: str) -> str:
    return _B8_RE.sub("の出願であって", text)


# ----------------------------------------------------------------------------
# 拒絶理由通知書の「書」抜けを修正 (公報側の表記揺れを統一)
# ----------------------------------------------------------------------------

_KYOZETSU_NO_SHO_RE = re.compile(r"拒絶理由通知(?!書)")

def _absorb_kyozetsu_riyu_sho(text: str) -> str:
    return _KYOZETSU_NO_SHO_RE.sub("拒絶理由通知書", text)


# ----------------------------------------------------------------------------
# 手続補正書（方式）行の両側削除 (Lievito は規範通り除外、公報側で書く案件あり)
# ----------------------------------------------------------------------------

def _drop_houshiki_hosei(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "手続補正書（方式）" in line:
            continue
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 同日結合行内の書類順序を統一 (公報・Lievito で順序揺れがあるため両側ソート)
# ----------------------------------------------------------------------------

_COMBINED_LINE_RE = re.compile(r"^(.*?[：:])([^：:\n]+の提出)$", re.MULTILINE)
_DOC_NAME_SPLIT_RE = re.compile(r"[、]+|及び")

def _normalize_combined_doc_order(text: str) -> str:
    """同日結合行『日付：A、B、C の提出』の書類名部分をソートして順序差を吸収."""
    def repl(m):
        prefix = m.group(1)
        suffix = m.group(2)
        body = suffix[:-3]  # 「の提出」削除
        parts = [p.strip() for p in _DOC_NAME_SPLIT_RE.split(body) if p.strip()]
        if len(parts) < 2:
            return m.group(0)  # 単独書類はそのまま
        parts.sort()
        return prefix + "、".join(parts) + "の提出"
    return _COMBINED_LINE_RE.sub(repl, text)


# 50条の2の通知付記の形式差吸収 (公報側「特許法５０条の２の通知を伴う拒絶理由通知書」=書類名埋込形式
# → corpus 基準の「拒絶理由通知書」+別行「（特許法５０条の２の通知を伴う。）」に統一)
_FIFTY_NO_2_EMBEDDED_RE = re.compile(r"：特許法５０条の２の通知を伴う拒絶理由通知書")

def _normalize_fifty_no_2_format(text: str) -> str:
    return _FIFTY_NO_2_EMBEDDED_RE.sub("：拒絶理由通知書\n（特許法５０条の２の通知を伴う。）", text)


# ----------------------------------------------------------------------------
# 見出しの「第」プレフィックス無視 (z_kakka 「第１ 手続の経緯」/ z_no_kakka 「１ 手続の経緯」を同視)
# ----------------------------------------------------------------------------

_HEAD_DAI_RE = re.compile(r"^第([０-９0-9])[\s　]*手続の経緯", re.MULTILINE)

def _absorb_head_dai(text: str) -> str:
    return _HEAD_DAI_RE.sub(r"\1 手続の経緯", text)


# 公報の却下無しZ パターンで「１ 本願は、…」「２ 請求人の主張、…」のサブ見出し番号を除去
_HEAD_SUB_NUM_RE = re.compile(r"^[０-９0-9][\s　]+(?=本願は、)", re.MULTILINE)

def _absorb_head_sub_num(text: str) -> str:
    return _HEAD_SUB_NUM_RE.sub("", text)


# 日付内のパディング空白「令和X年 ３月 ２４日」「令和X年　３月　２４日」→「令和X年３月２４日」
_DATE_PADDING_RE = re.compile(r"(年|月)[\s　]+([０-９0-9])")

def _absorb_date_padding(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _DATE_PADDING_RE.sub(r"\1\2", text)
    return text


# ----------------------------------------------------------------------------
# 前置報告書行の両側削除 (corpus 基準では出力規範だが公報側で省略が多いため評価対象外)
# ----------------------------------------------------------------------------

def _drop_zenchi_report(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "前置報告書" in line:
            continue
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 日付リダクション (同月/同日) を両側でフル展開してから比較
# ----------------------------------------------------------------------------

# Lievito は直前行と「年同じ→同年、年月同じ→同月、年月日同じ→同日」とリダクションする
# 公報側はフル表記が多い。両側でフル展開してから比較すると差分が消える.
_ZEN_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")
_HAN_DIGIT = str.maketrans("0123456789", "０１２３４５６７８９")
_DATE_FULL_RE = re.compile(
    r"(令和|平成|昭和|令)([０-９0-9元]+)年[\s　]*([０-９0-9]+)月[\s　]*([０-９0-9]+)日"
)
_DATE_REDUCED_RE = re.compile(
    r"同年[\s　]*([０-９0-9]+)月[\s　]*([０-９0-9]+)日|"
    r"同月[\s　]*([０-９0-9]+)日|"
    r"同日"
)


def _zen_int(s: str) -> int:
    return int(s.translate(_ZEN_DIGIT).replace("元", "1"))


def _int_to_zen(n: int) -> str:
    return str(n).translate(_HAN_DIGIT)


def _expand_dates_full(text: str) -> str:
    """直前行のフル日付を引き継いで、同月/同年/同日をフル日付に展開."""
    lines = text.splitlines()
    out: list[str] = []
    last_era = last_year = last_month = last_day = None
    for line in lines:
        # 行内の最初のフル日付を捉える
        m_full = _DATE_FULL_RE.search(line)
        if m_full:
            last_era = m_full.group(1)
            last_year = _zen_int(m_full.group(2))
            last_month = _zen_int(m_full.group(3))
            last_day = _zen_int(m_full.group(4))
            out.append(line)
            continue
        # リダクション形式があれば展開
        if last_era is None:
            out.append(line)
            continue

        def replace_reduced(m):
            nonlocal last_month, last_day
            if m.group(1) is not None:  # 同年M月D日
                month = _zen_int(m.group(1))
                day = _zen_int(m.group(2))
                last_month, last_day = month, day
                return f"{last_era}{_int_to_zen(last_year)}年{_int_to_zen(month)}月{_int_to_zen(day)}日"
            if m.group(3) is not None:  # 同月D日
                day = _zen_int(m.group(3))
                last_day = day
                return f"{last_era}{_int_to_zen(last_year)}年{_int_to_zen(last_month)}月{_int_to_zen(day)}日"
            # 同日
            return f"{last_era}{_int_to_zen(last_year)}年{_int_to_zen(last_month)}月{_int_to_zen(last_day)}日"
        line = _DATE_REDUCED_RE.sub(replace_reduced, line)
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# 元号と数字間の空白除去 (「令和 ５年」「令和　５年」→「令和５年」)
# ----------------------------------------------------------------------------

_ERA_PADDING_RE = re.compile(r"(令和|平成|昭和|令)[\s　]+([０-９0-9元])")

def _absorb_era_padding(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _ERA_PADDING_RE.sub(r"\1\2", text)
    return text


# ----------------------------------------------------------------------------
# 「に出願された特願」⇔「に出願した特願」 同視 (corpus 基準で「した」)
# ----------------------------------------------------------------------------

_SHUSSUGAN_SARETA_RE = re.compile(r"に出願された(特願)")

def _absorb_sareta_shita(text: str) -> str:
    return _SHUSSUGAN_SARETA_RE.sub(r"に出願した\1", text)


# ----------------------------------------------------------------------------
# 「以下のとおりである」⇔「次のとおりである」 同視 (corpus 基準で「次」)
# ----------------------------------------------------------------------------

_IGAKA_TSUGI_RE = re.compile(r"以下のとおりである")

def _absorb_iga_tsugi(text: str) -> str:
    return _IGAKA_TSUGI_RE.sub("次のとおりである", text)


# ----------------------------------------------------------------------------
# 願番号の括弧の有無を同視 (「（特願…号）」⇔「特願…号」)
# ----------------------------------------------------------------------------

_TOKUGAN_PAREN_RE = re.compile(r"（(特願[０-９0-9]{4}－[０-９0-9]{6}号)）")

def _absorb_tokugan_paren(text: str) -> str:
    return _TOKUGAN_PAREN_RE.sub(r"\1", text)


# ----------------------------------------------------------------------------
# 「手続の経緯の概要」⇔「手続の経緯」 同視
# ----------------------------------------------------------------------------

_KEII_GAIYO_RE = re.compile(r"手続の経緯の概要")

def _absorb_keii_gaiyo(text: str) -> str:
    return _KEII_GAIYO_RE.sub("手続の経緯", text)


# ----------------------------------------------------------------------------
# 「面接」⇔「応対記録」 同視 (corpus 基準で「応対記録」)
# ----------------------------------------------------------------------------

_MENSETSU_RE = re.compile(r"：面接$", re.MULTILINE)

def _absorb_mensetsu(text: str) -> str:
    return _MENSETSU_RE.sub("：応対記録", text)


# ----------------------------------------------------------------------------
# 元号省略の送達日「（１１月７日：原査定の謄本の送達）」を直前行の元号年で補完
# ----------------------------------------------------------------------------

# 公報側で稀に「（M月D日：原査定の謄本の送達）」のように年が省略される.
# _expand_dates_full の前段で「同年M月D日」に補完しておくと展開ロジックに乗る.
_TOSOTATSU_NO_YEAR_RE = re.compile(
    r"（[\s　]*([０-９0-9]+)月[\s　]*([０-９0-9]+)日[\s　]*[：:][\s　]*原査定の謄本の送達"
)

def _absorb_toutatsu_no_year(text: str) -> str:
    return _TOSOTATSU_NO_YEAR_RE.sub(r"（同年\1月\2日：原査定の謄本の送達", text)


# ----------------------------------------------------------------------------
# 「（最後）」「（最初）」「（前置審査、最後）」付記の両側削除
# ----------------------------------------------------------------------------

# Lievito は規範通り付与、公報側は流儀差で省略するケースを吸収.
# 拒絶理由通知書の修飾子（括弧内）を両側で削除して比較する.
_SAIGO_RE = re.compile(r"（[^）]*(?:最後|最初|前置審査)[^）]*）")

def _drop_saigo_marker(text: str) -> str:
    return _SAIGO_RE.sub("", text)


# ----------------------------------------------------------------------------
# 非対称正規化: 公報側基準で行ごとに削除を決める
# ----------------------------------------------------------------------------

def _drop_lines_containing(text: str, marker: str) -> str:
    out = []
    for line in text.splitlines():
        if marker in line:
            continue
        out.append(line)
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def normalize_for_compare(text: str) -> str:
    """公報経緯と Lievito 生成経緯の比較用正規化.

    順序が重要:
      1. trim   : 経緯ブロック外の散文・エクスキューズ集 除去
      2. B-4    : 西暦(和暦) → 和暦
      3. B-3    : 44条1項の規定に基づいて 削除
      4. B-2    : とした特許出願であって → としたものであって
      5. B-8    : の特許出願 → の出願 (corpus 基準)
      6. C-4    : 願番号 空白桁埋め → ゼロ埋め
      7. B-5    : 優先日定義 削除
      8. C-5    : 同日順序ソート
      9. 前置報告書行削除
     10. 「拒絶理由通知書」書抜け修正
     11. B-7    : 「Ａ及びＢ」→「Ａ、Ｂ」 (corpus 基準)
     12. 見出し「第」プレフィックス削除
     13. 日付リダクション (同月/同年/同日) を両側でフル展開
     14. A群    : 空白・改行 (最後に集約)
    """
    text = _trim_keii_block(text)
    text = _absorb_b4_seireki(text)
    text = _absorb_b3_law44(text)
    text = _absorb_b2_toshita(text)
    text = _absorb_b8_no_shutsugan(text)
    text = _absorb_c4_appno(text)
    text = _absorb_b5_prio_def(text)
    text = _absorb_c5_same_day_order(text)
    text = _drop_zenchi_report(text)
    text = _drop_houshiki_hosei(text)
    text = _absorb_kyozetsu_riyu_sho(text)
    text = _normalize_fifty_no_2_format(text)
    text = _absorb_b7_oyobi(text)
    text = _normalize_combined_doc_order(text)
    text = _absorb_head_dai(text)
    text = _absorb_head_sub_num(text)
    text = _absorb_keii_gaiyo(text)
    text = _absorb_mensetsu(text)
    text = _absorb_toutatsu_no_year(text)
    text = _expand_dates_full(text)
    text = _absorb_date_padding(text)
    text = _absorb_era_padding(text)
    text = _absorb_sareta_shita(text)
    text = _absorb_iga_tsugi(text)
    text = _absorb_tokugan_paren(text)
    text = _drop_saigo_marker(text)
    text = _absorb_a_whitespace(text)
    return text


def normalize_pair(gen: str, act: str) -> tuple[str, str]:
    """非対称正規化: 公報側 (act) の有無で行削除を判定する.

    重要書類は「公報側にあれば残して比較、なければ両側削除」とする.
    - 送達日: 査定との日付整合性チェックとして重要だが、公報側で省略される案件も多い
    - 当審拒絶理由通知書: 同上
    - 上申書: 同上 (Lievito は全件出力、公報側で省略される案件多数)
    - 特許法50条の2付記: 同上 (Lievito は出力、公報側で省略される案件あり)
    """
    gen = normalize_for_compare(gen)
    act = normalize_for_compare(act)

    if "原査定の謄本の送達" not in act:
        gen = _drop_lines_containing(gen, "原査定の謄本の送達")
    if "当審拒絶理由通知書" not in act:
        gen = _drop_lines_containing(gen, "当審拒絶理由通知書")
    if "上申書" not in act:
        gen = _drop_lines_containing(gen, "上申書")
    if "特許法５０条の２" not in act:
        gen = _drop_lines_containing(gen, "特許法５０条の２")

    return gen, act


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
