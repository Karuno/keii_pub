"""generator/chronology.py — CHRONO 行生成

keii_model.yaml の chronology_rules を実装:
  - 並び順（日付昇順、A02 直下に Type C を挿入）
  - 同一日 Type B の結合（「、」で連結）
  - 同年/同月/同日 リダクション
  - 13 全角文字 + ：の桁揃え
  - 拒絶査定の「（以下「原査定」という。）」自動付与
  - 上申書フィルタ（前置報告書後のみ）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .dates import DocumentEntry
from .jp_dates import _to_zen, to_wareki


# ============================================================================
# データ構造
# ============================================================================

@dataclass
class ChronoLine:
    """1 chrono 行分のレンダリング結果。"""
    date_iso: str       # YYYY-MM-DD
    doc_type: str       # 'A' | 'B' | 'C'
    rendered: str       # 出力テキスト1行
    sources: list[DocumentEntry]


# ============================================================================
# 並び順 + Type C 挿入
# ============================================================================

def _sort_and_insert_souutatsu(entries: list[DocumentEntry]) -> list[DocumentEntry]:
    """日付昇順にソートし、A02 直下に Type C 送達行を確保する。

    Type C の DocumentEntry は dates.py で既に作られている前提（A02 と同日付の場合あり）。
    順序: 同日内では A→B→C の安定ソート（既に dates.py で済）。
    """
    # コピーして並び替え
    out = list(entries)
    out.sort(key=lambda e: (e.date_iso, {"A": 0, "B": 1, "C": 2}[e.doc_type]))

    # 補正の却下決定 (A502) と 拒絶査定 (A02) が同日なら、却下→査定→送達 の順
    # （同日内 A→A→C ソートだと、書類名 alphanum で順序が決まらないので明示）
    fixed: list[DocumentEntry] = []
    i = 0
    while i < len(out):
        e = out[i]
        # 同日 Type A グループ
        if e.doc_type == "A" and i + 1 < len(out) and out[i + 1].date_iso == e.date_iso and out[i + 1].doc_type == "A":
            # A 同士で「補正の却下」が「拒絶査定」より先になるよう並び替え
            same_day_a = []
            while i < len(out) and out[i].date_iso == e.date_iso and out[i].doc_type == "A":
                same_day_a.append(out[i])
                i += 1
            # 補正の却下 → 拒絶査定 → その他 A の順
            order_key = {"補正の却下の決定": 0, "拒絶査定": 1, "前置報告書": 2,
                         "拒絶理由通知書": 3, "特許査定": 4}
            same_day_a.sort(key=lambda x: order_key.get(x.name, 5))
            fixed.extend(same_day_a)
        else:
            fixed.append(e)
            i += 1
    return fixed


# ============================================================================
# 上申書フィルタ
# ============================================================================

def _filter_joushinsho(entries: list[DocumentEntry]) -> list[DocumentEntry]:
    """前置報告書より後にある上申書のみ残す（開発者ルール コメント[7]）。"""
    zenchi_idx = None
    for i, e in enumerate(entries):
        if e.name == "前置報告書":
            zenchi_idx = i
            break
    if zenchi_idx is None:
        # 前置報告書がない場合、上申書はすべて省略
        return [e for e in entries if e.name != "上申書"]
    # 前置報告書より前の上申書は除外
    out: list[DocumentEntry] = []
    for i, e in enumerate(entries):
        if e.name == "上申書" and i < zenchi_idx:
            continue
        out.append(e)
    return out


# ============================================================================
# 手続補正書（方式）の除外
# ============================================================================

def _filter_houshiki_hosei(entries: list[DocumentEntry]) -> list[DocumentEntry]:
    """手続補正書（方式）を除外（dates.py で除外済の場合は no-op）。"""
    return [e for e in entries if not (e.name == "手続補正書" and "方式" in (e.raw or {}).get("documentDescription", ""))]


# ============================================================================
# 同一日 Type B 結合
# ============================================================================

def _combine_same_day_b(entries: list[DocumentEntry]) -> list[tuple[str, str, list[DocumentEntry]]]:
    """並び順固定後、同日 Type B を結合し (date_iso, doc_type, group) のリストを返す。

    Type A と Type C は単独 group。
    結合内の書類順序はコーパスの典型ペアに従う:
      意見書 → 手続補正書（最頻 64件）
      審判請求書 → 手続補正書（38件）
      上申書 → 手続補正書（6件）
    """
    # 結合内順序のキー（小さい順に並ぶ）
    INNER_ORDER = {
        "誤訳訂正書": 0,
        "審判請求書": 1,
        "意見書": 2,
        "上申書": 3,
        "手続補正書": 4,
        "翻訳文の提出": 5,
        "国内書面の提出": 6,
        "応対記録": 7,
        "面接": 7,
    }
    out: list[tuple[str, str, list[DocumentEntry]]] = []
    i = 0
    while i < len(entries):
        e = entries[i]
        if e.doc_type == "B":
            group = [e]
            j = i + 1
            while j < len(entries) and entries[j].date_iso == e.date_iso and entries[j].doc_type == "B":
                group.append(entries[j])
                j += 1
            group.sort(key=lambda x: INNER_ORDER.get(x.name, 99))
            out.append((e.date_iso, "B", group))
            i = j
        else:
            out.append((e.date_iso, e.doc_type, [e]))
            i += 1
    return out


# ============================================================================
# 日付の和暦化 + リダクション
# ============================================================================

@dataclass
class _PrevDate:
    year: int
    month: int
    day: int


def _iso_to_ymd(iso: str) -> tuple[int, int, int]:
    return int(iso[:4]), int(iso[5:7]), int(iso[8:10])


def _format_date_with_reduction(iso: str, prev: _PrevDate | None) -> tuple[str, _PrevDate]:
    """前行と比較して『令和N年　M月　D日』『同年　M月　D日』『同月　D日』『同日』を返す。

    月日は2桁幅にパディング（1桁の前に全角空白）。

    返り値: (date_text, updated_prev)
    """
    y, m, d = _iso_to_ymd(iso)
    cur = _PrevDate(y, m, d)
    mm = _pad_md(m)
    dd = _pad_md(d)
    if prev is not None and prev.year == y and prev.month == m and prev.day == d:
        return "同日", cur
    if prev is not None and prev.year == y and prev.month == m:
        return f"同月{dd}日", cur
    if prev is not None and prev.year == y:
        return f"同年{mm}月{dd}日", cur
    # フル元号表記（パディング込み）
    from .jp_dates import ERAS
    yyyymmdd_int = y * 10000 + m * 100 + d
    name, base = "令和", 2019
    for boundary, n_, b in ERAS:
        if yyyymmdd_int >= boundary:
            name, base = n_, b
    nen = y - base + 1
    nen_str = "元" if nen == 1 else _to_zen(str(nen))
    return f"{name}{nen_str}年{mm}月{dd}日", cur


def _pad_md(n: int) -> str:
    """月日を2桁幅にパディング（1桁の前に全角空白1個、2桁はそのまま全角化）。"""
    if n < 10:
        return "　" + _to_zen(str(n))
    return _to_zen(str(n))


# ============================================================================
# 行 1 件のレンダリング（13 全角文字 + ：）
# ============================================================================

def _render_type_a(date_text: str, doc_name: str) -> str:
    """Type A: 「{date}付け：{doc_name}」 を 13 全角文字目に「：」が来るよう整形。

    桁揃え:
      位置 1-5: 年部分（「令和N年」または「同年」 ）
      位置 6-8: 月部分（「MM月」または「　M月」）
      位置 9-11: 日部分（「DD日」または「　D日」）
      位置 12-13: 「付け」
      位置 14: ：
    """
    line = _pad_date_to_position11(date_text) + "付け：" + doc_name
    return line


def _render_type_b(date_text: str, doc_names: list[str]) -> str:
    """Type B: 「{date}　　：{結合書類名}の提出」"""
    line = _pad_date_to_position11(date_text) + "　　：" + _format_b_doc_names(doc_names)
    return line


def _render_type_c(date_text: str) -> str:
    """Type C: 先頭5全角空白 +「（{date}　　：原査定の謄本の送達）」

    date_text は呼び出し側 (render_chronology) で _format_date_with_reduction を
    通したもの。前行（通常は同日付の A02 拒絶査定行）との比較により
    「令和N年MM月DD日」「同年MM月DD日」「同月DD日」「同日」のいずれかになる。
    本関数では date_text をそのまま括弧書きに埋め込むだけで、特定の短縮形を仮定しない。

    全体長は外側括弧除いて13全角文字相当（5スペース + （ + リダクション後日付 + 　　 + ：内容）。
    """
    return f"　　　　　（{date_text}　　：原査定の謄本の送達）"


def _format_b_doc_names(names: list[str]) -> str:
    """Type B 書類名を公用文ルールに従って結合し、末尾に「の提出」を付与。

    公用文作成の考え方（knowledge/standards/公用文作成の考え方.txt L1041-1051）:
      - 2要素: 「Ａ及びＢ」
      - 3要素以上: 「Ａ、Ｂ、Ｃ及びＤ」（最後だけ「及び」、他は「、」）
      - 階層あり (本ロジック対象外): 「Ａ及びＢ並びにＣ及びＤ」

    応対記録／面接 は単体使用が多く「の提出」を付けない。
    """
    if not names:
        return ""
    no_teishutu_names = {"応対記録", "面接"}
    if len(names) == 1 and names[0] in no_teishutu_names:
        return names[0]
    if len(names) == 1:
        return f"{names[0]}の提出"
    # 公用文準拠の結合
    if len(names) == 2:
        joined = f"{names[0]}及び{names[1]}"
    else:
        # 3要素以上: 最後だけ「及び」、他は「、」
        joined = "、".join(names[:-1]) + "及び" + names[-1]
    return joined + "の提出"


def _pad_date_to_position11(date_text: str) -> str:
    """日付文字列を「位置11 (=日) で終わる」よう先頭にパディング。

    各パターンは月日が _pad_md でパディング済（2桁幅）の前提。
      「令和N年MM月DD日」  → 11文字、そのまま
      「同年MM月DD日」     → 8文字、先頭3全角空白
      「同月DD日」         → 5文字、先頭6全角空白
      「同日」             → 2文字、先頭9全角空白

    元年（「令和元年」）の場合は内部処理で1文字短くなるため別途調整は呼び出し側。
    """
    if date_text.startswith("同年"):
        return "　　　" + date_text
    if date_text.startswith("同月"):
        return "　　　　　　" + date_text
    if date_text == "同日":
        return "　　　　　　　　　" + date_text
    return date_text  # フル元号はそのまま


# ============================================================================
# 修飾子（最後／最初）
# ============================================================================

def _apply_kyozetsu_modifiers(entries: list[DocumentEntry]) -> list[DocumentEntry]:
    """拒絶理由通知書本文に「最後の」記載があれば「（最後）」を付与する。

    H4 仮説検証 (95_verify_saigo_rule.py): 20/20 で
    「本文に『最後の』」 == 「corpus（最後）付与」 が一致。
    起案者の癖ではなく客観ルール。
    （最初）は通常省略する慣例（コーパス 3件のみ）のため付与しない。
    """
    for i, e in enumerate(entries):
        if e.name != "拒絶理由通知書":
            continue
        body_has_saigo = (e.raw or {}).get("_body_has_saigo", False)
        if body_has_saigo:
            entries[i] = DocumentEntry(
                name="拒絶理由通知書（最後）",
                code=e.code,
                date_iso=e.date_iso,
                doc_type=e.doc_type,
                source=e.source,
                note=e.note,
                raw=e.raw,
            )
    return entries


# ============================================================================
# メイン関数
# ============================================================================

def render_chronology(entries: list[DocumentEntry]) -> list[ChronoLine]:
    """全 entry を chrono 行のリストにレンダリング。"""
    # 1. 並び順
    sorted_entries = _sort_and_insert_souutatsu(entries)
    # 2. 上申書フィルタ
    sorted_entries = _filter_joushinsho(sorted_entries)
    # 3. 方式補正除外（dates.py で対応済の場合は no-op）
    sorted_entries = _filter_houshiki_hosei(sorted_entries)
    # 4. 拒絶理由通知書（最後）付与
    sorted_entries = _apply_kyozetsu_modifiers(sorted_entries)
    # 5. 同日 Type B 結合
    groups = _combine_same_day_b(sorted_entries)

    # 6. 各 group をレンダリング（同年/同月/同日 リダクションを通しで管理）
    out: list[ChronoLine] = []
    prev: _PrevDate | None = None
    for date_iso, doc_type, group in groups:
        date_text, prev = _format_date_with_reduction(date_iso, prev)

        if doc_type == "A":
            e = group[0]
            doc_name = e.name
            # 拒絶査定の別称付与
            if e.name == "拒絶査定":
                doc_name = "拒絶査定（以下「原査定」という。）"
            line = _render_type_a(date_text, doc_name)
            out.append(ChronoLine(date_iso, "A", line, group))
        elif doc_type == "B":
            names = [e.name for e in group]
            line = _render_type_b(date_text, names)
            out.append(ChronoLine(date_iso, "B", line, group))
        elif doc_type == "C":
            line = _render_type_c(date_text)
            out.append(ChronoLine(date_iso, "C", line, group))

    return out


# ============================================================================
# CLI 動作確認
# ============================================================================

def _main() -> None:
    import argparse
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.keii_python.generator.dates import get_doc_dates

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("appno")
    args = ap.parse_args()

    entries = get_doc_dates(args.appno)
    lines = render_chronology(entries)
    print(f"appno: {args.appno}")
    print(f"chrono lines: {len(lines)}")
    for line in lines:
        print(line.rendered)


if __name__ == "__main__":
    _main()
