"""92_batch_generate.py — 44件分の手続の経緯生成 + corpus 差分集計

各案件について:
  1. generator.keii.generate() で本文生成
  2. corpus の対応 .keii.txt を読込
  3. 正規化（先頭全角空白除去等）して文字単位 diff 計算
  4. 結果を inventory/batch_generate/ に保存

CLI:
  python 92_batch_generate.py
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
sys.path.insert(0, str(REPO_ROOT))

from generator.keii import generate  # noqa: E402
from tools.keii_normalize import normalize_for_compare, normalize_pair  # noqa: E402

INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
CORPUS_DIR = HERE / "corpus"
OUT_DIR = INV_DIR / "batch_generate"


def normalize(text: str) -> str:
    """正規化処理は tools/keii_normalize.py に統合済 (A+B+C4+C5 吸収)."""
    return normalize_for_compare(text)


def load_appno_map() -> dict[str, str]:
    out: dict[str, str] = {}
    if not APPNO_MAP.exists():
        return out
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 4 and cols[3] == "ok" and cols[1]:
            out.setdefault(cols[0], cols[1])
    return out


def find_corpus_text(case_key: str) -> str | None:
    matches = list(CORPUS_DIR.glob(f"{case_key}__*.keii.txt"))
    if not matches:
        return None
    matches.sort(key=lambda p: ("train_P1_history" in p.name, p.name))
    return matches[0].read_text(encoding="utf-8")


def diff_stats(generated: str, actual: str) -> dict:
    """文字単位 diff を計算。新指標 match (Y/N) と従来 ratio を両方返す."""
    n_gen = len(generated)
    n_act = len(actual)
    common = 0
    for a, b in zip(generated, actual):
        if a == b:
            common += 1
        else:
            break
    matcher = difflib.SequenceMatcher(None, generated, actual, autojunk=False)
    ratio = matcher.ratio()
    return {
        "len_gen": n_gen,
        "len_act": n_act,
        "common_prefix": common,
        "ratio": round(ratio, 4),
        "match": "Y" if generated == actual else "N",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cases", nargs="*", help="指定 case_key のみ")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    appno_map = load_appno_map()
    if args.cases:
        targets = [(c, appno_map[c]) for c in args.cases if c in appno_map]
    else:
        targets = sorted(appno_map.items())
        if args.limit > 0:
            targets = targets[: args.limit]

    print(f"targets: {len(targets)}")
    rows: list[dict] = []
    for case_key, appno in targets:
        try:
            res = generate(appno)
        except Exception as e:
            rows.append({"case_key": case_key, "appno": appno,
                         "pattern": "(error)", "ratio": 0.0, "len_gen": 0, "len_act": 0,
                         "error": f"{type(e).__name__}: {e}"})
            print(f"  {case_key:15s} ERROR: {e}")
            continue

        gen_text = res.text
        gen_path = OUT_DIR / f"{case_key}.gen.txt"
        gen_path.write_text(gen_text, encoding="utf-8")

        corpus_text = find_corpus_text(case_key)
        if not corpus_text:
            rows.append({"case_key": case_key, "appno": appno,
                         "pattern": res.pattern, "ratio": 0.0, "len_gen": len(gen_text), "len_act": 0,
                         "error": "corpus not found"})
            print(f"  {case_key:15s} corpus not found")
            continue

        # 正規化して比較 (非対称: 公報側 act 基準で送達日・当審拒理の片側削除を判定)
        gen_norm, act_norm = normalize_pair(gen_text, corpus_text)
        stats = diff_stats(gen_norm, act_norm)

        # diff レポート保存
        diff_path = OUT_DIR / f"{case_key}.diff.txt"
        diff_text = "\n".join(
            difflib.unified_diff(
                gen_norm.splitlines(), act_norm.splitlines(),
                fromfile="generated", tofile="actual", n=2, lineterm=""
            )
        )
        diff_path.write_text(diff_text or "(identical)", encoding="utf-8")

        # 参照エラーを含む生成結果は評価対象外 (情報源不在の正直な明示として skip)
        skip_reason = ""
        if "<<参照エラー" in gen_text:
            skip_reason = "reference_error_in_output"

        rows.append({
            "case_key": case_key, "appno": appno, "pattern": res.pattern,
            "match": stats["match"],
            "ratio": stats["ratio"],
            "len_gen": stats["len_gen"], "len_act": stats["len_act"],
            "common_prefix": stats["common_prefix"],
            "source": res.source_used, "error": "",
            "skip_reason": skip_reason,
        })
        if skip_reason:
            print(f"  {case_key:15s} SKIP ({skip_reason}) pattern={res.pattern}")
        else:
            print(f"  {case_key:15s} match={stats['match']} ratio={stats['ratio']:.3f} pattern={res.pattern}")

    # ログ出力
    cols = ["case_key", "appno", "pattern", "match", "ratio", "len_gen", "len_act", "common_prefix", "source", "error", "skip_reason"]
    log_path = OUT_DIR / "_batch_log.tsv"
    with log_path.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    # サマリ
    skipped_rows = [r for r in rows if r.get("skip_reason")]
    # 評価対象は ratio>0 かつ skip_reason 無し
    valid = [r for r in rows if r.get("ratio", 0) > 0 and not r.get("skip_reason")]
    if valid:
        # 新指標: 正規化後完全一致 Y/N の正答率
        n_match = sum(1 for r in valid if r.get("match") == "Y")
        match_rate = n_match / len(valid)
        # 旧指標 (ログ目的で残す)
        avg_ratio = sum(r["ratio"] for r in valid) / len(valid)
        ratios = sorted(r["ratio"] for r in valid)
        median = ratios[len(ratios) // 2]
        max_r = ratios[-1]
        min_r = ratios[0]
        n_perfect = sum(1 for r in valid if r["ratio"] >= 0.99)
        print(f"\n=== summary ===")
        print(f"  valid: {len(valid)}/{len(rows)}")
        print(f"  skipped: {len(skipped_rows)}/{len(rows)} (情報源不足のため評価対象外)")
        print(f"  [新指標] match Y: {n_match}/{len(valid)} ({match_rate:.3f})")
        print(f"  [旧指標] ratio mean={avg_ratio:.3f} median={median:.3f} min={min_r:.3f} max={max_r:.3f}")
        print(f"  [旧指標] ratio >= 0.99: {n_perfect}")
    if skipped_rows:
        print("\n  --- skipped cases ---")
        for r in skipped_rows:
            print(f"    {r['case_key']:15s} reason={r['skip_reason']} pattern={r.get('pattern','')}")
    print(f"  log: {log_path}")
    print(f"  per-case: {OUT_DIR}/<case_key>.{{gen,diff}}.txt")


if __name__ == "__main__":
    main()
