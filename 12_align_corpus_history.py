"""12_align_corpus_history.py — コーパス chrono行と doc_history の対応付け

各案件について:
  1. corpus/{case}__*.keii.txt から CHRONO_A/B/C 行を抽出
  2. 対応する doc_history.json から documentList を抽出
  3. 行ごとの (日付, 書類種別) と documentList の (legalDate, documentDescription) を照合
  4. ズレ／欠落／余剰 を一覧化

出力:
  inventory/align_per_case.tsv     ─ 案件×行×照合結果
  inventory/align_summary.md       ─ Type A の日付ズレ / 欠落／余剰 集計

CLI:
  python 12_align_corpus_history.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date as _date
from pathlib import Path


def _date_diff_days(corpus_ymd: tuple[int, int, int], history_yyyymmdd: str) -> int | None:
    try:
        c = _date(*corpus_ymd)
        h = _date(int(history_yyyymmdd[:4]), int(history_yyyymmdd[4:6]), int(history_yyyymmdd[6:8]))
        return (h - c).days
    except Exception:
        return None

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
INV_DIR = HERE / "inventory"
CORPUS_DIR = HERE / "corpus"
COLLECTED_DIR = INV_DIR / "doc_history_collected"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"

sys.path.insert(0, str(HERE))
from generator.jp_dates import _to_zen  # noqa: E402

# === 元号→西暦変換 ===

ZEN_DIGIT_TR = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}


def parse_jp_date(date_text: str, prev: tuple[int, int, int] | None = None) -> tuple[int, int, int] | None:
    """『令和４年　８月　１日』『同月　２３日』等を (yyyy, mm, dd) に変換。"""
    s = date_text.translate(ZEN_DIGIT_TR).replace("　", " ")
    s = re.sub(r"\s+", "", s)

    # 「同年」「同月」「同日」
    if "同日" in s and prev:
        return prev
    if s.startswith("同月") and prev:
        m = re.search(r"同月(\d{1,2})日", s)
        if m:
            return (prev[0], prev[1], int(m.group(1)))
        return None
    if s.startswith("同年") and prev:
        m = re.search(r"同年(\d{1,2})月(\d{1,2})日", s)
        if m:
            return (prev[0], int(m.group(1)), int(m.group(2)))
        return None

    # 元号
    for era, base in ERA_BASE.items():
        if s.startswith(era):
            tail = s[len(era):]
            tail = tail.replace("元", "1")
            m = re.search(r"(\d{1,2})年(\d{1,2})月(\d{1,2})日", tail)
            if m:
                y = base + int(m.group(1))
                return (y, int(m.group(2)), int(m.group(3)))

    # 西暦
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    return None


# === 書類名の抽出（chrono 行の：以降） ===

def extract_doc_name(line: str) -> str:
    m = re.search(r"[:：](.+)$", line)
    if not m:
        return ""
    name = m.group(1).strip()
    # 末尾の「（以下「原査定」という。）」「）」を除去
    name = re.sub(r"（以下[「『][^」』]*?[」』][^）)]*?）", "", name)
    name = re.sub(r"[）)]\s*$", "", name)
    return name.strip()


def parse_corpus_chrono(text: str) -> list[dict]:
    """keii.txt から chrono 行を時系列で抽出。前の日付を参照して 同年/同月/同日 を解決。"""
    out: list[dict] = []
    prev: tuple[int, int, int] | None = None
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        # 「（同月●日　　：原査定の謄本の送達）」 など括弧書きも対象
        # 日付部分を抽出：「●●年●月●日」または「同年●月●日」「同月●日」「同日」
        m = re.search(r"(?:[（(])?[\s　]*"
                      r"((?:令和|平成|昭和|大正)[\s　]*[\d０-９元]{1,2}年[\s　]*[\d０-９]{1,2}月[\s　]*[\d０-９]{1,2}日"
                      r"|同年[\s　]*[\d０-９]{1,2}月[\s　]*[\d０-９]{1,2}日"
                      r"|同月[\s　]*[\d０-９]{1,2}日"
                      r"|同日)"
                      r".*?[:：]", line)
        if not m:
            continue
        date_text = m.group(1)
        ymd = parse_jp_date(date_text, prev)
        if ymd:
            prev = ymd
        else:
            continue
        doc_name = extract_doc_name(line)
        is_paren = line.strip().startswith("（") or line.strip().startswith("(")
        is_tsuke = "付け" in m.group(0)
        out.append({
            "lineno": i,
            "date": ymd,
            "doc_name": doc_name,
            "is_paren": is_paren,
            "is_tsuke": is_tsuke,
            "raw": line,
        })
    return out


# === doc_history からの抽出 ===

def load_doc_history(case_dir: str | None, appno: str | None) -> dict | None:
    """指定 case_dir または appno から doc_history を読み込む。"""
    if case_dir:
        p = REPO_ROOT / "inputs" / case_dir / "doc_history" / "doc_history.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    if appno:
        p = COLLECTED_DIR / f"{appno}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


# === 案件 → (case_dir, appno) のマップ構築 ===

def load_appno_map() -> dict[str, str]:
    out: dict[str, str] = {}
    if not APPNO_MAP.exists():
        return out
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        case_key, appno, _src, status = cols[0], cols[1], cols[2], cols[3]
        if status == "ok" and appno and case_key not in out:
            out[case_key] = appno
    return out


def find_case_dir(case_key: str) -> str | None:
    """inputs 直下で {case_key} を含むケースフォルダ名を返す。"""
    inputs = REPO_ROOT / "inputs"
    for p in inputs.iterdir():
        if not p.is_dir() or p.name == "fortrain":
            continue
        if case_key in p.name:
            return p.name
    fortrain = inputs / "fortrain"
    if fortrain.exists():
        for p in fortrain.iterdir():
            if p.is_dir() and case_key in p.name:
                return f"fortrain/{p.name}"
    return None


# === 書類名のマッチング ===

# corpus 表記 → doc_history documentDescription のキーワード
DOC_NAME_KEYS = [
    ("拒絶理由通知書", "拒絶理由通知書"),
    ("拒絶査定", "拒絶査定"),
    ("補正の却下", "補正却下決定"),
    ("意見書", "意見書"),
    ("手続補正書", "手続補正書"),
    ("審判請求書", "審判請求書"),
    ("前置報告書", "前置報告書"),
    ("上申書", "上申書"),
    ("応対記録", "応対記録"),
    ("面接", "応対記録"),
    ("翻訳文", "翻訳文"),
    ("国内書面", "国内書面"),
    ("謄本の送達", "_TYPE_C_SOUTATSU"),  # 特殊: 拒絶査定の legalDate を使う
    ("誤訳訂正", "誤訳訂正"),
]


def match_doc_name(corpus_name: str) -> str:
    for needle, key in DOC_NAME_KEYS:
        if needle in corpus_name:
            return key
    return ""


# === doc_history.json から documentList 全部 ===

def all_documents(raw: dict) -> list[dict]:
    data = raw.get("result", {}).get("data", {}) or {}
    biblio = data.get("bibliographyInformation", []) or []
    out: list[dict] = []
    for b in biblio:
        for d in b.get("documentList", []) or []:
            out.append(d)
    return out


def _matches_target(target_key: str, desc: str) -> bool:
    if target_key == "拒絶理由通知書": return "拒絶理由通知書" in desc
    if target_key == "拒絶査定":     return "拒絶査定" in desc
    if target_key == "補正却下決定":  return "補正" in desc and "却下" in desc
    if target_key == "意見書":       return desc == "意見書"
    if target_key == "手続補正書":   return "手続補正書" in desc and "（方式）" not in desc
    if target_key == "審判請求書":   return desc == "審判請求書"
    if target_key == "前置報告書":   return "前置報告書" in desc
    if target_key == "上申書":       return "上申書" in desc
    if target_key == "応対記録":     return "応対" in desc or "面接" in desc
    if target_key == "翻訳文":       return "翻訳" in desc
    if target_key == "国内書面":     return "国内書面" in desc
    if target_key == "誤訳訂正":     return "誤訳" in desc
    if target_key == "_TYPE_C_SOUTATSU": return "拒絶査定" in desc
    return False


def doc_history_match(
    docs: list[dict], target_key: str, ymd: tuple[int, int, int],
    used: set[int]
) -> dict | None:
    """target_key と date に最も近い、まだ使われていない documentList エントリを返す。"""
    best: dict | None = None
    best_abs: int = 10**9
    for d in docs:
        if id(d) in used:
            continue
        if not _matches_target(target_key, d.get("documentDescription", "")):
            continue
        legal_date = d.get("legalDate", "")
        if not (legal_date and len(legal_date) == 8):
            continue
        days = _date_diff_days(ymd, legal_date)
        if days is None:
            continue
        ad = abs(days)
        if ad < best_abs:
            best_abs = ad
            best = d
    return best


def split_corpus_doc_names(name: str) -> list[str]:
    """corpus の書類名行から複数書類を分離（同日結合の「、」「及び」「並びに」で分割）。"""
    # 末尾の「の提出」を取り除いてから分割
    cleaned = re.sub(r"の提出$", "", name).strip()
    parts = re.split(r"[、，]|及び|並びに", cleaned)
    return [p.strip() for p in parts if p.strip()]


# === メイン ===

def main() -> None:
    INV_DIR.mkdir(exist_ok=True)
    appno_map = load_appno_map()
    cases = sorted(appno_map.keys())
    print(f"cases with appno: {len(cases)}")

    rows: list[list] = []
    type_a_diffs: list[tuple[str, str, int, int, int]] = []  # case, doc_type, corpus_date, history_date, diff
    not_in_history: list[tuple[str, dict]] = []
    not_in_corpus: list[tuple[str, dict]] = []

    for case_key in cases:
        appno = appno_map.get(case_key)
        case_dir = find_case_dir(case_key)
        raw = load_doc_history(case_dir, appno)
        if not raw:
            continue
        docs = all_documents(raw)

        # corpus
        corpus_files = list(CORPUS_DIR.glob(f"{case_key}__*.keii.txt"))
        if not corpus_files:
            continue
        # 0_ALL の起案docx由来を優先
        corpus_files.sort(key=lambda p: ("train_P1_history" in p.name, p.name))
        text = corpus_files[0].read_text(encoding="utf-8")
        chrono = parse_corpus_chrono(text)

        used_history: set[int] = set()

        for c in chrono:
            ymd = c["date"]
            ymd_str = f"{ymd[0]:04d}-{ymd[1]:02d}-{ymd[2]:02d}"
            sub_names = split_corpus_doc_names(c["doc_name"]) or [c["doc_name"]]
            for sub in sub_names:
                target_key = match_doc_name(sub)
                history_doc = None
                history_date = ""
                history_desc = ""
                diff_days_val = ""
                if target_key:
                    history_doc = doc_history_match(docs, target_key, ymd, used_history)
                    if history_doc:
                        used_history.add(id(history_doc))
                        history_date = history_doc.get("legalDate", "")
                        history_desc = history_doc.get("documentDescription", "")
                        d_int = _date_diff_days(ymd, history_date) if history_date else None
                        diff_days_val = "" if d_int is None else str(d_int)

                rows.append([
                    case_key, c["lineno"],
                    "C" if c["is_paren"] else ("A" if c["is_tsuke"] else "B"),
                    ymd_str, sub[:40],
                    target_key, history_desc[:30], history_date, diff_days_val,
                ])

                # Type A 系統ズレ
                if not c["is_paren"] and c["is_tsuke"] and history_date and diff_days_val != "":
                    type_a_diffs.append((case_key, target_key, ymd_str, history_date, int(diff_days_val)))

                if target_key and not history_doc:
                    not_in_history.append((case_key, {"doc_name": sub, "date": ymd_str, "lineno": c["lineno"]}))

        # doc_history で未マッチのもの（起案で省略）
        for d in docs:
            if id(d) in used_history:
                continue
            desc = d.get("documentDescription", "")
            # allow-list に含まれる種別のみ
            for needle, _ in DOC_NAME_KEYS:
                if needle in desc:
                    not_in_corpus.append((case_key, {"desc": desc, "legalDate": d.get("legalDate", "")}))
                    break

    # === 出力 1: per_case TSV ===
    out_tsv = INV_DIR / "align_per_case.tsv"
    with out_tsv.open("w", encoding="utf-8") as f:
        f.write("case_key\tlineno\ttype\tcorpus_date\tcorpus_doc\tmatched_key\thistory_desc\thistory_legalDate\tdiff_days\n")
        for r in rows:
            f.write("\t".join(str(c) for c in r) + "\n")

    # === 出力 2: summary md ===
    summary: list[str] = []
    summary.append(f"# corpus ⇔ doc_history 対応分析\n")
    summary.append(f"対象: {len(cases)} 件 / 行: {len(rows)}\n")

    # Type A の系統的ズレ
    summary.append("## Type A 起案日 vs legalDate のズレ（系統的差異の確認・本物の日数差）\n")
    if type_a_diffs:
        # 書類タイプ別ヒストグラム
        per_key = defaultdict(list)
        for case, key, cd, hd, diff in type_a_diffs:
            per_key[key].append(diff)
        for key, diffs in per_key.items():
            cnt = Counter(diffs)
            summary.append(f"### {key} ({len(diffs)} 件)\n")
            summary.append("| diff_days | 件数 |\n|---:|---:|")
            for k, v in sorted(cnt.items()):
                summary.append(f"| {k:+d} | {v} |")
            ds = sorted(diffs)
            summary.append(f"\n中央値={ds[len(ds)//2]:+d} / 最小={ds[0]:+d} / 最大={ds[-1]:+d}\n")

        big = sorted(type_a_diffs, key=lambda t: abs(t[4]), reverse=True)[:10]
        summary.append("### 差異が大きい 10 件（マッチング誤りの疑い）\n")
        for case, key, cd, hd, diff in big:
            summary.append(f"- {case} {key}: corpus={cd} history={hd} diff={diff:+d}")
        summary.append("")
    else:
        summary.append("（マッチした Type A データなし）\n")

    # corpus にあるが history にない
    summary.append("## corpus にあるが doc_history にない (lookup失敗)\n")
    cnt_not_in_hist = Counter((it[1]["doc_name"][:20]) for it in not_in_history)
    for k, v in cnt_not_in_hist.most_common(20):
        summary.append(f"- `{k}`: {v} 件")
    summary.append("")

    # history にあるが corpus に出ていない
    summary.append("## doc_history に存在するが corpus に出ていない（起案で省略 or allow-list外）\n")
    cnt_not_in_corp = Counter(it[1]["desc"] for it in not_in_corpus)
    for k, v in cnt_not_in_corp.most_common(20):
        summary.append(f"- `{k}`: {v} 件")
    summary.append("")

    out_md = INV_DIR / "align_summary.md"
    out_md.write_text("\n".join(summary), encoding="utf-8")

    print(f"\nrows: {len(rows)}")
    print(f"out: {out_tsv}")
    print(f"out: {out_md}")


if __name__ == "__main__":
    main()
