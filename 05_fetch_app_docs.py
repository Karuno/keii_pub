"""05_fetch_app_docs.py — JPO API から書類本体XMLを一括取得

エンドポイント:
  app_doc_cont_refusal_reason_decision/{appno}   庁作成書類（拒絶理由通知書・拒絶査定 等）
  app_doc_cont_opinion_amendment/{appno}         提出書類（意見書・補正書 等）

各書類の XML から抽出するフィールド:
  document_name        書類名
  drafting_date        起案日（庁作成書類のみ。Type A の primary source）
  doc_number           書類番号
  draft_person_name    起案者名
  draft_person_code    起案者スタッフコード
  body_excerpt         本文の冒頭500字（50条の2 や （最後）等の検出用）

出力:
  inventory/doc_xmls/{appno}/{document_number}.xml      原本 XML
  inventory/doc_xmls/{appno}/_summary.json              書類リスト + 抽出フィールド
  inventory/doc_xmls_log.tsv                            取得ログ

CLI:
  python 05_fetch_app_docs.py                  # 全 44 案件
  python 05_fetch_app_docs.py --limit 3        # パイロット
  python 05_fetch_app_docs.py --appno 2018244177  # 1件指定
  python 05_fetch_app_docs.py --force          # 既存も再取得
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
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
XMLS_DIR = INV_DIR / "doc_xmls"
LOG_PATH = INV_DIR / "doc_xmls_log.tsv"

API_BASE = "https://ip-data.jpo.go.jp/api/patent/v1"
ENDPOINTS = [
    ("outbound", f"{API_BASE}/app_doc_cont_refusal_reason_decision/{{appno}}"),
    ("inbound",  f"{API_BASE}/app_doc_cont_opinion_amendment/{{appno}}"),
]
SLEEP_SEC = 0.7

# === XML フィールド抽出 ===

DRAFTING_DATE_RE = re.compile(r"<jp:drafting-date>\s*<jp:date>(\d{8})</jp:date>", re.S)
DOC_NAME_RE = re.compile(r"<jp:document-name>([^<]+)</jp:document-name>")
DRAFT_NAME_RE = re.compile(r"<jp:draft-person-group>\s*<jp:name>([^<]+)</jp:name>", re.S)
DRAFT_CODE_RE = re.compile(r"<jp:draft-person-group>.*?<jp:staff-code>([^<]*)</jp:staff-code>", re.S)
BODY_RE = re.compile(r"<jp:drafting-body>(.*?)</jp:drafting-body>", re.S)
LEGAL_DATE_RE = re.compile(r"<jp:legal-date>\s*<jp:date>(\d{8})</jp:date>", re.S)
RECEIPT_DATE_RE = re.compile(r"<receipt-date>\s*<date>(\d{8})</date>", re.S)


def parse_xml_meta(xml_bytes: bytes) -> dict:
    """XML から起案日・書類名・本文サンプル等を抽出。"""
    # JPO の XML は Shift_JIS。デコードに失敗したら latin1 で fallback（バイト保持）
    try:
        text = xml_bytes.decode("shift_jis", errors="replace")
    except Exception:
        text = xml_bytes.decode("latin-1", errors="replace")

    out: dict = {}
    if m := DOC_NAME_RE.search(text):
        out["document_name"] = m.group(1).strip()
    if m := DRAFTING_DATE_RE.search(text):
        out["drafting_date"] = m.group(1)
    if m := DRAFT_NAME_RE.search(text):
        out["draft_person_name"] = m.group(1).strip()
    if m := DRAFT_CODE_RE.search(text):
        out["draft_person_code"] = m.group(1).strip()
    if m := LEGAL_DATE_RE.search(text):
        out["xml_legal_date"] = m.group(1)
    if m := RECEIPT_DATE_RE.search(text):
        out["receipt_date"] = m.group(1)

    # 本文（drafting-body） — 提出書類は jp:drafting-body がない場合あり
    if m := BODY_RE.search(text):
        body = m.group(1)
        # XML タグを除去して text-only に
        plain = re.sub(r"<[^>]+>", "", body)
        plain = re.sub(r"\s+", " ", plain).strip()
        out["body_excerpt"] = plain[:500]

    return out


# === 案件 → appno マップ ===

def load_unique_targets() -> list[tuple[str, str]]:
    """case_appno_map.tsv から (case_key, appno) を返す。"""
    if not APPNO_MAP.exists():
        print(f"appno map not found: {APPNO_MAP}", file=sys.stderr)
        sys.exit(1)
    seen: dict[str, str] = {}
    for line in APPNO_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        case_key, appno, _src, status = cols[0], cols[1], cols[2], cols[3]
        if status == "ok" and appno and case_key not in seen:
            seen[case_key] = appno
    return sorted(seen.items())


# === 1 案件処理 ===

def fetch_zip(appno: str, endpoint_url: str, token: str) -> tuple[int, bytes, str]:
    """ZIP を取得。`api_get` のラッパでなく urllib 直で（バイナリ用）。"""
    from urllib import request, error
    req = request.Request(endpoint_url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except error.HTTPError as e:
        return e.code, b"", str(e.reason)
    except Exception as e:
        return -1, b"", str(e)


def process_one(case_key: str, appno: str, token: str, force: bool) -> dict:
    """1 案件分の書類XMLを取得・展開・サマリ化。"""
    out_dir = XMLS_DIR / appno
    summary_path = out_dir / "_summary.json"

    record: dict = {
        "case_key": case_key, "appno": appno,
        "outbound_status": "", "inbound_status": "",
        "n_xmls": 0, "n_with_drafting_date": 0,
        "remain_after": "",
        "skipped": False, "summary_path": "",
    }

    if not force and summary_path.exists():
        record["skipped"] = True
        record["summary_path"] = str(summary_path.relative_to(HERE))
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
            record["n_xmls"] = len(existing.get("documents", []))
            record["n_with_drafting_date"] = sum(1 for d in existing.get("documents", []) if d.get("drafting_date"))
        except Exception:
            pass
        return record

    out_dir.mkdir(parents=True, exist_ok=True)
    documents: list[dict] = []

    for ep_name, ep_url in ENDPOINTS:
        time.sleep(SLEEP_SEC)
        url = ep_url.format(appno=appno)
        status, body, ct = fetch_zip(appno, url, token)
        record[f"{ep_name}_status"] = str(status)

        if status != 200 or not body:
            continue

        # ZIP の場合
        if not zipfile.is_zipfile(io.BytesIO(body)):
            # JSON エラーレスポンスの場合
            try:
                err_json = json.loads(body.decode("utf-8", errors="replace"))
                remain = err_json.get("result", {}).get("remainAccessCount", "")
                if remain:
                    record["remain_after"] = remain
            except Exception:
                pass
            continue

        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for name in z.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                xml_bytes = z.read(name)
                # ZIP 内ディレクトリ名（A523_NNNNN 等）も保存名に含めて衝突回避
                # 例:
                #   outbound: 06124222264-jpntce.xml      → outbound__06124222264-jpntce.xml
                #   inbound : A523_52400844166/JPOXMLDOC01-jpbibl.xml
                #             → inbound__A523_52400844166__JPOXMLDOC01-jpbibl.xml
                parts = name.split("/")
                if len(parts) > 1:
                    out_name = f"{ep_name}__{parts[0]}__{parts[-1]}"
                    doc_number = parts[0]  # A523_NNNNN
                else:
                    out_name = f"{ep_name}__{parts[-1]}"
                    m = re.match(r"(\d+)[\-_]", parts[-1])
                    doc_number = m.group(1) if m else parts[-1].replace(".xml", "")
                out_xml = out_dir / out_name
                out_xml.write_bytes(xml_bytes)

                meta = parse_xml_meta(xml_bytes)
                meta.update({
                    "endpoint": ep_name,
                    "doc_number": doc_number,
                    "xml_filename": out_name,
                    "zip_member": name,
                })
                documents.append(meta)

    record["n_xmls"] = len(documents)
    record["n_with_drafting_date"] = sum(1 for d in documents if d.get("drafting_date"))

    # サマリ JSON 出力
    summary = {
        "case_key": case_key,
        "appno": appno,
        "documents": documents,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    record["summary_path"] = str(summary_path.relative_to(HERE))
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--appno", default=None, help="単一出願番号で実行")
    ap.add_argument("--force", action="store_true", help="既存も再取得")
    args = ap.parse_args()

    XMLS_DIR.mkdir(parents=True, exist_ok=True)

    if args.appno:
        targets = [("(single)", args.appno)]
    else:
        targets = load_unique_targets()
        if args.limit > 0:
            targets = targets[: args.limit]

    print(f"targets: {len(targets)}")

    # API 残量チェック（10アクセスバッファ）。1件あたり 2 endpoint = 2 calls
    n_planned_calls = sum(2 for c, a in targets
                          if args.force or not (XMLS_DIR / a / "_summary.json").exists())
    check_remain_or_abort(planned_calls=n_planned_calls)

    print("acquiring token...")
    token = get_access_token()
    print(f"token: {token[:18]}...")

    records: list[dict] = []
    for i, (case_key, appno) in enumerate(targets, 1):
        rec = process_one(case_key, appno, token, args.force)
        records.append(rec)
        marker = "SK" if rec["skipped"] else "OK"
        print(f"  [{i:3d}/{len(targets)}] {marker} {case_key:15s} appno={appno}  "
              f"out={rec['outbound_status']}  in={rec['inbound_status']}  "
              f"n_xmls={rec['n_xmls']}  draft_dates={rec['n_with_drafting_date']}")
        # remain 更新
        ra = rec.get("remain_after", "")
        if ra:
            try:
                ra_int = int(ra)
                update_remain(ra_int)
                if ra_int <= 10:
                    print(f"\n** STOP: 残量 {ra_int} <= 10 (バッファ)。 **")
                    break
            except Exception:
                pass

    # ログ出力
    cols = ["case_key", "appno", "outbound_status", "inbound_status",
            "n_xmls", "n_with_drafting_date", "skipped", "summary_path", "remain_after"]
    with LOG_PATH.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in records:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    n_ok = sum(1 for r in records if r["n_xmls"] > 0)
    n_dates = sum(r["n_with_drafting_date"] for r in records)
    print(f"\n=== summary ===")
    print(f"  cases with XMLs: {n_ok}/{len(records)}")
    print(f"  total drafting_dates extracted: {n_dates}")
    print(f"  log: {LOG_PATH}")


if __name__ == "__main__":
    main()
