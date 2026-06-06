"""generator/dates.py — 書類日付取得の抽象層

呼び出し元（ルールベース生成器）に対し、データソースを隠蔽して
案件×書類×日付の統一リストを返す。

優先順位:
  1. Primary（API + 補完 補助ソース）
     - inventory/doc_xmls/{appno}/_summary.json
       Type A 書類の drafting-date（拒絶理由通知書/拒絶査定/補正の却下/特許査定）
     - inventory/zenchi_drafting/{appno}.json
       前置報告書の作成日（補助ソース）
     - inputs/.../doc_history/doc_history.json
       Type B 書類の legalDate、Type C 送達日（A02 legalDate）
  2. Fallback（API 不通時）
     - inventory/aux_dates/{appno}.json
       全書類分の起案日／受領日（補助ソース）

公開 API:
  get_doc_dates(appno) -> list[DocumentEntry]
  get_doc_dates_with_source(appno) -> tuple[list[DocumentEntry], str]  # source = 'primary' | 'fallback'
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve().parent
# (removed: PROJECT_ROOT not used in this layout)
KEII_PYTHON_ROOT = HERE.parent
INV_DIR = KEII_PYTHON_ROOT / "inventory"

DocType = Literal["A", "B", "C"]
DateSource = Literal[
    "api_xml",            # API XML drafting-date (Type A 主要書類)
    "external_zenchi",    # 補助ソース 前置報告書「作成日」
    "doc_history",        # doc_history.json legalDate (Type B / Type C)
    "external_fallback",  # 補助ソース (fallback)
]


@dataclass
class DocumentEntry:
    """書類1件分のメタデータ。"""
    name: str            # 起案上の名称（例: 拒絶理由通知書、意見書）
    code: str            # documentCode（A131 等。fallback 時は推定 or 空）
    date_iso: str        # YYYY-MM-DD
    doc_type: DocType
    source: DateSource
    note: str = ""       # 例: "（最後）" や "起案日 vs 作成日" 等
    raw: dict | None = field(default=None)


# ============================================================================
# 書類タイプ判定
# ============================================================================

# Allow-List（doc_history.json documentDescription / 起案上の名称 マッチ用）
TYPE_A_DOCS = {
    # documentCode → 起案上の名称
    # ★ 仕様書 (特許情報標準データ 4-01/4-18) と JPO API 実観測の突合は
    #   docs/doc_code_mapping.md を参照。新規 FB で未知のコードに遭遇しないよう
    #   仕様書定義と実 API 観測の両方を網羅する。
    "A01":  "特許査定",                # 仕様書 4-01 (実 API で観測)
    "A02":  "拒絶査定",
    "A03":  "特許査定",                # 旧来からの Lievito 認識。維持
    "A131": "拒絶理由通知書",
    "A132": "拒絶理由通知書",          # 仕様書 4-01 派生 (実 API 未観測、防御的)
    "A133": "拒絶理由通知書",          # 同上
    "A191": "補正の却下の決定",        # 仕様書 4-01 (実 API で観測)
    "A192": "補正の却下の決定",        # 同上派生 (実 API 未観測、防御的)
    "A502": "補正の却下の決定",        # 旧来からの Lievito 認識。維持
    "A913": "前置報告書",              # ★ 補助ソース取得が必要な理由:
                                       #   JPO API は A913 のレコード自体は返すが、
                                       #   その legalDate は「発送日」であり、
                                       #   起案上必要なのは前置報告書本文の「作成日」(= 起案日)。
                                       #   作成日は API 本文配信対象外のため、
                                       #   J-PlatPat 経過情報の前置報告書本文から抽出する。
                                       #   詳細: _from_zenchi_drafting() および
                                       #   06_fetch_zenchi_drafting.py
    "C13":  "当審拒絶理由通知書",      # 審判段階（前置解除後）
    "C30":  "面接記録",                # 仕様書 4-01 / 実 API で観測
    "C302": "応対記録",                # 仕様書 4-18 (審判段階) / 実 API で観測
}

TYPE_B_DOCS = {
    # documentCode → 起案上の名称 (chronology で「の提出」を付与)
    # ★ 仕様書突合の根拠と網羅性については docs/doc_code_mapping.md を参照。
    "A53":     "意見書",
    "A521":    "手続補正書",   # 仕様書 4-01 派生 (実 API 未観測、防御的)
    "A522":    "手続補正書",   # 同上
    "A523":    "手続補正書",   # （方式）は除外
    "A524":    "誤訳訂正書",   # 仕様書 4-01 / 実 API で観測
    "A631":    "翻訳文",       # 仕様書 4-01 翻訳文提出書 (PCT 派生)
    "A632":    "翻訳文",       # 仕様書 4-01 「国内書面」(経緯上は翻訳文表記で扱う)
    "A634":    "翻訳文",       # 仕様書 4-01 「国際出願翻訳文提出書」
    "A781":    "上申書",
    "A971015": "応対記録",
    "C60":     "審判請求書",
}

# 生成器が出力する書類タイプ判定（書類名キーワード）
TYPE_A_KEYWORDS = ["拒絶理由通知書", "拒絶査定", "補正の却下の決定", "特許査定", "前置報告書", "当審拒絶理由通知書"]


def is_type_a_by_name(name: str) -> bool:
    return any(kw in name for kw in TYPE_A_KEYWORDS)


# ============================================================================
# Primary 経路: API + 補完
# ============================================================================

def _find_doc_history_json(appno: str) -> Path | None:
    """doc_history.json のファイルパスを探索する。

    探索順:
      1. inventory/doc_history_collected/{appno}.json (一次ソース)
      2. settings.inputs_fallback_dir 配下 (オプション)
    """
    from .settings import get_inputs_fallback_dir
    # collected/ 優先
    collected = INV_DIR / "doc_history_collected" / f"{appno}.json"
    if collected.exists():
        return collected
    # 補助探索ディレクトリ（settings.yaml で設定）
    fallback = get_inputs_fallback_dir()
    if fallback is not None and fallback.exists():
        for p in fallback.rglob("doc_history.json"):
            if any(part.startswith(".") for part in p.parts):
                continue
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                if j.get("result", {}).get("data", {}).get("applicationNumber") == appno:
                    return p
            except Exception:
                continue
    return None


def _yyyymmdd_to_iso(s: str) -> str | None:
    if not s or len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _xml_body_has_saigo(appno: str, xml_filename: str) -> bool:
    """XML 全文から「最後の」を検索（body_excerpt 500字制限の補正）。"""
    if not xml_filename:
        return False
    xml_path = INV_DIR / "doc_xmls" / appno / xml_filename
    if not xml_path.exists():
        return False
    try:
        text = xml_path.read_bytes().decode("shift_jis", errors="replace")
    except Exception:
        return False
    # drafting-body 内に絞る
    import re as _re
    m = _re.search(r"<jp:drafting-body>(.*?)</jp:drafting-body>", text, _re.S)
    body = m.group(1) if m else text
    plain = _re.sub(r"<[^>]+>", " ", body)
    return "最後の" in plain


def _inbound_xml_filename(appno_digits: str, document_number: str) -> str | None:
    """doc_xmls/{appno}/_summary.json から inbound 書類の XML ファイル名を引く。

    summary の inbound エントリ doc_number は「{documentCode}_{documentNumber}」形式。
    A523 (手続補正書) の補正対象を XML 本文から判定するために使用する。
    """
    if not appno_digits or not document_number:
        return None
    summary_path = INV_DIR / "doc_xmls" / appno_digits / "_summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    target = f"A523_{document_number}"
    for d in data.get("documents", []):
        if d.get("endpoint") == "inbound" and d.get("doc_number") == target:
            return d.get("xml_filename") or None
    return None


def _a523_amends_spec_or_claims(appno_digits: str, document_number: str) -> bool | None:
    """手続補正書 (A523) が明細書または特許請求の範囲を補正対象に含むか判定する。

    手続補正書 XML の <jp:contents-of-amendment jp:kind-of-document="..."> の値で判定:
      - "claims"      → 特許請求の範囲の補正
      - "description" → 明細書の補正
    上記いずれかを含めば True。審判請求書のみの補正 (appeal-c60) や、
    その他 (出願人/代理人変更等) のみであれば False。

    返り値:
      True  → 明細書/特許請求の範囲を補正 (経緯に記載する)
      False → どちらの補正も含まない (経緯に記載しない)
      None  → 判定材料なし (inbound XML 不在等。保守的に記載を維持する)
    """
    fn = _inbound_xml_filename(appno_digits, document_number)
    if not fn:
        return None
    xml_path = INV_DIR / "doc_xmls" / appno_digits / fn
    if not xml_path.exists():
        return None
    try:
        text = xml_path.read_bytes().decode("shift_jis", errors="replace")
    except Exception:
        return None
    return ('kind-of-document="claims"' in text
            or 'kind-of-document="description"' in text)


def _from_api_xml_summary(appno: str) -> list[DocumentEntry]:
    """05_fetch_app_docs.py の _summary.json から Type A エントリを構築。

    XML 全文をスキャンして拒絶理由通知書本文に「最後の」記載があるかを
    DocumentEntry.raw["_body_has_saigo"] に保存。
    （body_excerpt は 500字制限のため、XML 直接読みで補正）
    """
    p = INV_DIR / "doc_xmls" / appno / "_summary.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[DocumentEntry] = []
    for d in data.get("documents", []):
        if d.get("endpoint") != "outbound":
            continue
        name = d.get("document_name", "")
        date_raw = d.get("drafting_date", "")
        date_iso = _yyyymmdd_to_iso(date_raw)
        if not (name and date_iso):
            continue
        if not is_type_a_by_name(name):
            continue
        body_has_saigo = False
        if "拒絶理由通知書" in name:
            body_has_saigo = _xml_body_has_saigo(appno, d.get("xml_filename", ""))
        raw = dict(d)
        raw["_body_has_saigo"] = body_has_saigo
        out.append(DocumentEntry(
            name=name,
            code="",
            date_iso=date_iso,
            doc_type="A",
            source="api_xml",
            raw=raw,
        ))
    return out


def _from_zenchi_drafting(appno: str) -> list[DocumentEntry]:
    """前置報告書の 補助ソース 作成日。"""
    p = INV_DIR / "zenchi_drafting" / f"{appno}.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[DocumentEntry] = []
    for d in data.get("drafting_dates_all", []):
        if isinstance(d, str):  # 有効な YYYY-MM-DD
            out.append(DocumentEntry(
                name="前置報告書",
                code="A913",
                date_iso=d,
                doc_type="A",
                source="external_zenchi",
                raw={"original": d},
            ))
    return out


def _from_doc_history_json(p: Path) -> list[DocumentEntry]:
    """doc_history.json から Type B / Type C を構築。"""
    raw = json.loads(p.read_text(encoding="utf-8"))
    data = raw.get("result", {}).get("data", {})
    biblio = data.get("bibliographyInformation", []) or []
    # 手続補正書 (A523) の補正対象を XML から判定するために appno (数字) を取得
    appno_digits = re.sub(r"\D", "", str(data.get("applicationNumber", "")))
    out: list[DocumentEntry] = []

    # 翻訳文の提出日を決定する優先ルール:
    #   A634 (国際出願翻訳文提出書セット) があればその日付を採用
    #   A634 が無く A632 (国内書面+翻訳文セット) のみなら A632 を採用
    # これは PCT 国内移行で国内書面と別日に翻訳文実体を提出するケースに対応。
    a634_dates: set[str] = set()
    a632_dates: set[str] = set()
    for b in biblio:
        for d in b.get("documentList", []) or []:
            code = d.get("documentCode", "")
            legal = d.get("legalDate", "")
            if not legal:
                continue
            if code == "A634":
                a634_dates.add(legal)
            elif code == "A632":
                a632_dates.add(legal)
    translation_dates = a634_dates if a634_dates else a632_dates

    # Type B
    seen_translation_dates: set[str] = set()
    for b in biblio:
        for d in b.get("documentList", []) or []:
            code = d.get("documentCode", "")
            desc = d.get("documentDescription", "")
            legal = d.get("legalDate", "")
            iso = _yyyymmdd_to_iso(legal)
            if not iso:
                continue
            # Allow-list 判定 (詳細な突合表: docs/doc_code_mapping.md)
            if code == "A53":
                name = "意見書"
            elif code in ("A521", "A522", "A523"):
                if "（方式）" in desc:
                    continue  # 方式は除外
                # 明細書・特許請求の範囲のどちらの補正も含まない手続補正書
                # (例: 審判請求書のみの補正) は経緯に記載しない。
                # 審判請求の前後は無関係で、補正対象そのもので判定する。
                # 判定材料 (inbound XML) が無い場合は保守的に記載を維持。
                amends = _a523_amends_spec_or_claims(appno_digits, d.get("documentNumber", ""))
                if amends is False:
                    continue
                name = "手続補正書"
            elif code == "A524":
                name = "誤訳訂正書"
            elif code == "A971015":
                name = "応対記録"
            elif code == "C60":
                name = "審判請求書"
            elif code == "A781":
                name = "上申書"
            elif code in ("A631", "A632", "A634"):
                # 翻訳文提出: 採用対象の日付のみ (重複は 1 件)
                if legal not in translation_dates:
                    continue
                if legal in seen_translation_dates:
                    continue
                seen_translation_dates.add(legal)
                name = "翻訳文"  # chronology.py が「の提出」を付与
            else:
                continue
            out.append(DocumentEntry(
                name=name,
                code=code,
                date_iso=iso,
                doc_type="B",
                source="doc_history",
                raw=d,
            ))

    # Type C: A02 (拒絶査定) の legalDate を「謄本送達日」として使用
    for b in biblio:
        for d in b.get("documentList", []) or []:
            if d.get("documentCode") == "A02":
                legal = d.get("legalDate", "")
                iso = _yyyymmdd_to_iso(legal)
                if iso:
                    out.append(DocumentEntry(
                        name="原査定の謄本の送達",
                        code="(C)",
                        date_iso=iso,
                        doc_type="C",
                        source="doc_history",
                        raw={"a02_legalDate": legal},
                    ))
                break  # A02 は通常1件

    # Type A from doc_history: 審判段階の当審拒絶理由通知書 (C13)
    # 審判段階の書類は JPO API の outbound XML で取得できないため、doc_history から拾う
    for b in biblio:
        for d in b.get("documentList", []) or []:
            if d.get("documentCode") == "C13":
                legal = d.get("legalDate", "")
                iso = _yyyymmdd_to_iso(legal)
                if iso:
                    out.append(DocumentEntry(
                        name="当審拒絶理由通知書",
                        code="C13",
                        date_iso=iso,
                        doc_type="A",
                        source="doc_history",
                        raw=d,
                    ))
    return out


# ============================================================================
# Fallback 経路: 補助ソース全書類
# ============================================================================

def _from_external_fallback(appno: str) -> list[DocumentEntry]:
    """07_fetch_external_fallback.py の aux_dates/{appno}.json から構築。"""
    p = INV_DIR / "aux_dates" / f"{appno}.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[DocumentEntry] = []
    for d in data.get("documents", []):
        name = d.get("name", "")
        is_a = d.get("is_type_a", False)
        if is_a:
            iso = d.get("drafting_date")
            if not iso:
                continue
            out.append(DocumentEntry(
                name=name,
                code="",
                date_iso=iso,
                doc_type="A",
                source="external_fallback",
                raw=d,
            ))
        elif d.get("is_type_b_target"):
            iso = d.get("table_date")
            if not iso or not re.match(r'^\d{4}-\d{2}-\d{2}$', iso):
                continue
            # 書類名を allow-list 名に正規化
            normalized = _normalize_type_b_name(name)
            if not normalized:
                continue
            out.append(DocumentEntry(
                name=normalized,
                code="",
                date_iso=iso,
                doc_type="B",
                source="external_fallback",
                raw=d,
            ))

    # Type C: A02（拒絶査定）に対応する送達日は、テーブルの拒絶査定 table_date が legalDate と同等
    # → Type A の external_fallback から拒絶査定エントリの table_date を引いて Type C エントリを生成
    for d in data.get("documents", []):
        if d.get("is_type_a") and "拒絶査定" in d.get("name", ""):
            iso = d.get("table_date")
            if iso and re.match(r'^\d{4}-\d{2}-\d{2}$', iso):
                out.append(DocumentEntry(
                    name="原査定の謄本の送達",
                    code="(C)",
                    date_iso=iso,
                    doc_type="C",
                    source="external_fallback",
                    raw={"a02_table_date": iso},
                ))
            break
    return out


def _normalize_type_b_name(raw_name: str) -> str | None:
    """補助ソース 表記を Allow-List 名に正規化。方式は除外。"""
    if "意見書" in raw_name:
        return "意見書"
    if "手続補正書" in raw_name:
        if "方式" in raw_name:
            return None
        return "手続補正書"
    if "審判請求書" in raw_name:
        return "審判請求書"
    if "上申書" in raw_name:
        return "上申書"
    if "応対記録" in raw_name or "面接" in raw_name:
        return "応対記録"
    if "翻訳文" in raw_name:
        return "翻訳文"
    if "誤訳訂正" in raw_name:
        return "誤訳訂正書"
    return None


# ============================================================================
# 公開 API
# ============================================================================

def get_doc_dates_with_source(appno: str) -> tuple[list[DocumentEntry], str]:
    """指定appnoの全書類日付を統一形式で返す。

    Returns:
      (entries, source_used)  source_used ∈ {'primary', 'fallback', 'none'}
    """
    # Primary 経路
    summary = _from_api_xml_summary(appno)
    zenchi = _from_zenchi_drafting(appno)
    doc_history_path = _find_doc_history_json(appno)
    history = _from_doc_history_json(doc_history_path) if doc_history_path else []

    # primary が成立する条件: API summary もしくは doc_history のいずれかが存在
    if summary or history:
        entries = summary + zenchi + history
        # primary に「誤訳訂正書」が含まれない場合、fallback (補助ソース全書類) から
        # 誤訳訂正書のエントリのみ補完する (API/XML には誤訳訂正書が出ないため)。
        if not any(e.name == "誤訳訂正書" for e in entries):
            for e in _from_external_fallback(appno):
                if e.name == "誤訳訂正書":
                    entries.append(e)
        # 日付昇順ソート（同日内は doc_type A→B→C 安定）
        entries.sort(key=lambda e: (e.date_iso, {"A": 0, "B": 1, "C": 2}[e.doc_type]))
        entries = _dedupe_same_day_same_name(entries)
        return entries, "primary"

    # Fallback 経路
    fallback = _from_external_fallback(appno)
    if fallback:
        fallback.sort(key=lambda e: (e.date_iso, {"A": 0, "B": 1, "C": 2}[e.doc_type]))
        fallback = _dedupe_same_day_same_name(fallback)
        return fallback, "fallback"

    return [], "none"


def _dedupe_same_day_same_name(entries: list[DocumentEntry]) -> list[DocumentEntry]:
    """同日・同タイプ・同名の重複を除去 (順序は保つ)。"""
    seen: set[tuple[str, str, str]] = set()
    out: list[DocumentEntry] = []
    for e in entries:
        key = (e.date_iso, e.doc_type, e.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def get_doc_dates(appno: str) -> list[DocumentEntry]:
    """シンプル版。entries のみ返す。"""
    entries, _ = get_doc_dates_with_source(appno)
    return entries


# ============================================================================
# CLI（動作確認用）
# ============================================================================

def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("appno")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    entries, source = get_doc_dates_with_source(args.appno)
    if args.json:
        print(json.dumps({
            "appno": args.appno,
            "source": source,
            "entries": [e.__dict__ for e in entries],
        }, ensure_ascii=False, indent=2, default=str))
        return

    print(f"appno: {args.appno}")
    print(f"source_used: {source}")
    print(f"entries: {len(entries)}")
    for e in entries:
        print(f"  {e.date_iso}  type={e.doc_type}  src={e.source:20s}  name={e.name}")


if __name__ == "__main__":
    _main()
