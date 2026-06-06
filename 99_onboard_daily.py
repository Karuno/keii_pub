"""99_onboard_daily.py — Z審決appnoを日次でランダムN件 onboard

cron で毎日呼ばれ、archive 由来の Z審決 appno list と VPS既存inventoryを差分し、
未取得 appno からランダムN件を fetcher.onboard_appno で取得する。

進化ループ実行中 (/tmp/lievito_evolve.lock) はスキップ。

CLI:
  python 99_onboard_daily.py [N]
  N=50 がデフォルト
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# JPO API 認証情報パス (VPS 環境)
os.environ.setdefault("JPO_API_DIR", "/opt/keii_secrets/jpo_api")

HERE = Path(__file__).resolve().parent
APPNO_LIST = HERE / "inventory" / "z_appno_list.json"
EXISTING_DIR = HERE / "inventory" / "doc_history_collected"
LOG = HERE / "inventory" / "onboard_daily.log"
EVOLVE_LOCK = Path("/tmp/lievito_evolve.lock")
DEFAULT_N = 50


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    if EVOLVE_LOCK.exists():
        log(f"SKIP: evolve loop running ({EVOLVE_LOCK})")
        return

    candidates = json.loads(APPNO_LIST.read_text(encoding="utf-8"))
    existing = {p.stem for p in EXISTING_DIR.glob("*.json")}
    todo = [a for a in candidates if a not in existing]
    random.shuffle(todo)
    todo = todo[:n]
    log(f"start onboard: target={len(todo)} (candidates remaining={len([a for a in candidates if a not in existing])})")

    ok_count = 0
    fail_count = 0
    t_start = time.time()
    for i, appno in enumerate(todo, 1):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "fetcher.onboard_appno", appno],
                capture_output=True, text=True, timeout=240,
                cwd=str(HERE),
            )
            if proc.returncode == 0:
                # 簡易判定: doc_history.json が生成されたか
                if (EXISTING_DIR / f"{appno}.json").exists():
                    log(f"  OK   [{i}/{len(todo)}] {appno}")
                    ok_count += 1
                else:
                    log(f"  WARN [{i}/{len(todo)}] {appno}: exit 0 but no doc_history")
                    fail_count += 1
            else:
                err = proc.stderr.strip().split("\n")[-1][:120] if proc.stderr else "(no stderr)"
                log(f"  FAIL [{i}/{len(todo)}] {appno}: exit {proc.returncode} — {err}")
                fail_count += 1
        except subprocess.TimeoutExpired:
            log(f"  TIMEOUT [{i}/{len(todo)}] {appno}")
            fail_count += 1
        except Exception as e:
            log(f"  EXCEPT [{i}/{len(todo)}] {appno}: {type(e).__name__}: {e}")
            fail_count += 1

    elapsed = time.time() - t_start
    log(f"done: ok={ok_count} fail={fail_count} elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    main()
