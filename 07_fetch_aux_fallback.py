"""07_fetch_external_fallback.py — 補助ソースから全書類分の起案日／受領日を取得

主経路 (API) 不通時の **代替経路 (fallback)**。 1案件あたり以下を1セッションで取得:

  - 書類リスト（書類名・経過情報の表示日 = legalDate 相当）
  - Type A 庁書類 (拒絶理由通知書／拒絶査定／補正の却下の決定／特許査定／前置報告書)
    → リンクをクリック → 本文の「起案日」or「作成日」を抽出
  - Type B 提出書類 (意見書／手続補正書／審判請求書／上申書／応対記録／翻訳文)
    → table の td.date_width 列から日付を直接取得

入力:
  inventory/case_appno_map.tsv (case_key, appno)

出力:
  inventory/aux_dates/{appno}.json   ← 統合書類リスト
  inventory/aux_dates_log.tsv        ← 実行ログ

CLI:
  python 07_fetch_external_fallback.py                 # 全件
  python 07_fetch_external_fallback.py --limit 3       # パイロット
  python 07_fetch_external_fallback.py --appno 2018244177
  python 07_fetch_external_fallback.py --force         # 既存も再取得
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
    print("playwright not installed. pip install playwright && playwright install chromium",
          file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
INV_DIR = HERE / "inventory"
APPNO_MAP = INV_DIR / "case_appno_map.tsv"
OUT_DIR = INV_DIR / "aux_dates"
LOG_PATH = INV_DIR / "aux_dates_log.tsv"

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

ZEN_TR = str.maketrans("０１２３４５６７８９", "0123456789")
ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}

# Type A 庁書類: 本文に「起案日」or「作成日」あり。リンクをクリックして抽出
TYPE_A_LABELS = ["拒絶理由通知書", "拒絶査定", "補正の却下の決定", "特許査定", "前置報告書"]

# Type B 提出書類（必要なものだけ）: 経過テーブルから直接日付取得
TYPE_B_LABELS_INCLUDE = [
    "意見書",
    "審判請求書",
    "上申書",
    "応対記録",
    "面接記録",
    "翻訳文",
    "国内書面",
    "誤訳訂正",
]
# 手続補正書は「（方式）」を除外、「（自発・内容）」「（補正命令）」のみ採用
TYPE_B_HOSEI_LABELS = ["手続補正書"]


def is_type_a(name: str) -> bool:
    return any(lbl in name for lbl in TYPE_A_LABELS)


def is_type_b_include(name: str) -> bool:
    if any(lbl in name for lbl in TYPE_B_LABELS_INCLUDE):
        return True
    # 手続補正書 — 方式は除外
    if "手続補正書" in name and "方式" not in name:
        return True
    return False


def jp_date_to_iso(era: str, y_raw: str, m_raw: str, d_raw: str) -> str | None:
    try:
        y_raw = y_raw.translate(ZEN_TR).replace("元", "1")
        m_raw = m_raw.translate(ZEN_TR)
        d_raw = d_raw.translate(ZEN_TR)
        year = ERA_BASE[era] + int(y_raw)
        return f"{year:04d}-{int(m_raw):02d}-{int(d_raw):02d}"
    except Exception:
        return None


DATE_PATTERN = re.compile(
    r'(?:起案日|作成日)[\s　:：]*(令和|平成|昭和|大正)[\s　]*([\d０-９元]+)[\s　]*年'
    r'[\s　]*([\d０-９]+)[\s　]*月[\s　]*([\d０-９]+)[\s　]*日'
)


def extract_drafting_date(html: str) -> tuple[str | None, str]:
    """書類本文から『起案日』or『作成日』を抽出。"""
    m_anchor = re.search(r'<a\s+name="D_PAGE1"', html)
    search_text = html[m_anchor.end():] if m_anchor else html
    # 全文検索 (最初の 起案日/作成日 = 書類ヘッダの起案日)。
    # 旧実装は先頭 8000 字のみ探し、CSS/シェルが前置される審判段階書類
    # (当審拒絶理由通知書等) で起案日がウィンドウ外 (offset ~72000) になり取り逃していた。
    m = DATE_PATTERN.search(search_text)
    if not m:
        return None, "no 起案日/作成日 pattern"
    iso = jp_date_to_iso(m.group(1), m.group(2), m.group(3), m.group(4))
    return iso, "起案日" if "起案日" in m.group(0) else "作成日"


def appno_hyphenated(appno_10: str) -> str:
    return f"{appno_10[:4]}-{appno_10[4:]}" if len(appno_10) == 10 else appno_10


def slash_date_to_iso(s: str) -> str | None:
    """'2022/08/22' → '2022-08-22' """
    m = re.match(r'^\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*$', s)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def navigate_to_inquiry(page: Page) -> None:
    page.goto(TOP_URL, wait_until="networkidle", timeout=60000)
    time.sleep(SHORT_WAIT)
    page.locator("#cfc001_globalNav_item_0").click(timeout=10000)
    time.sleep(1)
    page.locator("#cfc001_globalNav_sub_item_0_0").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_selector("#p00_srchCondtn_txtDocNoInputNo0", timeout=15000)


def fetch_one(context: BrowserContext, page: Page, appno: str) -> dict:
    """1案件の全書類リスト + Type A 起案日を取得。"""
    rec: dict = {"appno": appno, "input": appno_hyphenated(appno),
                 "documents": [], "error": None}

    inp = page.locator("#p00_srchCondtn_txtDocNoInputNo0")
    inp.fill("")
    time.sleep(0.5)
    inp.fill(rec["input"])
    time.sleep(0.5)
    page.locator("#p00_searchBtn_btnDocInquiry").click(timeout=10000)
    page.wait_for_load_state("networkidle", timeout=30000)
    time.sleep(SHORT_WAIT)

    try:
        with context.expect_page(timeout=30000) as np_info:
            page.locator("#patentUtltyIntnlNumOnlyLst_tableView_progReferenceInfo0").click(timeout=10000)
        keika = np_info.value
        keika.wait_for_load_state("networkidle", timeout=60000)
        keika.wait_for_selector("td.date_width", timeout=30000)
        time.sleep(LONG_WAIT)
    except PWTimeoutError as e:
        rec["error"] = f"keika page open failed: {e}"
        return rec

    # Step 1: 経過テーブル全行スキャン
    # tr 単位で document_name と日付を抽出
    rows = keika.locator("table tr")
    n_rows = rows.count()

    # 日付カラム（td.date_width）の値を順に取得
    date_tds = keika.locator("td.date_width")
    n_date = date_tds.count()
    dates_list: list[str] = []
    for i in range(n_date):
        try:
            txt = date_tds.nth(i).inner_text(timeout=2000).strip()
            dates_list.append(txt)
        except Exception:
            dates_list.append("")

    # 行ごとに「リンク（書類名）」+ 「日付（td.date_width）」を抽出
    documents: list[dict] = []
    for i in range(n_rows):
        try:
            row_text = rows.nth(i).inner_text(timeout=2000).strip()
        except Exception:
            continue
        if not row_text:
            continue
        # 行内の date_width td を持つか
        date_in_row = rows.nth(i).locator("td.date_width")
        if date_in_row.count() == 0:
            continue
        date_text = date_in_row.first.inner_text(timeout=2000).strip()
        date_iso = slash_date_to_iso(date_text)

        # 書類名: 行内の最初のテキストセル
        # name は「{書類名}\t{日付}」形式の row_text から先頭を取り出す
        name = row_text.split("\n")[0].strip()
        # date が含まれていれば除去
        name = name.replace(date_text, "").strip()
        # tab 区切りの最後セルが日付なので前部を採用
        if "\t" in name:
            name = name.split("\t")[0].strip()

        documents.append({
            "name": name,
            "table_date": date_iso or date_text,  # 受領日 (legalDate) 相当
            "is_type_a": is_type_a(name),
            "is_type_b_target": is_type_b_include(name) and not is_type_a(name),
            "drafting_date": None,
            "drafting_date_label": None,
            "row_index": i,
        })

    # Step 2: Type A 書類はリンク click → 本文「起案日/作成日」抽出
    # ※ 同じラベルが複数ある場合は、行のクリックで対応する書類を開く必要
    for label in TYPE_A_LABELS:
        # この label にマッチする書類を全件処理
        matching = [d for d in documents if label in d["name"] and d["drafting_date"] is None]
        if not matching:
            continue
        # クリック対象: a:has-text(label) すべて
        a_locs = keika.locator(f'a:has-text("{label}")')
        n_a = a_locs.count()
        # n_a と matching の数が一致する想定（複数前置報告書など）
        for i in range(min(n_a, len(matching))):
            try:
                with context.expect_page(timeout=30000) as np2_info:
                    a_locs.nth(i).click(timeout=10000)
                doc_page = np2_info.value
                doc_page.wait_for_load_state("networkidle", timeout=60000)
                try:
                    doc_page.wait_for_function(
                        "document.body && document.body.innerHTML.includes('D_PAGE1')",
                        timeout=20000,
                    )
                except PWTimeoutError:
                    pass
                time.sleep(SHORT_WAIT)
                html = doc_page.content()
                d_iso, d_label = extract_drafting_date(html)
                # 診断: 起案日/作成日 抽出失敗時に本文を保存 (env AUX_DEBUG_SAVE_BODY=1)
                import os as _os
                if _os.environ.get("AUX_DEBUG_SAVE_BODY") and d_iso is None:
                    _dbg = INV_DIR / "aux_debug"
                    _dbg.mkdir(parents=True, exist_ok=True)
                    _ri = matching[i].get("row_index", i) if i < len(matching) else i
                    (_dbg / f"{appno}_row{_ri}.html").write_text(html, encoding="utf-8")
                # 一致する書類エントリにセット
                if i < len(matching):
                    matching[i]["drafting_date"] = d_iso
                    matching[i]["drafting_date_label"] = d_label
                doc_page.close()
            except Exception as e:
                if i < len(matching):
                    matching[i]["drafting_date"] = None
                    matching[i]["drafting_date_label"] = f"error: {str(e)[:60]}"

    keika.close()
    rec["documents"] = documents
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
        context = browser.new_context(locale="ja-JP", viewport={"width": 1400, "height": 1200})
        page = context.new_page()
        try:
            navigate_to_inquiry(page)
        except Exception as e:
            sys.exit(f"initial nav failed: {e}")

        for i, (case_key, appno) in enumerate(targets, 1):
            out_path = OUT_DIR / f"{appno}.json"
            if not args.force and out_path.exists():
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                ndocs = len(existing.get("documents", []))
                ntype_a = sum(1 for d in existing["documents"] if d.get("drafting_date"))
                print(f"  [{i:3d}/{len(targets)}] SK {case_key:15s} appno={appno}  ndocs={ndocs} typeA_dates={ntype_a}")
                continue

            time.sleep(SEARCH_GAP_SEC)
            try:
                rec = fetch_one(context, page, appno)
            except Exception as e:
                rec = {"appno": appno, "documents": [], "error": f"exception: {type(e).__name__}: {e}"}
            rec["case_key"] = case_key
            out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            ndocs = len(rec.get("documents", []))
            ntype_a = sum(1 for d in rec.get("documents", []) if d.get("drafting_date"))
            err = rec.get("error", "") or ""
            print(f"  [{i:3d}/{len(targets)}] OK {case_key:15s} appno={appno}  ndocs={ndocs} typeA_dates={ntype_a}  err={err[:50]}")
            log_rows.append({"case_key": case_key, "appno": appno, "ndocs": ndocs, "typeA_dates": ntype_a, "err": err})

            try:
                navigate_to_inquiry(page)
            except Exception as e:
                print(f"  ! nav reset failed: {e}")
                break

        browser.close()

    cols = ["case_key", "appno", "ndocs", "typeA_dates", "err"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in log_rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    print(f"\n=== summary ===")
    print(f"  total: {len(log_rows)}")
    print(f"  log: {LOG_PATH}")


if __name__ == "__main__":
    main()
