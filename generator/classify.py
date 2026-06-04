"""doc_history.json を読んで AppType と pattern を判定する（純Python）。"""
from __future__ import annotations

from typing import Any


def get_data(raw: dict[str, Any]) -> dict[str, Any]:
    return raw.get("result", {}).get("data", {}) or {}


def has_translation_doc(data: dict[str, Any]) -> bool:
    """documentList に翻訳文を含むか（PCT外国語/外国語書面の判定材料）。"""
    biblio = data.get("bibliographyInformation", []) or []
    for b in biblio:
        for d in b.get("documentList", []) or []:
            desc = d.get("documentDescription", "")
            code = d.get("documentCode", "")
            if "翻訳文" in desc or code in ("A632",):
                return True
    return False


def is_divisional(data: dict[str, Any]) -> tuple[bool, dict | None]:
    """親出願情報の有無を確認し、分割と判定すべきかを返す。

    A1_verify_pct_5kt_rule.py で確認済の事実:
      - 親 appno 5桁目=5 = PCT国内段階。これが本願の親なら **「分割_PCT原出願」** パターン
      - 親 appno 5桁目≠5 で filingDate 異なる = 通常分割（基本形）

    parent と own の filingDate が同一なら分割でない（例外: PCT 移行関連の同日記録）
    """
    parent = data.get("parentApplicationInformation", {}) or {}
    if not (parent and len(parent) > 0):
        return False, None
    parent_appno = parent.get("parentApplicationNumber", "")
    parent_filing = parent.get("filingDate", "")
    own_filing = data.get("filingDate", "")
    if not parent_appno or not parent_filing:
        return False, parent
    if parent_filing == own_filing:
        return False, parent
    return True, parent


def parent_is_pct_national_phase(data: dict[str, Any]) -> bool:
    """親出願番号の 5桁目=5 なら親が PCT 国内段階（=分割_PCT原出願 パターン）。"""
    parent = data.get("parentApplicationInformation", {}) or {}
    if not parent:
        return False
    parent_appno = parent.get("parentApplicationNumber", "")
    return is_pct_national_phase(parent_appno)


def chain_root_is_pct_national_phase(data: dict[str, Any]) -> bool:
    """divisionalApplicationInformation の最上流 (divisionalGeneration=0) が
    PCT 国内段階なら True。多世代分割で最先がPCTのケース検出に使う。
    appno 5桁目=5 または internationalApplicationNumber が空でないことで判定。
    """
    divs = data.get("divisionalApplicationInformation", []) or []
    for dv in divs:
        if str(dv.get("divisionalGeneration", "")) == "0":
            top_appno = dv.get("applicationNumber", "") or ""
            top_intl = dv.get("internationalApplicationNumber", "") or ""
            return is_pct_national_phase(top_appno) or bool(top_intl)
    return False


def aux_root_is_pct_national_phase(data: dict[str, Any]) -> bool:
    """親系列 の最後尾（最先 appno）が PCT 国内段階なら True
    （chain_root_is_pct_national_phase のフォールバック）。"""
    appno = data.get("applicationNumber", "") or ""
    chain = load_aux_chain(appno)
    if not chain:
        return False
    return is_pct_national_phase(chain[-1].get("appno", "") or "")


def divisional_root_apptype_phrase(data: dict[str, Any]) -> str:
    """divisionalApplicationInformation の最上流 (divisionalGeneration=0) の
    internationalApplicationNumber プレフィクスから apptype フレーズを返す。

    判定:
      JP*       → "日本語特許出願"
      その他     → "外国語特許出願"  (US/EP/IB/CN/KR 等)
      intl 空   → "特許出願"          (情報不足。安全側のフォールバック)
    """
    divs = data.get("divisionalApplicationInformation", []) or []
    earliest_intl = ""
    for dv in divs:
        if str(dv.get("divisionalGeneration", "")) == "0":
            earliest_intl = dv.get("internationalApplicationNumber", "") or ""
            break
    if not earliest_intl:
        return "特許出願"
    if earliest_intl.startswith("JP"):
        return "日本語特許出願"
    return "外国語特許出願"


def divisional_terminal_phrase(data: dict[str, Any]) -> str:
    """本願自身の翻訳文 (A631/A632) 有無で終端表現を返す。
    あり → 「外国語書面出願」 / なし → 「特許出願」。"""
    if has_translation_doc(data):
        return "外国語書面出願"
    return "特許出願"


def _load_doc_history_data(appno: str) -> dict[str, Any] | None:
    """inventory/doc_history_collected/{appno}.json の data 部を返す (なければ None)。"""
    if not appno:
        return None
    import json as _json
    from pathlib import Path as _Path
    p = _Path(__file__).resolve().parent.parent / "inventory" / "doc_history_collected" / f"{appno}.json"
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8")).get("result", {}).get("data", {}) or {}
    except (OSError, ValueError):
        return None


def parent_apptype_phrase(parent_appno: str) -> str | None:
    """親出願 (parent_appno) の doc_history を読み、apptype に対応する
    「分割の親」として表記すべきフレーズを返す。

    返り値:
      - "外国語書面出願"           — 親が国内出願 + A631/A632 あり (外国語書面)
      - None                         — 親が通常の国内出願 (= 「特願xxx号」のみ)
      - PCT 系は本関数では返さない (intro.py が pattern 分岐で別途扱う)

    親 doc_history が無ければ None を返す (= フォールバック「特願xxx号」)。
    """
    data = _load_doc_history_data(parent_appno)
    if not data:
        return None
    intl_appno = data.get("internationalApplicationNumber") or ""
    if intl_appno:
        # PCT 国内段階 — 本関数では扱わない (上位の pattern 分岐に任せる)
        return None
    if has_translation_doc(data):
        return "外国語書面出願"
    return None


def is_pct_national_phase(appno: str) -> bool:
    """出願番号10桁の5桁目（=下6桁の1桁目）が "5" なら PCT 国内段階。

    ユーザー提示ルール（A1_verify_pct_5kt_rule.py で precision 100% 検証済）。
    例:
      2022510315 → "5" → PCT 国内段階
      2018244177 → "2" → 通常出願
    """
    if not appno or len(appno) != 10:
        return False
    return appno[4] == "5"


def detect_apptype(data: dict[str, Any]) -> str:
    """history-rules.md §1.1 の AppType A/B/C/D を返す。

    シグナル優先順位:
      1. internationalApplicationNumber + nationalPublicationNumber → A
      2. internationalApplicationNumber → B
      3. **本願 appno 5桁目=5** → PCT 国内段階。documentList の翻訳文有無で A/B 区別
      4. has_translation_doc → C （外国語書面出願）
      5. それ以外 → D
    """
    intl_appno = data.get("internationalApplicationNumber", "") or ""
    nat_pub = data.get("nationalPublicationNumber", "") or ""
    own_appno = data.get("applicationNumber", "") or ""

    if intl_appno and nat_pub:
        return "A"
    if intl_appno:
        return "B"
    # 5桁目=5 ルール
    if is_pct_national_phase(own_appno):
        # 翻訳文書類あり → 外国語特許出願 (A) / なし → 日本語特許出願 (B)
        return "A" if has_translation_doc(data) else "B"
    if has_translation_doc(data):
        return "C"
    return "D"


def load_aux_chain(appno: str) -> list[dict]:
    """inventory/aux_appinfo/{appno}.json から chain を返す（補助ソース由来）。

    各エントリ: {appno, filing_date, sokyu_date, parent_appno, is_divisional, ...}
    chain[0] = 本願、chain[-1] = 最先の出願。
    user 方針により分割系列の正の経路は JPP（API 不採用）。
    """
    import json as _json
    from pathlib import Path as _Path
    p = _Path(__file__).resolve().parent.parent / "inventory" / "aux_appinfo" / f"{appno}.json"
    if not p.exists():
        return []
    return _json.loads(p.read_text(encoding="utf-8")).get("chain", [])


# 後方互換のため別名も残す（旧 API 経由の chain は廃止）
def load_parent_chain(appno: str) -> list[dict]:
    """互換 alias: 親系列 を返す。"""
    return load_aux_chain(appno)


def get_generation(data: dict[str, Any]) -> int:
    """親系列 から世代数を取得。本願=0、親1代=1、…。

    親系列 がない場合は 0（=非分割または取得失敗）。フォールバックは設けない
    （ユーザー方針: JPP が取れなければエラーとする）。
    """
    appno = data.get("applicationNumber", "") or ""
    chain = load_aux_chain(appno)
    if not chain:
        return 0
    return len(chain) - 1


def detect_pattern(data: dict[str, Any]) -> dict:
    """yaml の honyo_intro_patterns に対応する pattern キーを返す。

    返り値: {pattern, apptype, has_priority, priority_type, is_pct, is_divisional, generation, ...}
    """
    apptype = detect_apptype(data)
    intl_appno = data.get("internationalApplicationNumber", "") or ""
    is_pct = bool(intl_appno)

    prios = data.get("priorityRightInformation", []) or []
    paris_prios = [p for p in prios if p.get("parisPriorityDate")]
    kokunai_prios = [p for p in prios if p.get("nationalPriorityDate")]
    has_paris = len(paris_prios) > 0
    has_kokunai = len(kokunai_prios) > 0

    is_div, parent_info = is_divisional(data)
    parent_is_pct = parent_is_pct_national_phase(data) if is_div else False
    chain_root_pct = (
        chain_root_is_pct_national_phase(data) or aux_root_is_pct_national_phase(data)
    ) if is_div else False
    generation = get_generation(data)

    pattern = "通常内国出願"
    if is_div:
        # 世代 (chain depth) で分岐。最先が PCT国内段階の場合は親がPCT扱い
        if generation >= 3:
            pattern = "分割_第3世代以降"
        elif generation == 2 and chain_root_pct:
            pattern = "分割_PCT原出願_第2世代"
        elif generation == 2:
            pattern = "分割_第2世代"
        elif parent_is_pct or chain_root_pct:
            pattern = "分割_PCT原出願"
        else:
            pattern = "分割_基本形"
    elif is_pct or apptype in ("A", "B"):
        # 本願自体がPCT国内段階
        if has_paris:
            pattern = "パリ条約優先＋ＰＣＴ"
        elif has_kokunai:
            pattern = "国内優先＋日本語ＰＣＴ"
        else:
            # 優先権なしのPCT。生成器テンプレ未対応のため、暫定的に PCT 寄りパターン
            pattern = "パリ条約優先＋ＰＣＴ" if apptype == "A" else "国内優先＋日本語ＰＣＴ"
    elif has_paris:
        pattern = "パリ条約優先権"
    elif has_kokunai:
        pattern = "国内優先権"
    # 外国語書面出願の判定（apptype C, 非分割のみ。分割案件の上書き禁止）
    if not is_div and apptype == "C" and has_paris:
        pattern = "パリ条約優先＋外国語書面出願"

    return {
        "pattern": pattern,
        "apptype": apptype,
        "has_paris": has_paris,
        "has_kokunai": has_kokunai,
        "is_pct": is_pct,
        "is_divisional": is_div,
        "n_paris_prios": len(paris_prios),
        "n_kokunai_prios": len(kokunai_prios),
        "generation": generation,
        "parent_is_pct": parent_is_pct,
        "chain_root_pct": chain_root_pct,
    }
