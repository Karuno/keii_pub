"""91_generate_intro.py — 冒頭文だけ生成して corpus と diff

closed-loop 検証用。yaml ルールベースで冒頭文1行を生成し、
corpus/*.keii.txt の対応行と比較する。

CLI:
  python 91_generate_intro.py 2023-019613
  python 91_generate_intro.py 2024-004328
  python 91_generate_intro.py --all     # 既知の閉ループ案件全件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
sys.path.insert(0, str(HERE))

from generator.intro import generate_intro  # noqa: E402

CORPUS_DIR = HERE / "corpus"
INPUTS_DIR = REPO_ROOT / "inputs"

# 既知の閉ループ可能案件（doc_history.json + corpus 両方ある）
KNOWN_PAIRS = {
    "2023-019613": "inputs/fortrain/15_2023-019613",
    "2024-004328": "inputs/fortrain/19_2024-004328",
}


def find_doc_history(case_key: str) -> Path | None:
    if case_key in KNOWN_PAIRS:
        p = REPO_ROOT / KNOWN_PAIRS[case_key] / "doc_history" / "doc_history.json"
        if p.exists():
            return p
    # フォールバック: rglob
    for p in INPUTS_DIR.rglob("doc_history.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            appno = j.get("result", {}).get("data", {}).get("applicationNumber", "")
            # case_key と appno の対応を取りたいが、単純照合できないので case folder の名前で
            if case_key in str(p):
                return p
        except Exception:
            continue
    return None


def find_corpus_text(case_key: str) -> str | None:
    matches = list(CORPUS_DIR.glob(f"{case_key}__*.keii.txt"))
    if not matches:
        return None
    # 0_ALL の起案docx由来を優先（fortrain train_P1_history.txt は副）
    matches.sort(key=lambda p: ("train_P1_history" in p.name, p.name))
    return matches[0].read_text(encoding="utf-8")


def extract_corpus_intro(text: str) -> str:
    """corpus の本文から「本願は、…次のとおりである。」を含む行を抽出（前後空白は保持）。"""
    for line in text.splitlines():
        if "本願は、" in line and "次のとおりである" in line:
            return line  # 全角空白等のインデントを保持
    return ""


def diff_lines(generated: str, actual: str) -> list[str]:
    """文字単位 diff の簡易表示（先頭一致箇所まで・差異位置・以降）。"""
    if generated == actual:
        return ["EXACT MATCH"]
    # 共通プレフィックスを求める
    common = 0
    for a, b in zip(generated, actual):
        if a != b:
            break
        common += 1
    return [
        f"GENERATED ({len(generated)} chars): {generated}",
        f"ACTUAL    ({len(actual)} chars): {actual}",
        f"COMMON_PREFIX_LEN: {common}",
        f"GEN_AT_DIFF: '{generated[common:common+30]}'",
        f"ACT_AT_DIFF: '{actual[common:common+30]}'",
    ]


def process_one(case_key: str, log_lines: list[str]) -> None:
    log_lines.append(f"\n=== {case_key} ===")
    def p(s: str) -> None:
        log_lines.append(s)
        print(s)
    dh_path = find_doc_history(case_key)
    if not dh_path:
        p(f"  doc_history.json not found")
        return
    raw = json.loads(dh_path.read_text(encoding="utf-8"))
    data = raw.get("result", {}).get("data", {}) or {}

    result = generate_intro(data)
    p(f"  pattern: {result['pattern']}  (apptype={result['apptype']})")
    if result['missing_fields']:
        p(f"  missing: {result['missing_fields']}")
    p(f"  GENERATED: {result['intro_text']}")

    corpus = find_corpus_text(case_key)
    if not corpus:
        p(f"  corpus not found")
        return
    actual = extract_corpus_intro(corpus)
    p(f"  ACTUAL   : {actual}")
    p(f"  --- diff ---")
    for line in diff_lines(result['intro_text'], actual):
        p(f"  {line}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case_key", nargs="?")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        targets = sorted(KNOWN_PAIRS.keys())
    elif args.case_key:
        targets = [args.case_key]
    else:
        targets = sorted(KNOWN_PAIRS.keys())

    log_lines: list[str] = []
    for ck in targets:
        process_one(ck, log_lines)
    out_log = HERE / "inventory" / "intro_diff_log.txt"
    out_log.parent.mkdir(exist_ok=True)
    out_log.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"\n[saved] {out_log}")


if __name__ == "__main__":
    main()
