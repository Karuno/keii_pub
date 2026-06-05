"""09_fetch_aux_division_chain.py — 補助ソースの分割系列タブから親系列を取得

ユーザー指示: API では8世代までしか追えない（最先記録なし）が、
              補助ソースの分割系列タブは深い世代まで取れる（本願の祖先系列が完全）。
              よって補助ソースを正の経路にし、API は使わない。

経路:
  1. トップ → 番号照会 → 出願番号 (YYYY-NNNNNN)
  2. 経過情報ボタン → 新ウィンドウ
  3. 「分割出願情報」タブをクリック
  4. テーブル/ノードから世代別の出願番号と出願日を抽出

出力:
  inventory/aux_division_chains/{appno}.json   案件別の分割系列
  inventory/aux_division_log.tsv               実行ログ

CLI:
  python 09_fetch_aux_division_chain.py --appno 2023026797
  python 09_fetch_aux_division_chain.py             # 全44件
  python 09_fetch_aux_division_chain.py --force     # 既存も再取得
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    from playwright.sync_api import Page, BrowserContext
except ImportError:
    print("playwright not installed", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
OUT_DIR = INV_DIR / "aux_division_chains"
LOG_PATH = INV_DIR / "aux_division_log.tsv"

def _load_source_url() -> str:
    import os as _os
    from pathlib import Path as _P
    env_url = (_os.environ.get("ZENCHI_SOURCE_URL") or "").strip()
    if env_url:
        return env_url
    secret_path = _P(_os.environ.get("ZENCHI_SOURCE_FILE") or "/opt/keii_secrets/zenchi_source_url")
    if secret_path.exists():
        url = secret_path.read_text(encoding="utf-8").strip()
        if url:
            return url
    raise RuntimeError("source URL not configured")


TOP_URL = _load_source_url()
SEARCH_GAP_SEC = 4.0
SHORT_WAIT = 2.0
LONG_WAIT = 5.0


def appno_hyphen(appno_10: str) -> str:
    if len(appno_10) == 10 and appno_10.isdigit():
        return f"{appno_10[:4]}-{appno_10[4:]}"
    return appno_10


def navigate_to_keika(page: Page, context: BrowserContext, appno: str) -> Page | None:
    """番号照会 → 経過情報ウィンドウまで遷移し、新ウィンドウを返す。"""
    page.goto(TOP_URL, wait_until="networkidle", timeout=60000)
    time.sleep(SHORT_WAIT)
    page.locator("#cfc001_globalNav_item_0").click(timeout=10000)
    time.sleep(1)
    page.locator("#cfc001_globalNav_sub_item_0_0").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_selector("#p00_srchCondtn_txtDocNoInputNo0", timeout=15000)

    page.locator("#p00_srchCondtn_txtDocNoInputNo0").fill(appno_hyphen(appno))
    page.locator("#p00_searchBtn_btnDocInquiry").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    time.sleep(SHORT_WAIT)

    try:
        with context.expect_page(timeout=30000) as np_info:
            page.locator("#patentUtltyIntnlNumOnlyLst_tableView_progReferenceInfo0").click(timeout=10000)
        keika = np_info.value
        keika.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(LONG_WAIT)
        return keika
    except PWTimeoutError:
        return None


def click_division_tab(keika: Page) -> bool:
    """「分割出願情報」タブをクリック。"""
    try:
        tab = keika.locator('text="分割出願情報"').first
        if tab.count() == 0:
            return False
        tab.click(timeout=10000)
        time.sleep(LONG_WAIT)  # SPA 描画
        return True
    except Exception:
        return False


def parse_division_table(html: str) -> list[dict]:
    """分割出願情報タブの HTML から世代別エントリを抽出。

    観察: HTML 内に「第N世代 出願 YYYY-NNNNNN 公開 YYYY...」のような
          フラットなテキストパターンが現れる。
    抽出戦略:
      ノード/カードの順序で「世代名 + 出願番号」を抜く。
    """
    plain = re.sub(r"<[^>]+>", " ", html)
    plain = re.sub(r"\s+", " ", plain)

    # 「第N世代」「親出願」「最先の出願」「本願」のキーワード
    # その後の最初の YYYY-NNNNNN を出願番号とする
    rows: list[dict] = []
    # ありそうなパターン: 「第１世代 出願 2014-084257 公開 2014-201234」
    # 漢数字対応も
    gen_pattern = re.compile(
        r"(第[一二三四五六七八九十0-9０-９]+世代|親出願|最先(?:の出願)?|本願|出願)"
        r"[\s　]*"
        r"(?:出願)?[\s　]*"
        r"(\d{4}[-－]\d+)",
    )
    for m in gen_pattern.finditer(plain):
        label = m.group(1)
        appno = m.group(2).replace("－", "-")
        # 0-padding
        parts = appno.split("-")
        if len(parts) == 2 and parts[1].isdigit():
            appno = f"{parts[0]}-{int(parts[1]):06d}"
        rows.append({"label": label, "appno_dashed": appno, "appno": appno.replace("-", "")})
    # 重複排除（同一 appno_dashed）
    seen: set[str] = set()
    unique: list[dict] = []
    for r in rows:
        if r["appno_dashed"] in seen:
            continue
        seen.add(r["appno_dashed"])
        unique.append(r)
    return unique


def fetch_one(context: BrowserContext, page: Page, appno: str) -> dict:
    rec: dict = {"appno": appno, "found_division_tab": False, "entries": [], "error": None}
    keika = navigate_to_keika(page, context, appno)
    if not keika:
        rec["error"] = "経過情報ウィンドウ開けず"
        return rec

    if not click_division_tab(keika):
        rec["error"] = "分割出願情報タブが見つからない（=分割でない可能性）"
        keika.close()
        return rec
    rec["found_division_tab"] = True

    html = keika.content()
    rec["entries"] = parse_division_table(html)
    keika.close()
    return rec


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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args)
    print(f"targets: {len(targets)}")

    log_rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP", viewport={"width": 1500, "height": 1200})
        page = context.new_page()

        for i, (case_key, appno) in enumerate(targets, 1):
            out_path = OUT_DIR / f"{appno}.json"
            if not args.force and out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                n = len(existing.get("entries", []))
                print(f"  [{i:3d}/{len(targets)}] SK {case_key:15s} appno={appno}  entries={n}")
                continue

            time.sleep(SEARCH_GAP_SEC)
            try:
                rec = fetch_one(context, page, appno)
            except Exception as e:
                rec = {"appno": appno, "entries": [], "error": f"exception: {type(e).__name__}: {e}"}

            rec["case_key"] = case_key
            out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            n = len(rec.get("entries", []))
            err = rec.get("error", "") or ""
            print(f"  [{i:3d}/{len(targets)}] OK {case_key:15s} appno={appno}  entries={n}  err={err[:60]}")
            log_rows.append({"case_key": case_key, "appno": appno, "entries": n,
                             "found_division_tab": rec.get("found_division_tab", False), "error": err})

        browser.close()

    cols = ["case_key", "appno", "entries", "found_division_tab", "error"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    n_div = sum(1 for r in log_rows if r["found_division_tab"])
    print(f"\n=== summary ===")
    print(f"  with division tab: {n_div}/{len(log_rows)}")


if __name__ == "__main__":
    main()
