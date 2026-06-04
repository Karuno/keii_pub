"""08_fetch_parent_chain.py — 分割親出願の再帰ツリー構築（API 経由）

【注意】このスクリプトは API での再帰追跡を行うが、API は 8世代までしか辿れない
（最先まで届かない）。ユーザー方針により分割系列追跡は JPP「出願情報」タブを
正の経路（10_fetch_aux_appinfo.py）とする。本スクリプトは保守目的で残置するが、
分割系列の正確性を要する処理では 10_fetch_aux_appinfo.py の出力を使うこと。

本願の parentApplicationInformation を起点に、親 → 親の親 ... と JPO API で再帰取得し、
ツリー（=チェーン）を構築する。

入力:
  inventory/case_appno_map.tsv (case_key, appno)
出力:
  inventory/parent_chains/{appno}.json   各案件のチェーン情報
  inventory/parent_chains_log.tsv        実行ログ

各 chain は:
  [
    { "appno": "...", "filingDate": "YYYYMMDD", "parent_appno": "...", "is_pct_national_phase": bool },
    ...
  ]
  ※ 0 番目が本願、最後が「最先の出願（=親なし）」

CLI:
  python 08_fetch_parent_chain.py             # 全件
  python 08_fetch_parent_chain.py --appno 2023026797
  python 08_fetch_parent_chain.py --force     # 既存も再取得
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

INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
COLLECTED_DIR = INV_DIR / "doc_history_collected"
CHAIN_DIR = INV_DIR / "parent_chains"
LOG_PATH = INV_DIR / "parent_chains_log.tsv"

API_PROGRESS = "app_progress/{appno}"
SLEEP_SEC = 0.7
MAX_DEPTH = 15  # 異常な無限ループ防止


def load_doc_history_local(appno: str) -> dict | None:
    """既取得の doc_history を返す。なければ None。"""
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


def fetch_doc_history(appno: str, token: str) -> dict | None:
    """JPO API から取得して collected/ に保存。"""
    time.sleep(SLEEP_SEC)
    r = api_get(API_PROGRESS.format(appno=appno), token)
    status = r.get("result", {}).get("statusCode", "")
    if status != "100":
        return None
    COLLECTED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = COLLECTED_DIR / f"{appno}.json"
    out_path.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r


def is_pct_national(appno: str) -> bool:
    return len(appno) == 10 and appno[4] == "5"


def build_chain(start_appno: str, token: str, fetcher_calls: list[int]) -> list[dict]:
    """親を再帰的に辿ってチェーンを返す。"""
    chain: list[dict] = []
    current = start_appno
    visited: set[str] = set()
    while current and current not in visited and len(chain) < MAX_DEPTH:
        visited.add(current)
        # まずローカル
        raw = load_doc_history_local(current)
        if not raw:
            raw = fetch_doc_history(current, token)
            fetcher_calls[0] += 1
        if not raw:
            chain.append({"appno": current, "error": "fetch failed"})
            break
        data = raw.get("result", {}).get("data", {}) or {}
        parent = data.get("parentApplicationInformation", {}) or {}
        parent_appno = parent.get("parentApplicationNumber", "")
        parent_filing = parent.get("filingDate", "")
        own_filing = data.get("filingDate", "")
        chain.append({
            "appno": current,
            "filingDate": own_filing,
            "parent_appno": parent_appno or None,
            "parent_filingDate": parent_filing or None,
            "is_pct_national_phase": is_pct_national(current),
        })
        # 親が同じ filingDate ならループ防止（PCT 国内移行で記録）
        if parent_appno and parent_filing != own_filing:
            current = parent_appno
        else:
            break
    return chain


def load_targets(args) -> list[tuple[str, str]]:
    if args.appno:
        return [("(single)", args.appno)]
    if not APPNO_MAP.exists():
        sys.exit(f"appno map not found: {APPNO_MAP}")
    seen: dict[str, str] = {}
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 4 and cols[3] == "ok" and cols[1]:
            seen.setdefault(cols[0], cols[1])
    items = sorted(seen.items())
    if args.limit > 0:
        items = items[: args.limit]
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--appno", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args)
    print(f"targets: {len(targets)}")

    # 親取得が必要なのでトークン用意
    token = get_access_token()
    print(f"token: {token[:18]}...")

    fetcher_calls = [0]
    log_rows: list[dict] = []
    for i, (case_key, appno) in enumerate(targets, 1):
        out_path = CHAIN_DIR / f"{appno}.json"
        if not args.force and out_path.exists():
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            generations = len(existing.get("chain", [])) - 1
            print(f"  [{i:3d}/{len(targets)}] SK {case_key:15s} appno={appno}  generations={generations}")
            log_rows.append({"case_key": case_key, "appno": appno,
                             "generations": generations, "skipped": True})
            continue

        chain = build_chain(appno, token, fetcher_calls)
        rec = {"case_key": case_key, "appno": appno, "chain": chain}
        out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        generations = len(chain) - 1   # 本願は世代0、親が世代1...
        # PCT 国内移行が起源 (chain 末尾が PCT) かどうか
        last_pct = chain[-1].get("is_pct_national_phase", False) if chain else False
        print(f"  [{i:3d}/{len(targets)}] OK {case_key:15s} appno={appno}  "
              f"generations={generations}  last_pct={last_pct}")
        log_rows.append({"case_key": case_key, "appno": appno,
                         "generations": generations, "last_pct": last_pct, "skipped": False})

    cols = ["case_key", "appno", "generations", "last_pct", "skipped"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    print(f"\n=== summary ===")
    print(f"  total cases: {len(log_rows)}")
    print(f"  fetcher API calls: {fetcher_calls[0]}")
    gen_dist: dict[int, int] = {}
    for r in log_rows:
        g = r["generations"]
        gen_dist[g] = gen_dist.get(g, 0) + 1
    print(f"  generations distribution: {sorted(gen_dist.items())}")


if __name__ == "__main__":
    main()
