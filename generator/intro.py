"""冒頭文（本願は、…次のとおりである。）の生成。

入力: doc_history.json の data 部
出力: 1行の冒頭文文字列
"""
from __future__ import annotations

from typing import Any

from .classify import detect_pattern
from .jp_dates import (
    to_seireki,
    to_seireki_wareki,
    to_wareki,
    country_name,
)


def _format_paris_priority_list(prios: list[dict]) -> str:
    """複数のパリ優先を「２０●●年●月●日、米国」形式で連結。"""
    parts: list[str] = []
    for p in prios:
        date_str = p.get("parisPriorityDate", "")
        cd = p.get("parisPriorityCountryCd", "")
        if date_str and len(date_str) == 8:
            parts.append(to_seireki(date_str) + "、" + country_name(cd))
    return "、".join(parts)


def _format_kokunai_priority_list(prios: list[dict]) -> str:
    """国内優先（複数想定）を「令和●年●月●日」で連結。"""
    parts: list[str] = []
    for p in prios:
        date_str = p.get("nationalPriorityDate", "")
        if date_str and len(date_str) == 8:
            parts.append(to_wareki(date_str))
    return "、".join(parts)


def generate_intro(data: dict[str, Any]) -> dict:
    """冒頭文と判定情報を返す。

    返り値: {pattern, apptype, intro_text, missing_fields}
    """
    cls = detect_pattern(data)
    pattern = cls["pattern"]

    filing_date = data.get("filingDate", "")
    intl_filing = data.get("internationalFilingDate", "") or filing_date
    paris_prios = [p for p in (data.get("priorityRightInformation") or []) if p.get("parisPriorityDate")]
    kokunai_prios = [p for p in (data.get("priorityRightInformation") or []) if p.get("nationalPriorityDate")]

    missing: list[str] = []
    text = ""

    # 全テンプレ冒頭の「　本願は、」を頭につける（全角空白1個）
    HEAD = "　本願は、"
    TAIL = "その手続の経緯の概略は、次のとおりである。"

    if pattern == "通常内国出願":
        if not filing_date:
            missing.append("filingDate")
            text = ""
        else:
            text = f"{HEAD}{to_wareki(filing_date)}の出願であって、{TAIL}"

    elif pattern == "国内優先権":
        if not filing_date or not kokunai_prios:
            missing.extend(["filingDate" if not filing_date else None,
                            "nationalPriorityDate" if not kokunai_prios else None])
            missing = [m for m in missing if m]
        prio_str = _format_kokunai_priority_list(kokunai_prios)
        text = f"{HEAD}{to_wareki(filing_date)}（優先権主張　{prio_str}）の出願であって、{TAIL}"

    elif pattern == "国内優先＋日本語ＰＣＴ":
        prio_str = _format_kokunai_priority_list(kokunai_prios)
        if prio_str:
            text = (f"{HEAD}{to_seireki_wareki(intl_filing)}を国際出願日とする日本語特許出願であって"
                    f"（優先権主張　{prio_str}）、{TAIL}")
        else:
            # priorityRightInformation が空のまま本枝に入ることがある (detect_pattern の fallback).
            text = (f"{HEAD}{to_seireki_wareki(intl_filing)}を国際出願日とする日本語特許出願であって、{TAIL}")

    elif pattern == "パリ条約優先権":
        prio_str = _format_paris_priority_list(paris_prios)
        text = f"{HEAD}{to_wareki(filing_date)}（パリ条約による優先権主張　{prio_str}）の出願であって、{TAIL}"

    elif pattern == "パリ条約優先＋ＰＣＴ":
        prio_str = _format_paris_priority_list(paris_prios)
        if prio_str:
            text = (f"{HEAD}{to_seireki_wareki(intl_filing)}（パリ条約による優先権主張外国庁受理"
                    f"{prio_str}）を国際出願日とする外国語特許出願であって、{TAIL}")
        else:
            text = (f"{HEAD}{to_seireki_wareki(intl_filing)}を国際出願日とする外国語特許出願であって、{TAIL}")

    elif pattern == "パリ条約優先＋外国語書面出願":
        prio_str = _format_paris_priority_list(paris_prios)
        text = (f"{HEAD}{to_wareki(filing_date)}の外国語書面出願（パリ条約による優先権主張、"
                f"{prio_str}）であって、{TAIL}")

    elif pattern == "分割_基本形":
        # parentApplicationInformation の構造（JPO API実測）:
        #   {"parentApplicationNumber": "YYYYNNNNNN", "filingDate": "YYYYMMDD"}
        parent = data.get("parentApplicationInformation", {}) or {}
        parent_appno = parent.get("parentApplicationNumber", "")
        parent_filing = parent.get("filingDate", "")
        if not (parent_appno and parent_filing and filing_date):
            missing.extend([k for k, v in [
                ("parent.parentApplicationNumber", parent_appno),
                ("parent.filingDate", parent_filing),
                ("filingDate", filing_date)] if not v])
        if missing:
            text = f"<<参照エラー: 分割親情報不足 {missing}>>"
        else:
            # 親が外国語書面出願なら「外国語書面出願（特願xxx号）」、それ以外は「特願xxx号」
            from .classify import parent_apptype_phrase, divisional_terminal_phrase
            parent_label = parent_apptype_phrase(parent_appno)
            appno_str = f"特願{_format_appno_with_dash(parent_appno)}号"
            parent_phrase = f"{parent_label}（{appno_str}）" if parent_label else appno_str
            # 本願自身の終端 (外国語書面出願か通常か)
            self_terminal = divisional_terminal_phrase(data)
            # 優先権括弧 (本願の priorityRightInformation ベース)
            prio_str = _format_paris_priority_list(paris_prios) if paris_prios else ""
            paren = f"（パリ条約による優先権主張　{prio_str}）" if prio_str else ""
            text = (f"{HEAD}{to_wareki(parent_filing)}に出願した{parent_phrase}の一部を"
                    f"{to_wareki(filing_date)}に新たな{self_terminal}としたものであって"
                    f"{paren}、{TAIL}")

    elif pattern == "分割_第2世代":
        # 親系列 の filing_date は ISO 形式 'YYYY-MM-DD'
        from .classify import load_aux_chain
        chain = load_aux_chain(data.get("applicationNumber", "") or "")
        if len(chain) >= 3:
            grandparent = chain[2]
            parent = chain[1]
            gp_appno = grandparent.get("appno", "")
            gp_filing = (grandparent.get("filing_date") or "").replace("-", "")
            p_appno = parent.get("appno", "")
            p_filing = (parent.get("filing_date") or "").replace("-", "")
            if gp_filing and p_filing and filing_date:
                from .classify import parent_apptype_phrase, divisional_terminal_phrase
                # 最先 (gp = 祖父 = 最も古い親) の apptype phrase
                gp_label = parent_apptype_phrase(gp_appno)
                gp_appno_str = f"特願{_format_appno_with_dash(gp_appno)}号"
                gp_phrase = f"{gp_label}（{gp_appno_str}）" if gp_label else gp_appno_str
                self_terminal = divisional_terminal_phrase(data)
                prio_str = _format_paris_priority_list(paris_prios) if paris_prios else ""
                paren = f"（パリ条約による優先権主張　{prio_str}）" if prio_str else ""
                text = (f"{HEAD}{to_wareki(gp_filing)}に出願した{gp_phrase}の一部を"
                        f"{to_wareki(p_filing)}に新たな特許出願とした特願"
                        f"{_format_appno_with_dash(p_appno)}号の一部を"
                        f"{to_wareki(filing_date)}に新たな{self_terminal}としたものであって"
                        f"{paren}、{TAIL}")
            else:
                text = "<<参照エラー: 第2世代分割の filingDate 不足>>"
        else:
            text = "<<参照エラー: 第2世代分割の chain 情報不足>>"

    elif pattern == "分割_第3世代以降":
        # 第N世代分割の DIV_BLOCK 構造は keii.py 側で組み立てる。
        # ここでは intro 部分だけ返す。
        from .classify import load_aux_chain
        chain = load_aux_chain(data.get("applicationNumber", "") or "")
        if chain:
            earliest = chain[-1]
            earliest_appno = earliest.get("appno", "")
            earliest_filing = (earliest.get("filing_date") or "").replace("-", "")
            generation_num = len(chain) - 1  # 親の代数（本願は除く）
            n_chr = _gen_num_to_kanji(generation_num)
            if earliest_filing:
                # 第3世代以降は最先出願を「特願xxx号」で示す形式が確立しており、
                # 外国語書面ラベルは挿入しない (corpus 慣行)。ただし parent が
                # 外国語書面出願の場合、その旨を別途記載すべきケースは個別
                # 分析の対象とする (FB が出たら拡張)。
                text = (f"{HEAD}{to_wareki(filing_date)}にされた特許法４４条１項の規定による"
                        f"特許出願であって、{to_wareki(earliest_filing)}に出願した特願"
                        f"{_format_appno_with_dash(earliest_appno)}号を最先の出願とする、"
                        f"いわゆる第{n_chr}世代の分割出願であるところ、出願の分割の経緯は、"
                        f"次のとおりである。なお、括弧内は当該出願の提出日を示す。")
            else:
                text = "<<参照エラー: 多世代分割の最先出願 filingDate 不足>>"
        else:
            text = "<<参照エラー: 多世代分割の chain 情報不足>>"

    elif pattern == "分割_PCT原出願":
        # 親が PCT国内段階。apptype_phrase（外国語/日本語）と terminal_phrase
        # （翻訳文有無）で分岐する汎用テンプレ。corpus 例:
        #  「２０１７年（平成２９年）１１月１３日を国際出願日とする外国語特許出願
        #   （特願２０２０－５２４８８０号）の一部を、令和３年１２月２日に新たな
        #   外国語書面出願としたものであって（パリ条約による優先権主張…）、…」
        from .classify import (
            divisional_root_apptype_phrase,
            divisional_terminal_phrase,
        )
        parent = data.get("parentApplicationInformation", {}) or {}
        parent_appno = parent.get("parentApplicationNumber", "")
        parent_filing = parent.get("filingDate", "")
        if not (parent_appno and parent_filing and filing_date):
            text = f"<<参照エラー: 分割_PCT原出願 親情報不足>>"
        else:
            apptype_phrase = divisional_root_apptype_phrase(data)
            terminal_phrase = divisional_terminal_phrase(data)
            prio_str = _format_paris_priority_list(paris_prios) if paris_prios else ""
            paren = f"（パリ条約による優先権主張　{prio_str}）" if prio_str else ""
            text = (f"{HEAD}{to_seireki_wareki(parent_filing)}を国際出願日とする"
                    f"{apptype_phrase}（特願{_format_appno_with_dash(parent_appno)}号）の一部を、"
                    f"{to_wareki(filing_date)}に新たな{terminal_phrase}としたものであって"
                    f"{paren}、{TAIL}")

    elif pattern == "分割_PCT原出願_第2世代":
        # 第2世代分割で最先がPCT国内段階。corpus 例 (2023-020552):
        #  「２０１７年（平成２９年）２月２０日を国際出願日とする特許出願（特願２０１８－
        #   ５５７２２９号）の一部を、令和２年５月２０日に新たな特許出願とした
        #   特願２０２０－８７８７４号の一部を令和３年１０月１３日に新たな外国語書面
        #   出願としたものであって（パリ条約による優先権主張、…）、…」
        from .classify import (
            divisional_root_apptype_phrase,
            divisional_terminal_phrase,
            load_aux_chain,
        )
        parent = data.get("parentApplicationInformation", {}) or {}
        parent_appno = parent.get("parentApplicationNumber", "")
        parent_filing = parent.get("filingDate", "")
        chain = load_aux_chain(data.get("applicationNumber", "") or "")
        if len(chain) >= 3:
            earliest = chain[-1]
            earliest_appno = earliest.get("appno", "")
            earliest_filing = (earliest.get("filing_date") or "").replace("-", "")
        else:
            earliest_appno = ""
            earliest_filing = ""
        if not (earliest_filing and earliest_appno and parent_appno and parent_filing and filing_date):
            text = f"<<参照エラー: 分割_PCT原出願_第2世代 親情報不足>>"
        else:
            apptype_phrase = divisional_root_apptype_phrase(data)
            terminal_phrase = divisional_terminal_phrase(data)
            prio_str = _format_paris_priority_list(paris_prios) if paris_prios else ""
            paren = f"（パリ条約による優先権主張　{prio_str}）" if prio_str else ""
            text = (f"{HEAD}{to_seireki_wareki(earliest_filing)}を国際出願日とする"
                    f"{apptype_phrase}（特願{_format_appno_with_dash(earliest_appno)}号）の一部を、"
                    f"{to_wareki(parent_filing)}に新たな特許出願とした特願"
                    f"{_format_appno_with_dash(parent_appno)}号の一部を、"
                    f"{to_wareki(filing_date)}に新たな{terminal_phrase}としたものであって"
                    f"{paren}、{TAIL}")

    else:
        text = f"<<未対応パターン: {pattern}>>"

    return {
        "pattern": pattern,
        "apptype": cls["apptype"],
        "classification": cls,
        "intro_text": text,
        "missing_fields": missing,
    }


def _format_appno_with_dash(appno_10: str) -> str:
    """10桁出願番号 (YYYYNNNNNN) → 「２０●●－●●●●●●」全角ハイフン形式。"""
    if not appno_10 or len(appno_10) != 10:
        return appno_10
    from .jp_dates import _to_zen
    return f"{_to_zen(appno_10[:4])}－{_to_zen(appno_10[4:])}"


def _gen_num_to_kanji(n: int) -> str:
    """世代数を漢数字表記。例: 9 → 「９」（実起案は全角数字使用が一般）。"""
    from .jp_dates import _to_zen
    return _to_zen(str(n))
