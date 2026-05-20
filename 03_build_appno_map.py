"""03_build_appno_map.py — 審判番号 → 出願番号 マップ構築

corpus 配下の各起案docx（または train_P1_history.txt）を再読込し、
冒頭付近に出てくる「特願２０●●－●●●●●●」を抽出する。

入力:
  corpus/_index.tsv  ─ 抽出済みコーパスのソースパス一覧

出力:
  inventory/case_appno_map.tsv
    columns: case_key, appno, src_path, status, note
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
sys.path.insert(0, str(REPO_ROOT))

from analyzer.parse_docx_structure import parse_docx_to_flat  # noqa: E402

CORPUS_INDEX = HERE / "corpus" / "_index.tsv"
INV_DIR = HERE / "inventory"
OUT_PATH = INV_DIR / "case_appno_map.tsv"

# 全角→半角変換用
ZEN_DIGIT = "０１２３４５６７８９"
ZEN_TO_HAN = str.maketrans(ZEN_DIGIT + "－", "0123456789-")

# 特願YYYY-NNNNN(N) を検出（leading zero 省略あり: 1〜6桁許容）
# ハイフン類は複数バリアント対応:
#   \-       ASCII hyphen-minus (U+002D)
#   －       full-width hyphen-minus (U+FF0D)
#   −       minus sign (U+2212) ← 一部の起案で使用
#   ‐ – —   hyphen / en-dash / em-dash
TOKUGAN_PATTERN = re.compile(r"特願([０-９\d]{4})[\-－−‐–—]([０-９\d]{1,6})")


def extract_appno_from_text(text: str) -> str:
    """テキスト中で最初に現れる『特願YYYY-NNNNN(N)』を返す（10桁正規化）。"""
    m = TOKUGAN_PATTERN.search(text)
    if not m:
        return ""
    year = m.group(1).translate(ZEN_TO_HAN)
    num_raw = m.group(2).translate(ZEN_TO_HAN)
    num_padded = num_raw.zfill(6)  # 6桁ゼロ詰め
    return f"{year}{num_padded}"


def text_from_docx_head(path: Path, max_paras: int = 30) -> str:
    """docx の先頭 N 段落を結合してテキスト返却。"""
    flat, _ = parse_docx_to_flat(path)
    return "\n".join(p["text"] for p in flat[:max_paras])


def text_from_txt_head(path: Path, max_chars: int = 4000) -> str:
    return path.read_text(encoding="utf-8")[:max_chars]


def main() -> None:
    if not CORPUS_INDEX.exists():
        print(f"corpus index not found: {CORPUS_INDEX}", file=sys.stderr)
        sys.exit(1)

    INV_DIR.mkdir(exist_ok=True)

    rows = CORPUS_INDEX.read_text(encoding="utf-8").splitlines()
    header = rows[0].split("\t")
    col = {name: i for i, name in enumerate(header)}

    records: list[dict] = []
    for line in rows[1:]:
        cols = line.split("\t")
        if cols[col["status"]] != "ok":
            continue
        case_key = cols[col["case_key"]]
        src_path = cols[col["src_path"]]
        full_path = REPO_ROOT / src_path

        rec = {"case_key": case_key, "appno": "", "src_path": src_path,
               "status": "", "note": ""}

        if not full_path.exists():
            rec["status"] = "missing"
            records.append(rec)
            continue

        try:
            if full_path.suffix.lower() == ".docx":
                text = text_from_docx_head(full_path)
            else:
                text = text_from_txt_head(full_path)
            appno = extract_appno_from_text(text)
            if appno:
                rec["appno"] = appno
                rec["status"] = "ok"
            else:
                rec["status"] = "no_match"
                rec["note"] = "特願pattern not found in head"
        except Exception as e:
            rec["status"] = "error"
            rec["note"] = f"{type(e).__name__}: {e}"
        records.append(rec)
        marker = {"ok": "OK", "no_match": "NM", "error": "ER", "missing": "MS"}.get(rec["status"], "??")
        print(f"  {marker} {case_key:15s} appno={rec['appno']:10s}  {Path(src_path).name[:55]}")

    cols_out = ["case_key", "appno", "src_path", "status", "note"]
    with OUT_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols_out) + "\n")
        for r in records:
            f.write("\t".join(str(r.get(c, "")) for c in cols_out) + "\n")

    from collections import Counter
    sc = Counter(r["status"] for r in records)
    print(f"\n=== summary ===")
    for k, v in sc.most_common():
        print(f"  {k:10s} {v}")
    print(f"out: {OUT_PATH}")


if __name__ == "__main__":
    main()
