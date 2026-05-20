"""04_fetch_doc_history.py — JPO API で doc_history.json を一括取得

入力:
  inventory/case_appno_map.tsv  ─ case_key → appno マップ

出力:
  inventory/doc_history_collected/{appno}.json   各案件のJPO API応答
  inventory/fetch_log.tsv                         取得結果ログ

設計:
  - tools/fetcher/probe_jpo_api.py のトークン取得・APIコール機構を再利用
  - 既存ファイルがあればスキップ（resumable）
  - API レート制限応答（statusCode != "100"）で即時停止
  - 0.7秒スリープで穏やかに

CLI:
  python 04_fetch_doc_history.py --limit N      # 先頭N件のみ（パイロット用）
  python 04_fetch_doc_history.py                # 全件
  python 04_fetch_doc_history.py --force        # 既存ファイルも再取得
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE
sys.path.insert(0, str(REPO_ROOT))

# settings 経由で JPO_API_DIR を環境変数に反映してから probe_jpo_api を import する
from generator.settings import get_settings  # noqa: E402
get_settings()
from fetcher.probe_jpo_api import api_get, get_access_token  # noqa: E402
from generator.api_remain import (  # noqa: E402
    check_remain_or_abort, update_remain,
)

INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
COLLECTED_DIR = INV_DIR / "doc_history_collected"
LOG_PATH = INV_DIR / "fetch_log.tsv"

# 既存 doc_history.json の補助探索領域（settings.inputs_fallback_dir）。
# 未設定なら None 扱いで補助探索をスキップ。
from generator.settings import get_inputs_fallback_dir  # noqa: E402
EXISTING_DH_DIR = get_inputs_fallback_dir()

SLEEP_SEC = 0.7


def load_unique_targets() -> list[tuple[str, str]]:
    """case_appno_map.tsv から unique (case_key, appno) を返す。"""
    if not APPNO_MAP.exists():
        print(f"appno map not found: {APPNO_MAP}", file=sys.stderr)
        sys.exit(1)
    seen: dict[str, str] = {}
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        case_key, appno, _src, status = cols[0], cols[1], cols[2], cols[3]
        if status != "ok" or not appno:
            continue
        # 同じ case_key で複数取れていれば最初を採用
        seen.setdefault(case_key, appno)
    return sorted(seen.items())


def existing_doc_history_appnos() -> set[str]:
    """補助領域 (settings.inputs_fallback_dir) で既に取れている案件の appno を返す。
    未設定なら空集合（補助探索しない）。"""
    out: set[str] = set()
    if EXISTING_DH_DIR is None or not EXISTING_DH_DIR.exists():
        return out
    for p in EXISTING_DH_DIR.rglob("doc_history.json"):
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            appno = j.get("result", {}).get("data", {}).get("applicationNumber", "")
            if appno:
                out.add(appno)
        except Exception:
            pass
    return out


def fetch_one(appno: str, token: str) -> dict:
    """JPO app_progress エンドポイントを叩く。"""
    return api_get(f"app_progress/{appno}", token)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="既存ファイル再取得")
    args = ap.parse_args()

    COLLECTED_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_unique_targets()
    existing = existing_doc_history_appnos()

    print(f"unique targets: {len(targets)}")
    print(f"already in inputs/: {len(existing)} (will skip)")

    plan: list[tuple[str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    for case_key, appno in targets:
        out_path = COLLECTED_DIR / f"{appno}.json"
        if not args.force and out_path.exists():
            skipped.append((case_key, appno, "already_collected"))
            continue
        if not args.force and appno in existing:
            skipped.append((case_key, appno, "already_in_inputs"))
            continue
        plan.append((case_key, appno))

    if args.limit > 0:
        plan = plan[: args.limit]

    print(f"planned fetches: {len(plan)}")
    print(f"skipped (resume): {len(skipped)}")

    if not plan:
        print("nothing to fetch.")
        return

    # API 残量チェック（10アクセス常時バッファ）
    check_remain_or_abort(planned_calls=len(plan))

    print(f"\nacquiring token...")
    token = get_access_token()
    print(f"token acquired: {token[:18]}...")

    log_rows: list[dict] = []
    stop_reason: str | None = None

    for i, (case_key, appno) in enumerate(plan, 1):
        time.sleep(SLEEP_SEC)
        try:
            r = fetch_one(appno, token)
        except Exception as e:
            row = {"i": i, "case_key": case_key, "appno": appno,
                   "statusCode": "", "remain": "", "saved": False, "note": f"exception {type(e).__name__}: {e}"}
            log_rows.append(row)
            print(f"  [{i:3d}/{len(plan)}] EX {case_key} {appno}  {row['note'][:60]}")
            stop_reason = "exception"
            break

        status = r.get("result", {}).get("statusCode", "")
        remain = r.get("result", {}).get("remainAccessCount", "")
        err = r.get("result", {}).get("errorMessage", "")
        saved = False
        note = ""

        if status == "100":
            out_path = COLLECTED_DIR / f"{appno}.json"
            out_path.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
            saved = True
        elif status in ("204",):  # 該当データなし
            note = f"no_data ({err})"
        elif status in ("429", "503", "999") or "limit" in str(err).lower() or "rate" in str(err).lower():
            note = f"rate_limit_or_throttled ({err})"
            stop_reason = "rate_limited"
        else:
            note = f"unexpected status={status} err={err}"

        row = {"i": i, "case_key": case_key, "appno": appno,
               "statusCode": status, "remain": remain, "saved": saved, "note": note}
        log_rows.append(row)
        marker = "OK" if saved else ("SK" if status == "204" else "NG")
        print(f"  [{i:3d}/{len(plan)}] {marker} {case_key:15s} appno={appno}  status={status}  remain={remain}  {note[:60]}")

        # 残量更新と10アクセスバッファガード
        if remain:
            try:
                remain_int = int(remain)
                update_remain(remain_int)
                if remain_int <= 10:
                    print(f"\n** STOP: 残量 {remain_int} <= 10 (バッファ)。これ以上は呼び出さず終了。**")
                    stop_reason = "buffer_reached"
                    break
            except Exception:
                pass

        if stop_reason == "rate_limited":
            print(f"\n** STOP: rate limit detected. Re-run later (manual trigger). **")
            break

    # ログ書き出し
    cols = ["i", "case_key", "appno", "statusCode", "remain", "saved", "note"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    n_saved = sum(1 for r in log_rows if r["saved"])
    print(f"\n=== summary ===")
    print(f"  saved: {n_saved}")
    print(f"  attempted: {len(log_rows)}")
    print(f"  log: {LOG_PATH}")
    if log_rows:
        print(f"  last remainAccessCount: {log_rows[-1]['remain']}")


if __name__ == "__main__":
    main()
