"""02_inventory_doc_history.py — doc_history.json の充足度集計

目的:
  各案件の doc_history.json から、手続の経緯テンプレ（11パターン）の
  必要フィールドが取得可能かを集計する。

検出フィールド:
  - applicationNumber, inventionTitle, filingDate
  - publication: publicationNumber, publicationDate, ADPublicationNumber
  - international: internationalApplicationNumber, internationalPublicationNumber, nationalPublicationNumber
  - priority: priorityRightInformation[] の中身（パリ vs 国内、件数）
  - parent: parentApplicationInformation（分割親出願の有無）
  - divisional: divisionalApplicationInformation（子出願の有無）
  - documentList: 経過書類の記録（legalDate, documentDescription）

出力:
  inventory/doc_history_inventory.tsv  ── 案件×フィールド有無
  inventory/inventory_report.md        ── 集計サマリ・代表的欠損パターン

CLI:
  python 02_inventory_doc_history.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
INPUTS_ROOT = REPO_ROOT / "inputs"
INV_DIR = HERE / "inventory"
TSV_PATH = INV_DIR / "doc_history_inventory.tsv"
REPORT_PATH = INV_DIR / "inventory_report.md"


def find_doc_histories() -> list[Path]:
    out: list[Path] = []
    for p in INPUTS_ROOT.rglob("doc_history.json"):
        # .doc_history/ 配下の重複は除外（ドット先頭は隠しバックアップ想定）
        if any(part.startswith(".") for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def case_id_from_path(p: Path) -> tuple[str, str]:
    """doc_history.json のパスから (case_folder, app_no_guess) を返す。"""
    # inputs/{CASE_DIR}/doc_history/doc_history.json
    case_dir = p.parent.parent
    folder_name = case_dir.name
    # 親がfortrain ならその下の {CASE_DIR}
    return folder_name, ""


def safe_get(d: dict, *keys, default=None):
    cur: object = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def analyze_one(p: Path) -> dict:
    raw = json.loads(p.read_text(encoding="utf-8"))
    data = safe_get(raw, "result", "data") or {}

    appno = data.get("applicationNumber", "")
    title = data.get("inventionTitle", "")
    filing_date = data.get("filingDate", "")
    pub_no = data.get("publicationNumber", "")
    ad_pub = data.get("ADPublicationNumber", "")
    pub_date = data.get("publicationDate", "")
    intl_appno = data.get("internationalApplicationNumber", "")
    intl_pub_no = data.get("internationalPublicationNumber", "")
    intl_pub_date = data.get("internationalPublicationDate", "")
    nat_pub_no = data.get("nationalPublicationNumber", "")

    # 優先権
    prios = data.get("priorityRightInformation", []) or []
    n_paris = sum(1 for x in prios if x.get("parisPriorityDate"))
    n_kokunai = sum(1 for x in prios if x.get("nationalPriorityDate"))
    paris_countries = sorted({x.get("parisPriorityCountryCd", "") for x in prios if x.get("parisPriorityDate")})

    # 分割関係
    parent_info = data.get("parentApplicationInformation", {}) or {}
    has_parent = bool(parent_info) and len(parent_info) > 0
    divs = data.get("divisionalApplicationInformation", []) or []
    n_divs = len(divs)

    # 文書リスト（複数 numberType がある場合があるので全部総合）
    biblio = data.get("bibliographyInformation", []) or []
    all_docs: list[dict] = []
    number_types: list[str] = []
    for b in biblio:
        nt = b.get("numberType", "")
        number_types.append(nt)
        for d in b.get("documentList", []) or []:
            all_docs.append({**d, "numberType": nt})

    # 重要書類の有無
    desc_set = {d.get("documentDescription", "") for d in all_docs}
    has_kyozetsu_riyu = any("拒絶理由通知書" in s for s in desc_set)
    has_kyozetsu_satei = any("拒絶査定" in s for s in desc_set)
    has_iken = any("意見書" in s for s in desc_set)
    has_hosei = any("手続補正書" in s for s in desc_set)
    has_shinpan = any("審判請求書" in s for s in desc_set)
    has_zenchi = any("前置報告書" in s for s in desc_set)
    has_joushin = any("上申書" in s for s in desc_set)
    has_translation = any("翻訳文" in s for s in desc_set)
    has_priority_cert = any("優先権証明書" in s for s in desc_set)

    # 案件種別の推定（doc_history.json レベル）
    pattern = "通常内国出願"
    if intl_appno or intl_pub_no:
        if n_paris > 0:
            pattern = "パリ条約優先＋ＰＣＴ"
        elif n_kokunai > 0:
            pattern = "国内優先＋日本語ＰＣＴ"
        else:
            pattern = "ＰＣＴ"
    elif has_parent:
        pattern = "分割（世代不明）"
    elif n_paris > 0:
        pattern = "パリ条約優先権"
    elif n_kokunai > 0:
        pattern = "国内優先権"

    return {
        "case_dir": p.parent.parent.name,
        "src_path": str(p.relative_to(REPO_ROOT)),
        "appno": appno,
        "title": title[:30],
        "filingDate": filing_date,
        "pubNumber": pub_no,
        "ADPubNumber": ad_pub,
        "pubDate": pub_date,
        "intlAppno": intl_appno,
        "intlPubNumber": intl_pub_no,
        "intlPubDate": intl_pub_date,
        "natPubNumber": nat_pub_no,
        "n_priorities": len(prios),
        "n_paris_priorities": n_paris,
        "n_kokunai_priorities": n_kokunai,
        "paris_countries": ",".join(paris_countries),
        "has_parent": has_parent,
        "n_divisionals_filed": n_divs,
        "n_documents_total": len(all_docs),
        "numberTypes": ",".join(number_types),
        "has_kyozetsu_riyu": has_kyozetsu_riyu,
        "has_kyozetsu_satei": has_kyozetsu_satei,
        "has_iken": has_iken,
        "has_hosei": has_hosei,
        "has_shinpan": has_shinpan,
        "has_zenchi_houkoku": has_zenchi,
        "has_joushin": has_joushin,
        "has_translation": has_translation,
        "has_priority_cert": has_priority_cert,
        "estimated_pattern": pattern,
    }


def write_tsv(records: list[dict]) -> None:
    INV_DIR.mkdir(exist_ok=True)
    if not records:
        return
    cols = list(records[0].keys())
    with TSV_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in records:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")


def write_report(records: list[dict]) -> None:
    n = len(records)
    if n == 0:
        REPORT_PATH.write_text("doc_history.json が見つかりませんでした。\n", encoding="utf-8")
        return

    pat_counts = Counter(r["estimated_pattern"] for r in records)
    countries = Counter()
    for r in records:
        for c in r["paris_countries"].split(","):
            if c:
                countries[c] += 1

    field_avail = {
        "filingDate":         sum(1 for r in records if r["filingDate"]),
        "publicationNumber":  sum(1 for r in records if r["pubNumber"]),
        "ADPublicationNumber":sum(1 for r in records if r["ADPubNumber"]),
        "publicationDate":    sum(1 for r in records if r["pubDate"]),
        "internationalApplicationNumber": sum(1 for r in records if r["intlAppno"]),
        "nationalPublicationNumber":      sum(1 for r in records if r["natPubNumber"]),
        "n_priorities>0":     sum(1 for r in records if r["n_priorities"] > 0),
        "has_parent":         sum(1 for r in records if r["has_parent"]),
        "n_divisionals_filed>0": sum(1 for r in records if r["n_divisionals_filed"] > 0),
    }
    doc_avail = {
        "拒絶理由通知書": sum(1 for r in records if r["has_kyozetsu_riyu"]),
        "拒絶査定":      sum(1 for r in records if r["has_kyozetsu_satei"]),
        "意見書":        sum(1 for r in records if r["has_iken"]),
        "手続補正書":    sum(1 for r in records if r["has_hosei"]),
        "審判請求書":    sum(1 for r in records if r["has_shinpan"]),
        "前置報告書":    sum(1 for r in records if r["has_zenchi_houkoku"]),
        "上申書":        sum(1 for r in records if r["has_joushin"]),
        "翻訳文":        sum(1 for r in records if r["has_translation"]),
        "優先権証明書":  sum(1 for r in records if r["has_priority_cert"]),
    }

    lines: list[str] = []
    lines.append(f"# doc_history.json 充足度集計\n")
    lines.append(f"対象: {n} 件\n")
    lines.append("")
    lines.append("## 案件種別の推定分布（doc_history からの推定）\n")
    for k, v in pat_counts.most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## 主要フィールドの取得可能件数\n")
    lines.append("| フィールド | 取得可能 / 総数 |")
    lines.append("|---|---|")
    for k, v in field_avail.items():
        lines.append(f"| {k} | {v} / {n} |")
    lines.append("")
    lines.append("## 経過書類の有無（documentList から）\n")
    lines.append("| 書類 | 含む案件数 / 総数 |")
    lines.append("|---|---|")
    for k, v in doc_avail.items():
        lines.append(f"| {k} | {v} / {n} |")
    lines.append("")
    lines.append("## パリ優先国分布\n")
    for c, v in countries.most_common():
        lines.append(f"- {c}: {v}")
    lines.append("")
    lines.append("## 案件別レコード（簡易）\n")
    for r in records:
        lines.append(f"### {r['case_dir']}")
        lines.append(f"- 出願番号: {r['appno']}　/　名称: {r['title']}")
        lines.append(f"- 出願日: {r['filingDate']}　/　公開: {r['pubNumber']}")
        lines.append(f"- 推定パターン: {r['estimated_pattern']}")
        if r["intlAppno"]:
            lines.append(f"- 国際出願番号: {r['intlAppno']}")
        if r["n_priorities"] > 0:
            lines.append(f"- 優先権: パリ {r['n_paris_priorities']} / 国内 {r['n_kokunai_priorities']}　国: {r['paris_countries']}")
        if r["has_parent"]:
            lines.append(f"- 親出願あり（分割）")
        if r["n_divisionals_filed"]:
            lines.append(f"- 子出願あり: {r['n_divisionals_filed']} 件")
        lines.append(f"- 文書: {r['n_documents_total']} 件")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    paths = find_doc_histories()
    print(f"discovered doc_history.json: {len(paths)}")
    records: list[dict] = []
    for p in paths:
        try:
            rec = analyze_one(p)
        except Exception as e:
            print(f"  ERROR {p}: {type(e).__name__}: {e}")
            continue
        records.append(rec)
        print(f"  OK {rec['case_dir']:35s} appno={rec['appno']} {rec['estimated_pattern']}")
    write_tsv(records)
    write_report(records)
    print(f"\nTSV: {TSV_PATH}")
    print(f"REPORT: {REPORT_PATH}")


if __name__ == "__main__":
    main()
