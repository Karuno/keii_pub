"""generator/api_remain.py — JPO API 残量ガード

ユーザー指示: 常に10アクセスは残すように作業すること。

メカニズム:
  - inventory/_api_remain.txt に最新の remainAccessCount を保存
  - 各 API call の前に check_remain_or_abort(planned_calls) を呼んで、
    10 + planned_calls の残量がなければ即停止する
  - 各 API call のレスポンスから取得した remainAccessCount を update_remain で記録

使い方:
  from .api_remain import check_remain_or_abort, update_remain

  check_remain_or_abort(planned_calls=44)  # 残量 < 10+44=54 なら停止
  ...
  resp = api_get(...)
  remain = resp.get("result", {}).get("remainAccessCount", "")
  if remain:
      update_remain(int(remain))

ファイル不在時は最新の doc_history_collected/*.json から推定する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INV_DIR = HERE.parent / "inventory"
REMAIN_FILE = INV_DIR / "_api_remain.txt"
COL_DIR = INV_DIR / "doc_history_collected"

BUFFER = 10  # 常時残すバッファ


def get_remain() -> int | None:
    """最新の API 残量を返す（不明なら None）。"""
    if REMAIN_FILE.exists():
        try:
            return int(REMAIN_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    # フォールバック: doc_history_collected の最新 mtime ファイル
    if COL_DIR.exists():
        files = sorted(COL_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:5]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                r = data.get("result", {}).get("remainAccessCount", "")
                if r:
                    return int(r)
            except Exception:
                continue
    return None


def update_remain(value: int) -> None:
    INV_DIR.mkdir(exist_ok=True)
    REMAIN_FILE.write_text(str(value), encoding="utf-8")


def check_remain_or_abort(planned_calls: int) -> int:
    """残量チェック。残量 < 10 + planned_calls なら sys.exit する。

    Returns: 現在の残量
    """
    remain = get_remain()
    if remain is None:
        print(
            "[api_remain] WARN: 残量不明。続行するが慎重に。",
            file=sys.stderr,
        )
        return -1
    threshold = BUFFER + planned_calls
    if remain < threshold:
        print(
            f"[api_remain] STOP: 残量 {remain} < 必要量 {threshold} (バッファ {BUFFER} + 予定 {planned_calls})。"
            f"\n              ユーザー指示により API 呼び出しを停止します。",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"[api_remain] OK: 残量 {remain} / 必要 {threshold} (バッファ {BUFFER} + 予定 {planned_calls})")
    return remain


def safe_api_get(api_get_fn, endpoint: str, token: str) -> dict:
    """api_get のラッパー。レスポンスから remainAccessCount を取得して update。"""
    r = api_get_fn(endpoint, token)
    remain = r.get("result", {}).get("remainAccessCount", "")
    if remain:
        try:
            update_remain(int(remain))
        except Exception:
            pass
    return r
