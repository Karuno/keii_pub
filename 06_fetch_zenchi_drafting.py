"""06_fetch_zenchi_drafting.py — 前置報告書の作成日を補助ソースから取得

主情報源 (JPO API) では前置報告書 (A913) 本文 XML が取得対象外のため、
独立した補助情報源から、出願番号→経過参照→前置報告書ドキュメントの
「作成日」を抽出して inventory に保存する。

接続先 URL は外部から注入する (環境変数 ZENCHI_SOURCE_URL、または
ZENCHI_SOURCE_FILE で示されるファイル、デフォルト
/opt/keii_secrets/zenchi_source_url から読み込む)。
本ファイルには URL リテラルを含まない。

入力: inventory/case_appno_map.tsv  (case_key, appno)
出力:
  inventory/zenchi_drafting/{appno}.json   各案件の取得結果
  inventory/zenchi_drafting_log.tsv        実行ログ

CLI:
  python 06_fetch_zenchi_drafting.py                # 全件
  python 06_fetch_zenchi_drafting.py --limit 3      # パイロット
  python 06_fetch_zenchi_drafting.py --appno 2018244177
  python 06_fetch_zenchi_drafting.py --force        # 既存も再取得
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    from playwright.sync_api import Page, BrowserContext
except ImportError:
    print("playwright not installed. pip install playwright && playwright install chromium",
          file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
OUT_DIR = INV_DIR / "zenchi_drafting"
LOG_PATH = INV_DIR / "zenchi_drafting_log.tsv"


def _load_source_url() -> str:
    """接続先 URL を環境変数 or secrets ファイルから読む。

    優先順位:
      1. 環境変数 ZENCHI_SOURCE_URL
      2. ファイル ZENCHI_SOURCE_FILE (デフォルト /opt/keii_secrets/zenchi_source_url)
    """
    env_url = (os.environ.get("ZENCHI_SOURCE_URL") or "").strip()
    if env_url:
        return env_url
    secret_path = Path(
        os.environ.get("ZENCHI_SOURCE_FILE") or "/opt/keii_secrets/zenchi_source_url"
    )
    if secret_path.exists():
        url = secret_path.read_text(encoding="utf-8").strip()
        if url:
            return url
    raise RuntimeError(
        "zenchi source URL not configured "
        "(set ZENCHI_SOURCE_URL or place URL in /opt/keii_secrets/zenchi_source_url)"
    )


TOP_URL = _load_source_url()
SEARCH_GAP_SEC = 4.0  # 外部サーバへの負荷配慮
SHORT_WAIT = 2.0
LONG_WAIT = 5.0

ZEN_TR = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}


def extract_drafting_date(html: str) -> tuple[str | None, str | None]:
    """対象ドキュメント本文中の『作成日 (元号N年M月D日)』を抽出 → YYYY-MM-DD。"""
    m = re.search(r'<a\s+name="D_PAGE1"', html)
    if not m:
        return None, "page anchor not found"
    tail = html[m.end():]
    m2 = re.search(
        r'作成日[\s　:：]*(令和|平成|昭和|大正)[\s　]*([\d０-９元]+)[\s　]*年'
        r'[\s　]*([\d０-９]+)[\s　]*月[\s　]*([\d０-９]+)[\s　]*日',
        tail[:5000]
    )
    if not m2:
        return None, "作成日 pattern not found"
    era, y_raw, m_raw, d_raw = m2.group(1), m2.group(2), m2.group(3), m2.group(4)
    y_raw = y_raw.translate(ZEN_TR).replace("元", "1")
    m_raw = m_raw.translate(ZEN_TR)
    d_raw = d_raw.translate(ZEN_TR)
    year = ERA_BASE[era] + int(y_raw)
    return f"{year:04d}-{int(m_raw):02d}-{int(d_raw):02d}", None


def appno_hyphenated(appno_10: str) -> str:
    """10桁出願番号を YYYY-NNNNNN 形式に。"""
    if len(appno_10) == 10 and appno_10.isdigit():
        return f"{appno_10[:4]}-{appno_10[4:]}"
    return appno_10


def navigate_to_inquiry(page: Page) -> None:
    """トップ → 番号照会 ページまでナビ。"""
    page.goto(TOP_URL, wait_until="networkidle", timeout=60000)
    time.sleep(SHORT_WAIT)
    page.locator("#cfc001_globalNav_item_0").click(timeout=10000)
    time.sleep(1)
    page.locator("#cfc001_globalNav_sub_item_0_0").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_selector("#p00_srchCondtn_txtDocNoInputNo0", timeout=15000)


def fetch_zenchi_for(context: BrowserContext, page: Page, appno: str) -> dict:
    """1 案件分の取得。"""
    rec: dict = {
        "appno": appno,
        "appno_input": appno_hyphenated(appno),
        "found_keika": False,
        "found_zenchi_link": False,
        "drafting_date": None,
        "drafting_dates_all": [],
        "error": None,
    }

    inp = page.locator("#p00_srchCondtn_txtDocNoInputNo0")
    inp.fill("")
    time.sleep(0.5)
    inp.fill(rec["appno_input"])
    time.sleep(0.5)
    page.locator("#p00_searchBtn_btnDocInquiry").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    time.sleep(SHORT_WAIT)

    try:
        with context.expect_page(timeout=30000) as np_info:
            page.locator("#patentUtltyIntnlNumOnlyLst_tableView_progReferenceInfo0").click(timeout=10000)
        keika_page = np_info.value
        keika_page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(LONG_WAIT)
        rec["found_keika"] = True
    except PWTimeoutError as e:
        rec["error"] = f"参照ボタンが見つからず: {e}"
        return rec

    keika_html = keika_page.content()
    if "前置報告書" not in keika_html:
        keika_page.close()
        rec["error"] = "前置報告書 not in keika"
        return rec
    rec["found_zenchi_link"] = True

    zenchi_links = keika_page.locator('a:has-text("前置報告書")')
    n_links = zenchi_links.count()
    for i in range(n_links):
        try:
            with context.expect_page(timeout=30000) as np2_info:
                zenchi_links.nth(i).click(timeout=10000)
            doc_page = np2_info.value
            doc_page.wait_for_load_state("networkidle", timeout=60000)
            try:
                doc_page.wait_for_function(
                    "document.body && document.body.innerHTML.includes('D_PAGE1')",
                    timeout=30000,
                )
            except PWTimeoutError:
                pass
            time.sleep(SHORT_WAIT)
            html = doc_page.content()
            date, err = extract_drafting_date(html)
            if date:
                rec["drafting_dates_all"].append(date)
            else:
                rec["drafting_dates_all"].append({"error": err})
            doc_page.close()
        except Exception as e:
            rec["drafting_dates_all"].append({"error": str(e)[:100]})

    keika_page.close()

    valid_dates = [d for d in rec["drafting_dates_all"] if isinstance(d, str)]
    if valid_dates:
        rec["drafting_date"] = sorted(valid_dates)[0]

    return rec


def load_targets(args) -> list[tuple[str, str]]:
    if args.appno:
        return [("(single)", args.appno)]
    if not APPNO_MAP.exists():
        print(f"appno map not found: {APPNO_MAP}", file=sys.stderr)
        sys.exit(1)
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
    ap.add_argument("--force", action="store_true", help="既存も再取得")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args)
    print(f"targets: {len(targets)}")

    log_rows: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP", viewport={"width": 1400, "height": 1000})
        page = context.new_page()
        try:
            navigate_to_inquiry(page)
        except Exception as e:
            print(f"initial navigation failed: {e}", file=sys.stderr)
            browser.close()
            sys.exit(1)

        for i, (case_key, appno) in enumerate(targets, 1):
            out_path = OUT_DIR / f"{appno}.json"
            if not args.force and out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                d = existing.get("drafting_date")
                print(f"  [{i:3d}/{len(targets)}] SK {case_key:15s} appno={appno}  drafting={d or '(none)'}")
                log_rows.append({"i": i, "case_key": case_key, "appno": appno, "skipped": True,
                                 "found_keika": existing.get("found_keika"),
                                 "found_zenchi_link": existing.get("found_zenchi_link"),
                                 "drafting_date": d, "error": existing.get("error", "")})
                continue

            time.sleep(SEARCH_GAP_SEC)
            try:
                rec = fetch_zenchi_for(context, page, appno)
            except Exception as e:
                rec = {"appno": appno, "drafting_date": None, "error": f"exception: {type(e).__name__}: {e}"}

            rec["case_key"] = case_key
            out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            d = rec.get("drafting_date")
            err = rec.get("error", "") or ""
            print(f"  [{i:3d}/{len(targets)}] OK {case_key:15s} appno={appno}  drafting={d or '(none)'}  err={err[:50]}")
            log_rows.append({"i": i, "case_key": case_key, "appno": appno, "skipped": False,
                             "found_keika": rec.get("found_keika"),
                             "found_zenchi_link": rec.get("found_zenchi_link"),
                             "drafting_date": d, "error": err})

            try:
                navigate_to_inquiry(page)
            except Exception as e:
                print(f"  ! navigation reset failed: {e}", file=sys.stderr)
                break

        browser.close()

    cols = ["i", "case_key", "appno", "skipped", "found_keika", "found_zenchi_link", "drafting_date", "error"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    n_dates = sum(1 for r in log_rows if r.get("drafting_date"))
    n_no_zenchi = sum(1 for r in log_rows if r.get("error") == "前置報告書 not in keika")
    print(f"\n=== summary ===")
    print(f"  drafting_date acquired: {n_dates}")
    print(f"  no 前置報告書: {n_no_zenchi}")
    print(f"  total: {len(log_rows)}")


if __name__ == "__main__":
    main()
