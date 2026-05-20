"""generator/keii.py — 「手続の経緯」セクションの統合生成

HEAD + INTRO + CHRONO + 末尾を組み立てる。

CLI:
  python -m generator.keii 2018244177
  python -m generator.keii 2018244177 --kind z_no_kakka
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Literal

from .dates import get_doc_dates_with_source, DocumentEntry
from .chronology import render_chronology, ChronoLine
from .intro import generate_intro
from .jp_dates import to_seireki_wareki, to_wareki, country_name


KkokkaKind = Literal["z_kakka", "z_no_kakka"]
PrioDefStyle = Literal["A", "B", "C"]


@dataclass
class TraceEntry:
    line: str
    basis: str
    source: str


@dataclass
class Excuse:
    """生成時にルール選択で揺れがあった項目の説明。

    出力時、エクスキューズ集として末尾に薄文字でまとめる想定。
    """
    rule_id: str         # 例: "prio_def_style", "no_shutsugan_pattern", "zenchi_inclusion"
    chosen: str          # 選んだ値・スタイル
    reason: str          # 選んだ根拠（開発者ルール準拠等）
    note: str = ""       # 追加注記（例: コーパスのバリエーション）


@dataclass
class GenerateResult:
    text: str
    trace: list[TraceEntry]
    pattern: str
    apptype: str
    source_used: str           # 'primary' | 'fallback' | 'none'
    missing_fields: list[str]
    excuses: list[Excuse] = field(default_factory=list)
    text_with_excuses: str = ""  # text + 末尾エクスキューズ集（薄文字マーカー付き）


HEAD_BY_KIND = {
    "z_kakka":    "第１　手続の経緯",
    "z_no_kakka": "１　手続の経緯",
}


def _appno_dash(appno_10: str) -> str:
    """10桁出願番号 → 「２０●●－●●●●●●」全角ハイフン形式。"""
    if not appno_10 or len(appno_10) != 10:
        return appno_10
    from .jp_dates import _to_zen
    return f"{_to_zen(appno_10[:4])}－{_to_zen(appno_10[4:])}"


def _find_doc_history_data(appno: str) -> dict[str, Any] | None:
    """doc_history.json の data 部を返す（INTRO 生成用）。

    探索順:
      1. inventory/doc_history_collected/{appno}.json (一次ソース)
      2. settings.inputs_fallback_dir 配下の doc_history.json (オプション)
    """
    from .settings import get_inputs_fallback_dir
    HERE = Path(__file__).resolve().parent
    INV = HERE.parent / "inventory"

    # collected/ 優先
    p = INV / "doc_history_collected" / f"{appno}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8")).get("result", {}).get("data", {}) or {}
    # 補助探索ディレクトリ（settings.yaml で設定）
    fallback = get_inputs_fallback_dir()
    if fallback is not None and fallback.exists():
        for q in fallback.rglob("doc_history.json"):
            if any(part.startswith(".") for part in q.parts):
                continue
            try:
                j = json.loads(q.read_text(encoding="utf-8"))
                if j.get("result", {}).get("data", {}).get("applicationNumber") == appno:
                    return j["result"]["data"]
            except Exception:
                continue
    return None


def _build_intro_with_prio_def(data: dict[str, Any], style: PrioDefStyle) -> dict:
    """INTRO 生成し、優先日定義を要求 style で組み込む。

    現状 generator/intro.py は基本テンプレを返すので、style A/B/C に応じて
    末尾処理を本関数で行う。
    """
    res = generate_intro(data)
    intro_text = res["intro_text"]

    prios = data.get("priorityRightInformation", []) or []
    paris = [p for p in prios if p.get("parisPriorityDate")]
    kokunai = [p for p in prios if p.get("nationalPriorityDate")]

    has_priority = bool(paris or kokunai)
    if not has_priority:
        return {**res, "intro_text": intro_text}

    # 優先日（最先の優先権主張日）を選択
    if paris:
        prio_date = paris[0].get("parisPriorityDate", "")
    else:
        prio_date = kokunai[0].get("nationalPriorityDate", "")

    if not (prio_date and len(prio_date) == 8):
        return {**res, "intro_text": intro_text}

    # スタイルに応じて挿入
    if style == "A":
        # INTRO 末尾「なお接続」
        prio_text = to_seireki_wareki(prio_date) if paris else to_wareki(prio_date)
        # ※ 国内優先は和暦のみ (開発者ルール コメント[8])
        if kokunai and not paris:
            prio_text = to_wareki(prio_date)
        nao = f"なお、{prio_text}を以下「優先日」という。"
        intro_text = intro_text.rstrip("。") + "。" + nao
    elif style == "B":
        # INTRO 内括弧ネスト（既存テンプレに「（以下「優先日」という。）」を埋め込む）
        # B スタイルは intro.py のテンプレ書き換えが必要なので、現状 A にフォールバック
        prio_text = to_seireki_wareki(prio_date) if paris else to_wareki(prio_date)
        if kokunai and not paris:
            prio_text = to_wareki(prio_date)
        nao = f"なお、{prio_text}を以下「優先日」という。"
        intro_text = intro_text.rstrip("。") + "。" + nao
    elif style == "C":
        # CHRONO 末尾の独立段落で出力するので、INTRO は変更しない
        pass

    return {**res, "intro_text": intro_text, "_prio_date_iso": _yyyymmdd_to_iso(prio_date),
            "_prio_is_paris": bool(paris)}


def _yyyymmdd_to_iso(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _build_prio_def_independent(prio_date_iso: str, is_paris: bool) -> str:
    """C スタイル用の独立段落 PRIO_DEF を返す。"""
    yyyymmdd = prio_date_iso.replace("-", "")
    if is_paris:
        date_text = to_seireki_wareki(yyyymmdd)
    else:
        date_text = to_wareki(yyyymmdd)
    return f"　以下、{date_text}を「優先日」という。"


def generate(
    appno: str,
    kkokka_kind: KkokkaKind = "z_kakka",
    prio_def_style: PrioDefStyle = "A",
) -> GenerateResult:
    """1案件分の手続の経緯セクションを生成する。

    Args:
      kkokka_kind: 起案種別（HEAD の番号付け）。z_kakka='第１' / z_no_kakka='１'
      prio_def_style: 優先日定義の挿入スタイル
        A = INTRO 末尾「なお接続」（開発者ルール推奨）
        B = INTRO 内括弧ネスト
        C = CHRONO 末尾の独立段落

    前置報告書の出力可否は起案者パラメータではなく書類間関係（上申書との関連性）で
    決定する（generator/chronology.py の zenchi/joushin 判定を参照）。
    """
    # 1. doc_history data 取得
    data = _find_doc_history_data(appno)
    if data is None:
        return GenerateResult(
            text=f"<<参照エラー: doc_history.json not found for appno={appno}>>",
            trace=[], pattern="", apptype="", source_used="none",
            missing_fields=["doc_history.json"],
        )

    # 2. 起案日リスト取得（抽象層）
    entries, source_used = get_doc_dates_with_source(appno)
    if not entries:
        return GenerateResult(
            text=f"<<参照エラー: 起案日データが取得できませんでした (appno={appno})>>",
            trace=[], pattern="", apptype="", source_used="none",
            missing_fields=["doc_xmls/", "zenchi_drafting/", "jplatpat_dates/"],
        )

    # 3. INTRO 生成
    intro_res = _build_intro_with_prio_def(data, prio_def_style)
    intro_text = intro_res["intro_text"]
    pattern = intro_res["pattern"]
    apptype = intro_res["apptype"]
    missing = intro_res.get("missing_fields", [])

    # 4. CHRONO 生成
    lines = render_chronology(entries)

    # 5. 結合
    parts: list[str] = []
    parts.append(HEAD_BY_KIND[kkokka_kind])
    parts.append(intro_text)

    # 多世代分割: 「１　出願分割の経緯の概略」見出し + DIV_CHAIN 行群 +
    # 「２　本願の手続の経緯の概略」見出し
    if pattern == "分割_第3世代以降":
        # intro_text の前に「１　出願分割の経緯の概略」を挿入
        parts.insert(1, "１　出願分割の経緯の概略")
        from .classify import load_jpp_chain
        from .jp_dates import _to_zen, to_wareki
        chain = load_jpp_chain(appno)
        if chain:
            # chain[0] = 本願, chain[-1] = 最先
            earliest = chain[-1]
            ea = earliest.get("appno", "")
            ef = (earliest.get("filing_date") or "").replace("-", "")
            if ea and ef:
                parts.append(f"　最先の出願　：特願{_appno_dash(ea)}号（{to_wareki(ef)}）")
            # 第1世代分割 〜 第(N-1)世代分割
            for i, gen_node in enumerate(reversed(chain[1:-1]), start=1):
                a = gen_node.get("appno", "")
                f = (gen_node.get("filing_date") or "").replace("-", "")
                if a and f:
                    parts.append(f"　第{_to_zen(str(i))}世代分割：特願{_appno_dash(a)}号（{to_wareki(f)}）")
            # 本願
            own_filing = data.get("filingDate", "")
            if own_filing:
                parts.append(f"　本願　　　　：特願{_appno_dash(appno)}号（{to_wareki(own_filing)}）")

            # 二次小見出し + 本願の手続の経緯
            parts.append("")
            parts.append("２　本願の手続の経緯の概略")
            parts.append("　本願の出願後の手続の経緯の概略は、次のとおりである。")

    for ln in lines:
        parts.append(ln.rendered)
    if prio_def_style == "C" and "_prio_date_iso" in intro_res:
        parts.append(_build_prio_def_independent(
            intro_res["_prio_date_iso"], intro_res["_prio_is_paris"]
        ))

    text = "\n".join(parts)

    # 6. エクスキューズ集の構築
    #   揺れがあるルールについて、本生成器が選択した値と根拠を記録。
    #   生成出力末尾に薄文字でまとめて表示する想定。
    excuses: list[Excuse] = []
    has_priority = bool(
        (data.get("priorityRightInformation") or [])
    )
    if has_priority:
        excuses.append(Excuse(
            rule_id="prio_def_style",
            chosen="A（INTRO 末尾「なお接続」）",
            reason="開発者ルール / P1-history.md v0.4 推奨デフォルト",
            note="コーパスでは A=9 / B=6 / C=11 と揺れあり",
        ))
    # 通常出願（パターンA/B 揺れ）
    if pattern in ("通常内国出願", "国内優先権", "パリ条約優先権"):
        excuses.append(Excuse(
            rule_id="no_shutsugan_pattern",
            chosen="パターンA（「の出願」+ 出願日直後の括弧）",
            reason="開発者ルール / P1-history.md v0.4 推奨デフォルト",
            note="コーパスでは A=13件 / B（「の特許出願」）=7件と揺れあり",
        ))
    # 同日結合の接続語
    if any(ln.doc_type == "B" and len(ln.sources) >= 2 for ln in lines):
        excuses.append(Excuse(
            rule_id="connector_oyobi",
            chosen="「Ａ及びＢ」「Ａ、Ｂ、Ｃ及びＤ」",
            reason="公用文作成の考え方 (knowledge/standards/公用文作成の考え方.txt L1041-1051)",
            note="コーパス実例は「、」結合が多数派だが、公用文準拠で「及び」を採用",
        ))
    # 前置報告書
    has_zenchi = any(e.name == "前置報告書" for e in entries)
    if has_zenchi:
        excuses.append(Excuse(
            rule_id="zenchi_inclusion",
            chosen="出力（書く）",
            reason="開発者ルール準拠（暫定）。本来は上申書本文に前置報告書言及があれば書く",
            note="上申書本文は API 提供範囲外のため J-PlatPat スクレイピング未実施。今は常に出力",
        ))

    # 末尾エクスキューズ集の薄文字注記
    excuse_block = ""
    if excuses:
        excuse_lines = ["", "<!-- 以下、エクスキューズ集（薄文字で表示） -->", "<faint>"]
        excuse_lines.append("【本起案で採用したルール選択について（揺れあり項目）】")
        for ex in excuses:
            excuse_lines.append(f"・{ex.rule_id}: {ex.chosen}")
            excuse_lines.append(f"  根拠: {ex.reason}")
            if ex.note:
                excuse_lines.append(f"  備考: {ex.note}")
        excuse_lines.append("</faint>")
        excuse_block = "\n".join(excuse_lines)

    text_with_excuses = text + ("\n" + excuse_block if excuse_block else "")

    # 7. TRACE 構築
    trace: list[TraceEntry] = [TraceEntry(line=parts[0], basis="HEAD", source="keii_model.yaml")]
    trace.append(TraceEntry(line=intro_text, basis=f"INTRO ({pattern})", source="generator/intro.py"))
    for ln in lines:
        src_label = ",".join(set(e.source for e in ln.sources))
        trace.append(TraceEntry(line=ln.rendered, basis=f"CHRONO Type {ln.doc_type}", source=src_label))

    return GenerateResult(
        text=text, trace=trace, pattern=pattern, apptype=apptype,
        source_used=source_used, missing_fields=missing,
        excuses=excuses, text_with_excuses=text_with_excuses,
    )


# ============================================================================
# CLI
# ============================================================================

def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("appno")
    ap.add_argument("--kind", choices=["z_kakka", "z_no_kakka"], default="z_kakka")
    ap.add_argument("--prio-def-style", choices=["A", "B", "C"], default="A")
    ap.add_argument("--out", default=None, help="出力ファイル（指定なければ stdout）")
    args = ap.parse_args()

    res = generate(args.appno, args.kind, args.prio_def_style)
    if args.out:
        Path(args.out).write_text(res.text, encoding="utf-8")
        print(f"saved: {args.out}  pattern={res.pattern}  source={res.source_used}")
    else:
        print(res.text)


if __name__ == "__main__":
    _main()
