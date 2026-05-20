"""01_collect_corpus.py — 過去起案docxから「手続きの経緯」節を抽出してコーパス化

対象:
  - knowledge/examples/cases/0_ALL/        確定起案（拒絶査定不服審判のみ）
  - inputs/fortrain/                        トレーニング用既確定
  - inputs/{CASE_ID}/                       進行中（起案docxがある場合のみ）

除外:
  - 判定／異議関連の起案（拒絶査定不服審判ではないため）
  - 拡張子が .docx でないもの

抽出ロジック:
  1. tools/analyzer/parse_docx_structure.parse_docx() でツリー化
  2. find_sections_by_title(tree, /手続.?の経緯/) で対象節を抽出
  3. extract_section_text() で本文取得
  4. corpus/{case_no}__{src_label}.keii.txt に保存

出力:
  corpus/_index.tsv  (case_no \t src_path \t status \t n_sections \t char_count \t note)
  corpus/{case_no}__{src_label}.keii.txt
  corpus/_failed/{case_no}__{src_label}.tree.json   (失敗時にツリーを残す)

CLI:
  python 01_collect_corpus.py --limit N         # 先頭N件のみ（パイロット）
  python 01_collect_corpus.py --source 0_ALL    # 0_ALL のみ
  python 01_collect_corpus.py                   # 全件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

# プロジェクトルート: 01_collect_corpus.py から見て3階層上
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE  # claude_appeal/
sys.path.insert(0, str(REPO_ROOT))

from analyzer.parse_docx_structure import (  # noqa: E402
    parse_docx,
    find_sections_by_title,
    extract_section_text,
)

# -------- 設定 --------

CORPUS_DIR = HERE / "corpus"
FAILED_DIR = CORPUS_DIR / "_failed"
INDEX_PATH = CORPUS_DIR / "_index.tsv"

# 「手続の経緯」「手続きの経緯」の両方を捕捉
KEII_PATTERN = re.compile(r"手続.?の経緯")

# 母集団ディレクトリ
SOURCES = {
    "0_ALL": REPO_ROOT / "knowledge" / "examples" / "cases" / "0_ALL",
    "fortrain": REPO_ROOT / "inputs" / "fortrain",
    "inputs": REPO_ROOT / "inputs",
}

# 拒絶査定不服審判のみを対象（判定・異議は除外）
# 当審拒絶理由通知書は文書構造として「手続の経緯」節を持たないため除外。
# ※ EXCLUDE_NAME は filter_by_name() でファイル名 (p.name) のみに適用する。
#    審決本文中に「拒絶理由」の語が出ることは本フィルタには影響しない。
INCLUDE_NAME = re.compile(r"不服")
EXCLUDE_NAME = re.compile(r"判定|異議|拒絶理由|拒理|審尋")


# -------- ロジック --------


def make_case_key(name: str, *, parent_folder: str = "") -> str:
    """ファイル名 or 親フォルダ名から審判番号を抽出（重複排除キー）。

    優先順:
      1. ファイル名の「不服YYYY-NNNNNN」
      2. ファイル名の「YYYY-NNNNNN」
      3. 親フォルダ名の「YYYY-NNNNNN」（fortrain/inputs ケース用）
    """
    m = re.search(r"不服(\d{4}-\d{4,6})", name)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4}-\d{4,6})", name)
    if m:
        return m.group(1)
    if parent_folder:
        m = re.search(r"(\d{4}-\d{4,6})", parent_folder)
        if m:
            return m.group(1)
    return name[:40]


def make_src_label(path: Path) -> str:
    """出典ラベル（ファイル名sanitize、最大40字）。"""
    stem = path.stem
    # ファイル名先頭の特殊文字や空白を整理
    cleaned = re.sub(r"[\s☆＿_]+", "_", stem)
    cleaned = re.sub(r"[^\w\-一-龥ぁ-んァ-ヶ]", "", cleaned)
    return cleaned[:50]


def discover_docx_files(source_filter: str | None) -> list[tuple[str, Path]]:
    """全候補docxを返す。tuple(source_name, path)。

    収集ポリシー:
      - 0_ALL: 直下の全docx（後段でファイル名フィルタ適用）
      - fortrain: 各案件サブディレクトリの train_output/train_P1_history*.docx のみ
                  （他は研究資料・WIP・対訳など混在のためスコープ外）
      - inputs: 各案件サブディレクトリの train_output/train_P1_history*.docx のみ
                （アクティブ案件の WIP draft は別途検討）
    """
    out: list[tuple[str, Path]] = []
    for src_name, root in SOURCES.items():
        if source_filter and source_filter != src_name:
            continue
        if not root.exists():
            continue

        if src_name == "0_ALL":
            for p in sorted(root.glob("*.docx")):
                out.append((src_name, p))
        elif src_name == "fortrain":
            for case_dir in sorted(root.iterdir()):
                if not case_dir.is_dir():
                    continue
                train_dir = case_dir / "train_output"
                if train_dir.exists():
                    # docxがあれば優先、なければ.txt（同名）にフォールバック
                    for docx_p in sorted(train_dir.glob("train_P1_history*.docx")):
                        txt_p = docx_p.with_suffix(".txt")
                        out.append((src_name, txt_p if txt_p.exists() else docx_p))
        elif src_name == "inputs":
            for case_dir in sorted(root.iterdir()):
                if not case_dir.is_dir() or case_dir.name == "fortrain":
                    continue
                train_dir = case_dir / "train_output"
                if train_dir.exists():
                    for docx_p in sorted(train_dir.glob("train_P1_history*.docx")):
                        txt_p = docx_p.with_suffix(".txt")
                        out.append((src_name, txt_p if txt_p.exists() else docx_p))

    return out


def filter_by_name(items: list[tuple[str, Path]]) -> tuple[list[tuple[str, Path]], list[tuple[str, Path, str]]]:
    """ファイル名で対象/除外を分ける。返り値: (採用, 除外＋理由)。

    ※ EXCLUDE_NAME はファイル名 (p.name) のみに適用。本文内容には影響しない。
    ※ INCLUDE_NAME (不服マーカ) は 0_ALL のみに適用。
       fortrain/inputs は discover 段階で train_P1_history*.docx に絞っており、
       審判番号は親フォルダ名から取得する想定。
    """
    accepted: list[tuple[str, Path]] = []
    rejected: list[tuple[str, Path, str]] = []
    for src_name, p in items:
        name = p.name
        if name.startswith("~$"):
            rejected.append((src_name, p, "tempfile"))
            continue
        if EXCLUDE_NAME.search(name):
            rejected.append((src_name, p, "non_target_type"))
            continue
        if src_name == "0_ALL" and not INCLUDE_NAME.search(name):
            rejected.append((src_name, p, "no_fufuku_marker"))
            continue
        accepted.append((src_name, p))
    return accepted, rejected


def extract_keii_from_text(text: str) -> str | None:
    """プレーンテキストから「手続の経緯」セクションを抽出。

    検出ロジック:
      - 「手続の経緯」を含む行をヘッダ行として開始
      - 開始行以降を抽出。次のL1/L2見出し（第２／２　）で打ち切り
      - 何も見つからなければ None
    """
    lines = text.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if KEII_PATTERN.search(line):
            start = i
            break
    if start is None:
        return None

    # 終了位置を探索: 次の「第２」「第二」または行頭「２　」（L2）
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j]
        if re.match(r"^第[二2２]", s):
            end = j
            break
        if re.match(r"^[2２][　 ]", s):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def process_one(src_name: str, path: Path) -> dict:
    """1件処理。dict(status, ...) を返す。"""
    # fortrain/inputs では親(or祖父母)フォルダ名から審判番号を取得
    parent_names = [path.parent.name, path.parent.parent.name if path.parent.parent else ""]
    case_key = make_case_key(path.name, parent_folder=" ".join(parent_names))
    src_label = make_src_label(path)
    out_path = CORPUS_DIR / f"{case_key}__{src_label}.keii.txt"

    record: dict = {
        "src_name": src_name,
        "src_path": str(path.relative_to(REPO_ROOT)),
        "case_key": case_key,
        "src_label": src_label,
        "out_path": "",
        "status": "",
        "n_sections": 0,
        "char_count": 0,
        "note": "",
    }

    # .txt 入力分岐（fortrain の事前抽出済テキスト）
    if path.suffix.lower() == ".txt":
        try:
            text_all = path.read_text(encoding="utf-8")
        except Exception as e:
            record["status"] = "parse_error"
            record["note"] = f"{type(e).__name__}: {e}"
            return record
        section_text = extract_keii_from_text(text_all)
        if section_text is None:
            record["status"] = "no_section"
            return record
        record["char_count"] = len(section_text)
        if record["char_count"] < 30:
            record["status"] = "too_short"
            return record
        out_path.write_text(section_text, encoding="utf-8")
        record["out_path"] = str(out_path.relative_to(HERE))
        record["status"] = "ok"
        record["n_sections"] = 1
        record["note"] = "from_txt"
        return record

    try:
        parsed = parse_docx(path)
    except Exception as e:
        record["status"] = "parse_error"
        record["note"] = f"{type(e).__name__}: {e}"
        return record

    sections = find_sections_by_title(parsed["tree"], KEII_PATTERN)
    record["n_sections"] = len(sections)

    if not sections:
        record["status"] = "no_section"
        # ツリーをfailedに保存（後で目視確認用）
        FAILED_DIR.mkdir(exist_ok=True)
        tree_dump = FAILED_DIR / f"{case_key}__{src_label}.tree.json"
        try:
            tree_titles = [n["heading_text"] for n in parsed["tree"]]
            tree_dump.write_text(
                json.dumps(
                    {
                        "filename": parsed["filename"],
                        "case_no": parsed["case_no"],
                        "total_paragraphs": parsed["total_paragraphs"],
                        "top_level_titles": tree_titles,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        return record

    # 複数マッチ時は最も浅い（小さい level）レベルのものを優先
    sections.sort(key=lambda n: (n["level"], n["start_idx"]))
    main = sections[0]
    text = extract_section_text(main, include_children=True)
    record["char_count"] = len(text)

    if record["char_count"] < 30:
        record["status"] = "too_short"
        record["note"] = f"under 30 chars; matched section: {main.get('heading_text','')[:40]}"
        FAILED_DIR.mkdir(exist_ok=True)
        (FAILED_DIR / f"{case_key}__{src_label}.short.txt").write_text(text, encoding="utf-8")
        return record

    out_path.write_text(text, encoding="utf-8")
    record["out_path"] = str(out_path.relative_to(HERE))
    record["status"] = "ok"
    if len(sections) > 1:
        record["note"] = f"multi_matches={len(sections)}; chose level={main['level']} idx={main['start_idx']}"
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="0=全件、>0=先頭N件のみ")
    ap.add_argument("--source", choices=list(SOURCES.keys()), default=None)
    ap.add_argument("--clean", action="store_true", help="既存corpusを掃除してから実行")
    ap.add_argument("--dry-run", action="store_true", help="ファイル列挙のみ")
    args = ap.parse_args()

    CORPUS_DIR.mkdir(exist_ok=True)

    if args.clean:
        for p in CORPUS_DIR.glob("*.keii.txt"):
            p.unlink()
        if FAILED_DIR.exists():
            for p in FAILED_DIR.glob("*"):
                p.unlink()

    items = discover_docx_files(args.source)
    accepted, rejected = filter_by_name(items)

    print(f"discovered={len(items)} accepted={len(accepted)} rejected={len(rejected)}")
    if args.dry_run:
        for src_name, p in accepted[:20]:
            print(f"  ACC {src_name} {p.relative_to(REPO_ROOT)}")
        for src_name, p, reason in rejected[:20]:
            print(f"  REJ {reason:20s} {src_name} {p.relative_to(REPO_ROOT)}")
        return

    if args.limit > 0:
        accepted = accepted[: args.limit]

    records: list[dict] = []
    for i, (src_name, path) in enumerate(accepted, 1):
        try:
            rec = process_one(src_name, path)
        except Exception:
            rec = {
                "src_name": src_name,
                "src_path": str(path.relative_to(REPO_ROOT)),
                "case_key": make_case_key(path.name),
                "src_label": make_src_label(path),
                "out_path": "",
                "status": "exception",
                "n_sections": 0,
                "char_count": 0,
                "note": traceback.format_exc().splitlines()[-1][:200],
            }
        records.append(rec)
        marker = {"ok": "OK", "no_section": "NS", "too_short": "SH",
                  "parse_error": "PE", "exception": "EX"}.get(rec["status"], "??")
        print(f"  [{i:3d}/{len(accepted)}] {marker} {rec['case_key']:15s} {rec['char_count']:6d}c  {Path(rec['src_path']).name[:50]}")

    # _index.tsv 出力
    cols = ["status", "case_key", "src_name", "n_sections", "char_count", "src_label", "out_path", "src_path", "note"]
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in records:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    # サマリ
    from collections import Counter
    status_counts = Counter(r["status"] for r in records)
    print("\n=== summary ===")
    for k, v in status_counts.most_common():
        print(f"  {k:15s} {v:4d}")
    chars = [r["char_count"] for r in records if r["status"] == "ok"]
    if chars:
        chars_sorted = sorted(chars)
        print(f"  char_count ok: n={len(chars)} min={chars_sorted[0]} "
              f"median={chars_sorted[len(chars_sorted)//2]} max={chars_sorted[-1]}")
    print(f"\nindex: {INDEX_PATH}")
    print(f"failed dir: {FAILED_DIR}")


if __name__ == "__main__":
    main()
