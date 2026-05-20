"""13_align_drafting.py — corpus chrono行 vs API XML drafting-date で精度検証

12_align_corpus_history.py の後継。Type A は API XML の drafting-date を使い、
corpus の起案日表記と完全一致するか確認する。

入力:
  inventory/case_appno_map.tsv             case_key → appno
  inventory/doc_xmls/{appno}/_summary.json 各案件の書類リスト + drafting_date
  inputs/.../doc_history/doc_history.json  Type B/C の legalDate 用
  inventory/doc_history_collected/{appno}.json  同上 (代替)

出力:
  inventory/align_drafting.tsv             行×書類×差異
  inventory/align_drafting_summary.md      Type別 diff_days ヒストグラム

CLI:
  python 13_align_drafting.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date as _date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
INV_DIR = HERE / "inventory"
CORPUS_DIR = HERE / "corpus"
XMLS_DIR = INV_DIR / "doc_xmls"
COLLECTED_DIR = INV_DIR / "doc_history_collected"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"

sys.path.insert(0, str(HERE))

# === 元号→西暦変換 ===

ZEN_DIGIT_TR = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}


def parse_jp_date(date_text: str, prev: tuple[int, int, int] | None = None) -> tuple[int, int, int] | None:
    s = date_text.translate(ZEN_DIGIT_TR).replace("　", " ")
    s = re.sub(r"\s+", "", s)
    if "同日" in s and prev:
        return prev
    if s.startswith("同月") and prev:
        m = re.search(r"同月(\d{1,2})日", s)
        return (prev[0], prev[1], int(m.group(1))) if m else None
    if s.startswith("同年") and prev:
        m = re.search(r"同年(\d{1,2})月(\d{1,2})日", s)
        return (prev[0], int(m.group(1)), int(m.group(2))) if m else None
    for era, base in ERA_BASE.items():
        if s.startswith(era):
            tail = s[len(era):].replace("元", "1")
            m = re.search(r"(\d{1,2})年(\d{1,2})月(\d{1,2})日", tail)
            if m:
                return (base + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def diff_days(corpus_ymd: tuple[int, int, int], yyyymmdd: str) -> int | None:
    try:
        c = _date(*corpus_ymd)
        h = _date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
        return (h - c).days
    except Exception:
        return None


# === corpus chrono 抽出（12_align_corpus_history.py と共通ロジック） ===

def parse_corpus_chrono(text: str) -> list[dict]:
    out: list[dict] = []
    prev: tuple[int, int, int] | None = None
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = re.search(r"(?:[（(])?[\s　]*"
                      r"((?:令和|平成|昭和|大正)[\s　]*[\d０-９元]{1,2}年[\s　]*[\d０-９]{1,2}月[\s　]*[\d０-９]{1,2}日"
                      r"|同年[\s　]*[\d０-９]{1,2}月[\s　]*[\d０-９]{1,2}日"
                      r"|同月[\s　]*[\d０-９]{1,2}日"
                      r"|同日)"
                      r".*?[:：]", line)
        if not m:
            continue
        ymd = parse_jp_date(m.group(1), prev)
        if not ymd:
            continue
        prev = ymd
        # 書類名: ：以降。括弧内付記は除去
        doc_part = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
        doc_name = re.sub(r"（以下[「『][^」』]*?[」』][^）)]*?）", "", doc_part).strip()
        doc_name = re.sub(r"[）)]\s*$", "", doc_name).strip()
        is_paren = line.strip().startswith("（") or line.strip().startswith("(")
        is_tsuke = "付け" in m.group(0)
        out.append({"lineno": i, "date": ymd, "doc_name": doc_name,
                    "is_paren": is_paren, "is_tsuke": is_tsuke, "raw": line})
    return out


# === appno マップ ===

def load_appno_map() -> dict[str, str]:
    out: dict[str, str] = {}
    if not APPNO_MAP.exists():
        return out
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 4 and cols[3] == "ok" and cols[1]:
            out.setdefault(cols[0], cols[1])
    return out


def load_doc_history(appno: str) -> dict | None:
    p = COLLECTED_DIR / f"{appno}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    # inputs/ 配下も走査
    for q in (REPO_ROOT / "inputs").rglob("doc_history.json"):
        if any(part.startswith(".") for part in q.parts):
            continue
        try:
            j = json.loads(q.read_text(encoding="utf-8"))
            if j.get("result", {}).get("data", {}).get("applicationNumber") == appno:
                return j
        except Exception:
            continue
    return None


def all_doc_history_documents(raw: dict) -> list[dict]:
    biblio = raw.get("result", {}).get("data", {}).get("bibliographyInformation", []) or []
    out: list[dict] = []
    for b in biblio:
        for d in b.get("documentList", []) or []:
            out.append(d)
    return out


def load_xml_summary(appno: str) -> list[dict]:
    p = XMLS_DIR / appno / "_summary.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("documents", [])


# === 書類名マッチング ===

DOC_KEYS = [
    ("拒絶理由通知書", "拒絶理由通知書"),
    ("拒絶査定",     "拒絶査定"),
    ("補正の却下",   "補正却下決定"),
    ("補正の却下の決定", "補正却下決定"),
    ("補正却下",     "補正却下決定"),
    ("意見書",       "意見書"),
    ("手続補正書",   "手続補正書"),
    ("審判請求書",   "審判請求書"),
    ("前置報告書",   "前置報告書"),
    ("上申書",       "上申書"),
    ("応対記録",     "応対記録"),
    ("面接",         "応対記録"),
    ("翻訳文",       "翻訳文"),
    ("国内書面",     "国内書面"),
    ("謄本の送達",   "_TYPE_C_SOUTATSU"),
    ("誤訳訂正",     "誤訳訂正"),
]
TYPE_A_KEYS = {"拒絶理由通知書", "拒絶査定", "補正却下決定", "前置報告書", "特許査定"}
TYPE_C_KEYS = {"_TYPE_C_SOUTATSU"}


def doc_key(name: str) -> str:
    for needle, key in DOC_KEYS:
        if needle in name:
            return key
    return ""


def split_doc_names(name: str) -> list[str]:
    cleaned = re.sub(r"の提出$", "", name).strip()
    parts = re.split(r"[、，]|及び|並びに", cleaned)
    return [p.strip() for p in parts if p.strip()]


# === マッチング ===

def find_xml_doc(xml_docs: list[dict], target_key: str, ymd: tuple[int, int, int],
                 used: set[int]) -> dict | None:
    """XML サマリから target_key にマッチする書類で日付が最近接のものを返す。"""
    best: dict | None = None
    best_abs: int | None = None
    for d in xml_docs:
        if id(d) in used:
            continue
        name = d.get("document_name", "")
        if target_key == "拒絶理由通知書" and "拒絶理由通知書" not in name:
            continue
        if target_key == "拒絶査定" and "拒絶査定" not in name:
            continue
        if target_key == "補正却下決定" and "補正" not in name:
            continue
        if target_key == "前置報告書" and "前置報告書" not in name:
            continue
        if target_key == "特許査定" and "特許査定" not in name:
            continue
        dd = d.get("drafting_date", "")
        if not (dd and len(dd) == 8):
            continue
        days = diff_days(ymd, dd)
        if days is None:
            continue
        ad = abs(days)
        if best_abs is None or ad < best_abs:
            best_abs = ad
            best = d
    return best


def find_history_doc(docs: list[dict], target_key: str, ymd: tuple[int, int, int],
                     used: set[int]) -> dict | None:
    """doc_history.json から Type B 用に legalDate でマッチ。"""
    rules = {
        "意見書":      lambda desc: desc == "意見書",
        "手続補正書":  lambda desc: "手続補正書" in desc and "（方式）" not in desc,
        "審判請求書":  lambda desc: desc == "審判請求書",
        "上申書":      lambda desc: "上申書" in desc,
        "応対記録":    lambda desc: "応対" in desc or "面接" in desc,
        "翻訳文":      lambda desc: "翻訳" in desc,
        "国内書面":    lambda desc: "国内書面" in desc,
        "誤訳訂正":    lambda desc: "誤訳" in desc,
        "_TYPE_C_SOUTATSU": lambda desc: "拒絶査定" in desc,
    }
    pred = rules.get(target_key)
    if not pred:
        return None
    best: dict | None = None
    best_abs: int | None = None
    for d in docs:
        if id(d) in used:
            continue
        if not pred(d.get("documentDescription", "")):
            continue
        ld = d.get("legalDate", "")
        if not (ld and len(ld) == 8):
            continue
        days = diff_days(ymd, ld)
        if days is None:
            continue
        ad = abs(days)
        if best_abs is None or ad < best_abs:
            best_abs = ad
            best = d
    return best


# === メイン ===

def main() -> None:
    INV_DIR.mkdir(exist_ok=True)
    appno_map = load_appno_map()

    rows: list[list] = []
    type_a_diffs_by_key: dict[str, list[int]] = defaultdict(list)
    type_b_diffs_by_key: dict[str, list[int]] = defaultdict(list)
    type_a_perfect: int = 0
    type_a_total: int = 0
    type_b_perfect: int = 0
    type_b_total: int = 0

    for case_key in sorted(appno_map.keys()):
        appno = appno_map[case_key]
        xml_docs = load_xml_summary(appno)
        history_raw = load_doc_history(appno) or {}
        history_docs = all_doc_history_documents(history_raw)

        corpus_files = list(CORPUS_DIR.glob(f"{case_key}__*.keii.txt"))
        if not corpus_files:
            continue
        corpus_files.sort(key=lambda p: ("train_P1_history" in p.name, p.name))
        text = corpus_files[0].read_text(encoding="utf-8")
        chrono = parse_corpus_chrono(text)

        used_xml: set[int] = set()
        used_history: set[int] = set()

        for c in chrono:
            ymd = c["date"]
            ymd_str = f"{ymd[0]:04d}-{ymd[1]:02d}-{ymd[2]:02d}"
            for sub in (split_doc_names(c["doc_name"]) or [c["doc_name"]]):
                key = doc_key(sub)
                if not key:
                    rows.append([case_key, c["lineno"], "?", ymd_str, sub[:30],
                                 "", "", "", "", "no_key"])
                    continue

                if key in TYPE_A_KEYS:
                    xml_match = find_xml_doc(xml_docs, key, ymd, used_xml)
                    if xml_match:
                        used_xml.add(id(xml_match))
                        dd = xml_match.get("drafting_date", "")
                        d_days = diff_days(ymd, dd) if dd else None
                        rows.append([case_key, c["lineno"], "A", ymd_str, sub[:30],
                                     key, xml_match.get("document_name", ""), dd,
                                     "" if d_days is None else str(d_days), "xml"])
                        type_a_total += 1
                        if d_days == 0:
                            type_a_perfect += 1
                        if d_days is not None:
                            type_a_diffs_by_key[key].append(d_days)
                    else:
                        rows.append([case_key, c["lineno"], "A", ymd_str, sub[:30],
                                     key, "", "", "", "xml_miss"])
                        type_a_total += 1

                elif key in TYPE_C_KEYS:
                    # 送達日 = A02 の legalDate を引く
                    h_match = find_history_doc(history_docs, key, ymd, used_history)
                    if h_match:
                        used_history.add(id(h_match))
                        ld = h_match.get("legalDate", "")
                        d_days = diff_days(ymd, ld) if ld else None
                        rows.append([case_key, c["lineno"], "C", ymd_str, sub[:30],
                                     key, h_match.get("documentDescription", ""), ld,
                                     "" if d_days is None else str(d_days), "history_legalDate"])
                    else:
                        rows.append([case_key, c["lineno"], "C", ymd_str, sub[:30],
                                     key, "", "", "", "history_miss"])

                else:
                    # Type B
                    h_match = find_history_doc(history_docs, key, ymd, used_history)
                    if h_match:
                        used_history.add(id(h_match))
                        ld = h_match.get("legalDate", "")
                        d_days = diff_days(ymd, ld) if ld else None
                        rows.append([case_key, c["lineno"], "B", ymd_str, sub[:30],
                                     key, h_match.get("documentDescription", ""), ld,
                                     "" if d_days is None else str(d_days), "history_legalDate"])
                        type_b_total += 1
                        if d_days == 0:
                            type_b_perfect += 1
                        if d_days is not None:
                            type_b_diffs_by_key[key].append(d_days)
                    else:
                        rows.append([case_key, c["lineno"], "B", ymd_str, sub[:30],
                                     key, "", "", "", "history_miss"])
                        type_b_total += 1

    # 出力 1: TSV
    out_tsv = INV_DIR / "align_drafting.tsv"
    with out_tsv.open("w", encoding="utf-8") as f:
        f.write("case_key\tlineno\ttype\tcorpus_date\tcorpus_doc\tkey\tmatched_name\tmatched_date\tdiff_days\tsource\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")

    # 出力 2: summary md
    lines: list[str] = []
    lines.append("# corpus ⇔ API XML drafting-date 対応分析\n")
    lines.append(f"対象行: {len(rows)}\n")
    lines.append(f"## Type A 一致率 (drafting-date)\n")
    lines.append(f"perfect (diff=0): {type_a_perfect}/{type_a_total} = {type_a_perfect/max(type_a_total,1):.1%}\n")
    for key, diffs in type_a_diffs_by_key.items():
        cnt = Counter(diffs)
        ds = sorted(diffs)
        lines.append(f"### {key} ({len(diffs)} 件)\n")
        lines.append("| diff_days | 件数 |\n|---:|---:|")
        for k, v in sorted(cnt.items()):
            lines.append(f"| {k:+d} | {v} |")
        lines.append(f"\n中央値={ds[len(ds)//2]:+d} / 最小={ds[0]:+d} / 最大={ds[-1]:+d}\n")

    lines.append("\n## Type B 一致率 (legalDate)\n")
    lines.append(f"perfect (diff=0): {type_b_perfect}/{type_b_total} = {type_b_perfect/max(type_b_total,1):.1%}\n")
    for key, diffs in type_b_diffs_by_key.items():
        cnt = Counter(diffs)
        ds = sorted(diffs)
        lines.append(f"### {key} ({len(diffs)} 件)\n")
        lines.append("| diff_days | 件数 |\n|---:|---:|")
        for k, v in sorted(cnt.items()):
            lines.append(f"| {k:+d} | {v} |")
        lines.append(f"\n中央値={ds[len(ds)//2]:+d} / 最小={ds[0]:+d} / 最大={ds[-1]:+d}\n")

    out_md = INV_DIR / "align_drafting_summary.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"rows: {len(rows)}")
    print(f"Type A perfect: {type_a_perfect}/{type_a_total}")
    print(f"Type B perfect: {type_b_perfect}/{type_b_total}")
    print(f"out: {out_tsv}")
    print(f"out: {out_md}")


if __name__ == "__main__":
    main()
