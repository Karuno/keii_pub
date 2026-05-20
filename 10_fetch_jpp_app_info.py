"""10_fetch_jpp_app_info.py — JPP「出願情報」タブから親出願番号を再帰取得

用途: 分割出願の系列追跡（API 不正確のため JPP 一本化）

各号について JPP 経過情報→「出願情報」タブを開き、以下を抽出:
  - 出願日
  - 親出願番号（あれば）
  - 「分割（44条1項）」フラグ
  - 国内優先有無

本願から再帰的に親を遡り、直系系列を構築。失敗時はエラー（API フォールバックなし）。

CLI:
  python 10_fetch_jpp_app_info.py --appno 2023026797
  python 10_fetch_jpp_app_info.py             # 全44件
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
OUT_DIR = INV_DIR / "jpp_app_info"
LOG_PATH = INV_DIR / "jpp_app_info_log.tsv"

TOP_URL = "https://www.j-platpat.inpit.go.jp/"
SEARCH_GAP_SEC = 4.0
SHORT_WAIT = 2.0
LONG_WAIT = 5.0
MAX_DEPTH = 15  # 異常な無限ループ防止


def appno_hyphen(appno_10: str) -> str:
    if len(appno_10) == 10 and appno_10.isdigit():
        return f"{appno_10[:4]}-{appno_10[4:]}"
    return appno_10


def appno_to_10(appno_dashed: str) -> str:
    """'2021-205974' → '2021205974'"""
    s = appno_dashed.replace("-", "").replace("－", "")
    return s if len(s) == 10 else appno_dashed


def navigate_to_app_info(page: Page, context: BrowserContext, appno: str) -> Page | None:
    """番号照会→経過情報→「出願情報」タブまで遷移し、新ウィンドウを返す。"""
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

    # 経過情報ボタンが見つからない場合（=該当出願なし）はNoneで返す
    btn = page.locator("#patentUtltyIntnlNumOnlyLst_tableView_progReferenceInfo0")
    if btn.count() == 0:
        return None
    try:
        with context.expect_page(timeout=30000) as np_info:
            btn.first.click(timeout=10000)
        keika = np_info.value
        keika.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(LONG_WAIT)
    except PWTimeoutError:
        return None

    # 「出願情報」タブクリック
    try:
        tab = keika.locator('text="出願情報"').first
        if tab.count() == 0:
            keika.close()
            return None
        tab.click(timeout=5000)
        time.sleep(LONG_WAIT)
    except Exception:
        keika.close()
        return None
    return keika


def parse_app_info(html: str) -> dict:
    """出願情報タブの HTML から各種情報を抽出。

    JPP実物観察に基づく抽出（2024-006538 で確認）:
      - 「特許 出願YYYY-NNNNNN (YYYY/MM/DD) 出願種別(分割（44 条 1 項）) 遡及日(YYYY/MM/DD)」
      - 「原出願記事 関連種別(分割（４４条１項）) 特許 出願番号 YYYY-NNNNNN」
      - 「国内優先権記事 出願YYYY-NNNNNN 主張日(YYYY/MM/DD)」
      - 「拒絶理由通知（拒絶理由の引用文献情報） 起案日(YYYY/MM/DD)」
      - 「拒絶査定(拒絶査定時の文献) 起案日(YYYY/MM/DD)」
      - 「前置報告(前置報告時の文献) 起案日(YYYY/MM/DD)」
    """
    plain = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S)
    plain = re.sub(r"<style[^>]*>.*?</style>", " ", plain, flags=re.S)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = plain.replace("&nbsp;", " ").replace("&amp;", "&")
    plain = re.sub(r"\s+", " ", plain)

    out: dict = {
        "filing_date": None,           # 本願出願日
        "sokyu_date": None,            # 遡及日（最先出願日）
        "parent_appno": None,          # 直接の親出願番号
        "is_divisional": False,        # 44条1項分割か
        "shutsugan_shubetsu": None,    # 出願種別表記
        "kokunai_yusen": None,         # 国内優先権主張番号
        "kokunai_yusen_date": None,    # 国内優先権主張日
        "kyozetsu_riyu_kian": [],      # 拒絶理由通知書 起案日リスト
        "kyozetsu_satei_kian": None,   # 拒絶査定 起案日
        "zenchi_houkoku_kian": None,   # 前置報告書 起案日
    }

    # 出願種別
    m = re.search(r"出願種別\(([^)]+)\)", plain)
    if m:
        out["shutsugan_shubetsu"] = m.group(1).strip()
        if "44" in m.group(1) or "４４" in m.group(1):
            out["is_divisional"] = True

    # 本願出願日: 「特許 出願YYYY-NNNNNN (YYYY/MM/DD)」
    m = re.search(r"特許[\s　]*出願\d{4}-\d+[\s　]*\((\d{4}/\d{1,2}/\d{1,2})\)", plain)
    if m:
        out["filing_date"] = m.group(1).replace("/", "-")
        # ゼロ詰め
        parts = out["filing_date"].split("-")
        out["filing_date"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"

    # 遡及日（最先出願日）
    m = re.search(r"遡及日\((\d{4}/\d{1,2}/\d{1,2})\)", plain)
    if m:
        parts = m.group(1).split("/")
        out["sokyu_date"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"

    # 原出願記事 → 親出願番号
    # 「原出願記事 関連種別(分割（４４条１項）) 特許 出願番号 YYYY-NNNNNN」
    m = re.search(
        r"原出願記事.*?関連種別\([^)]*分割[^)]*\).*?出願番号[\s　]*(\d{4}-\d+)",
        plain
    )
    if m:
        out["parent_appno"] = appno_to_10(m.group(1))

    # 国内優先権記事
    m = re.search(r"国内優先権記事[\s　]*[特許実用新案]+[\s　]*出願(\d{4}-\d+)[\s　]*主張日\((\d{4}/\d{1,2}/\d{1,2})\)", plain)
    if m:
        out["kokunai_yusen"] = m.group(1)
        parts = m.group(2).split("/")
        out["kokunai_yusen_date"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"

    # 引用調査データの起案日（複数）
    # パターン: 「拒絶理由通知（拒絶理由の引用文献情報） 起案日(YYYY/MM/DD)」
    for m in re.finditer(r"拒絶理由[通知の引用]*[（(]?[^)]*[)）]?[\s　]*起案日\((\d{4}/\d{1,2}/\d{1,2})\)", plain):
        parts = m.group(1).split("/")
        out["kyozetsu_riyu_kian"].append(
            f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        )
    m = re.search(r"拒絶査定[（(]?[^)]*[)）]?[\s　]*起案日\((\d{4}/\d{1,2}/\d{1,2})\)", plain)
    if m:
        parts = m.group(1).split("/")
        out["kyozetsu_satei_kian"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    m = re.search(r"前置報告[（(]?[^)]*[)）]?[\s　]*起案日\((\d{4}/\d{1,2}/\d{1,2})\)", plain)
    if m:
        parts = m.group(1).split("/")
        out["zenchi_houkoku_kian"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"

    return out


def build_jpp_chain(start_appno: str, page: Page, context: BrowserContext,
                    fetch_calls: list[int]) -> list[dict]:
    """本願から親を再帰的に追って系列を返す。"""
    chain: list[dict] = []
    visited: set[str] = set()
    current = start_appno
    while current and current not in visited and len(chain) < MAX_DEPTH:
        visited.add(current)
        time.sleep(SEARCH_GAP_SEC)
        keika = navigate_to_app_info(page, context, current)
        fetch_calls[0] += 1
        if keika is None:
            chain.append({"appno": current, "error": "出願情報タブ取得失敗"})
            break
        info = parse_app_info(keika.content())
        keika.close()
        info["appno"] = current
        chain.append(info)
        if info.get("parent_appno") and info.get("is_divisional"):
            current = info["parent_appno"]
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args)
    print(f"targets: {len(targets)}")

    fetch_calls = [0]
    log_rows: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP", viewport={"width": 1500, "height": 1200})
        page = context.new_page()

        for i, (case_key, appno) in enumerate(targets, 1):
            out_path = OUT_DIR / f"{appno}.json"
            if not args.force and out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                gens = len(existing.get("chain", [])) - 1
                print(f"  [{i:3d}/{len(targets)}] SK {case_key:15s} appno={appno}  generations={gens}")
                continue

            try:
                chain = build_jpp_chain(appno, page, context, fetch_calls)
            except Exception as e:
                chain = [{"appno": appno, "error": f"exception: {e}"}]
            rec = {"case_key": case_key, "appno": appno, "chain": chain}
            out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            gens = len(chain) - 1
            print(f"  [{i:3d}/{len(targets)}] OK {case_key:15s} appno={appno}  generations={gens}")
            log_rows.append({"case_key": case_key, "appno": appno, "generations": gens})

        browser.close()

    cols = ["case_key", "appno", "generations"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    print(f"\n=== summary ===")
    print(f"  fetch calls: {fetch_calls[0]}")


if __name__ == "__main__":
    main()
