"""fetcher/onboard_appno.py — 1 案件分の inventory を JPO API のみで構築。

オンデマンド fetch 用のオーケストレータ。既存の 04/05/08 のコアロジックを
1 案件分に圧縮したもの。補助ソース 系 (06/10) は含まないため、それらに
依存するパターン (一部の優先権・分割) は未取得情報として扱われる。

公開 API:
  onboard_appno(appno, inventory_dir=None) -> dict

戻り値:
  {
    "appno": str,
    "status": "ok" | "doc_history_missing" | "error",
    "doc_history": str | None,         # 取得済みファイルの絶対パス
    "doc_xmls_summary": str | None,
    "parent_chain": str | None,
    "elapsed_sec": float,
    "error": str | None,
  }
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib import error, request

from .probe_jpo_api import api_get, get_access_token


API_BASE = "https://ip-data.jpo.go.jp/api/patent/v1"
ENDPOINTS_DOC = [
    ("outbound", "app_doc_cont_refusal_reason_decision/{appno}"),
    ("inbound", "app_doc_cont_opinion_amendment/{appno}"),
]
SLEEP_SEC = 0.7
MAX_PARENT_DEPTH = 15

_DRAFTING_DATE_RE = re.compile(r"<jp:drafting-date>\s*<jp:date>(\d{8})</jp:date>", re.S)
_DOC_NAME_RE = re.compile(r"<jp:document-name>([^<]+)</jp:document-name>")
_DRAFT_NAME_RE = re.compile(r"<jp:draft-person-group>\s*<jp:name>([^<]+)</jp:name>", re.S)
_DRAFT_CODE_RE = re.compile(r"<jp:draft-person-group>.*?<jp:staff-code>([^<]*)</jp:staff-code>", re.S)
_BODY_RE = re.compile(r"<jp:drafting-body>(.*?)</jp:drafting-body>", re.S)
_LEGAL_DATE_RE = re.compile(r"<jp:legal-date>\s*<jp:date>(\d{8})</jp:date>", re.S)
_RECEIPT_DATE_RE = re.compile(r"<receipt-date>\s*<date>(\d{8})</date>", re.S)


def _parse_xml_meta(xml_bytes: bytes) -> dict:
    try:
        text = xml_bytes.decode("shift_jis", errors="replace")
    except Exception:
        text = xml_bytes.decode("latin-1", errors="replace")
    out: dict = {}
    for key, rx in (
        ("document_name", _DOC_NAME_RE),
        ("drafting_date", _DRAFTING_DATE_RE),
        ("draft_person_name", _DRAFT_NAME_RE),
        ("draft_person_code", _DRAFT_CODE_RE),
        ("xml_legal_date", _LEGAL_DATE_RE),
        ("receipt_date", _RECEIPT_DATE_RE),
    ):
        m = rx.search(text)
        if m:
            out[key] = m.group(1).strip()
    m = _BODY_RE.search(text)
    if m:
        plain = re.sub(r"<[^>]+>", "", m.group(1))
        plain = re.sub(r"\s+", " ", plain).strip()
        out["body_excerpt"] = plain[:500]
    return out


def _fetch_binary(url: str, token: str) -> tuple[int, bytes]:
    req = request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except error.HTTPError as e:
        return e.code, b""
    except Exception:
        return -1, b""


def _fetch_doc_history(appno: str, token: str, inv_dir: Path) -> Path | None:
    out = inv_dir / "doc_history_collected" / f"{appno}.json"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    time.sleep(SLEEP_SEC)
    r = api_get(f"app_progress/{appno}", token)
    sc = r.get("result", {}).get("statusCode", "")
    if sc != "100":
        return None
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _fetch_doc_xmls(appno: str, token: str, inv_dir: Path) -> Path | None:
    out_dir = inv_dir / "doc_xmls" / appno
    summary = out_dir / "_summary.json"
    if summary.exists():
        return summary
    out_dir.mkdir(parents=True, exist_ok=True)
    documents: list[dict] = []
    for ep_name, ep_path in ENDPOINTS_DOC:
        time.sleep(SLEEP_SEC)
        url = f"{API_BASE}/{ep_path.format(appno=appno)}"
        status, body = _fetch_binary(url, token)
        if status != 200 or not body or not zipfile.is_zipfile(io.BytesIO(body)):
            continue
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for name in z.namelist():
                if not name.lower().endswith(".xml"):
                    continue
                xml = z.read(name)
                parts = name.split("/")
                if len(parts) > 1:
                    out_name = f"{ep_name}__{parts[0]}__{parts[-1]}"
                    doc_number = parts[0]
                else:
                    out_name = f"{ep_name}__{parts[-1]}"
                    m = re.match(r"(\d+)[\-_]", parts[-1])
                    doc_number = m.group(1) if m else parts[-1].replace(".xml", "")
                (out_dir / out_name).write_bytes(xml)
                meta = _parse_xml_meta(xml)
                meta.update({
                    "endpoint": ep_name, "doc_number": doc_number,
                    "xml_filename": out_name, "zip_member": name,
                })
                documents.append(meta)
    summary.write_text(
        json.dumps({"appno": appno, "documents": documents}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _fetch_parent_chain(appno: str, token: str, inv_dir: Path) -> Path | None:
    """本願の親チェーンを辿り、各親の doc_history も併せて inventory に保存。

    parentApplicationInformation['parentApplicationNumber'] は dict ですが、
    実体は単数。{parent_appno → さらに祖父…} と再帰し、各親について
    doc_history_collected も保存する (有/無で apptype 判定が変わるため)。
    """
    out = inv_dir / "parent_chains" / f"{appno}.json"
    if out.exists():
        # 念のため既存だけでなく、parent 各々の doc_history も補完取得
        try:
            chain = json.loads(out.read_text(encoding="utf-8")).get("chain", [])
            for entry in chain:
                a = entry.get("appno", "")
                if a and a != appno:
                    dh_p = inv_dir / "doc_history_collected" / f"{a}.json"
                    if not dh_p.exists():
                        time.sleep(SLEEP_SEC)
                        r = api_get(f"app_progress/{a}", token)
                        if r.get("result", {}).get("statusCode") == "100":
                            dh_p.parent.mkdir(parents=True, exist_ok=True)
                            dh_p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    chain: list[dict[str, Any]] = []
    cur = appno
    seen: set[str] = set()
    for _ in range(MAX_PARENT_DEPTH):
        if cur in seen:
            break
        seen.add(cur)
        dh = inv_dir / "doc_history_collected" / f"{cur}.json"
        if dh.exists():
            data = json.loads(dh.read_text(encoding="utf-8")).get("result", {}).get("data", {}) or {}
        else:
            time.sleep(SLEEP_SEC)
            r = api_get(f"app_progress/{cur}", token)
            if r.get("result", {}).get("statusCode") != "100":
                break
            data = r["result"]["data"]
            # 親の doc_history も保存 (cur != 本願 のときに保存)
            if cur != appno:
                dh.parent.mkdir(parents=True, exist_ok=True)
                dh.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        parent_info = data.get("parentApplicationInformation") or []
        parent_appno = (parent_info[0].get("parentApplicationNumber") if parent_info else None) or ""
        chain.append({
            "appno": cur,
            "filingDate": data.get("filingDate", ""),
            "parent_appno": parent_appno,
            "is_pct_national_phase": bool(data.get("internationalApplicationNumber")),
        })
        if not parent_appno:
            break
        cur = parent_appno
    out.write_text(
        json.dumps({"appno": appno, "chain": chain}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def onboard_appno(appno: str, inventory_dir: Path | str | None = None) -> dict:
    if inventory_dir is None:
        inventory_dir = Path(__file__).resolve().parent.parent / "inventory"
    inv_dir = Path(inventory_dir)
    inv_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "appno": appno, "status": "ok",
        "doc_history": None, "doc_xmls_summary": None, "parent_chain": None,
        "elapsed_sec": 0.0, "error": None,
    }
    t0 = time.time()
    try:
        token = get_access_token()
        p = _fetch_doc_history(appno, token, inv_dir)
        if p is None:
            result["status"] = "doc_history_missing"
            result["error"] = "app_progress returned no data (statusCode != 100)"
            result["elapsed_sec"] = round(time.time() - t0, 2)
            return result
        result["doc_history"] = str(p)

        p = _fetch_doc_xmls(appno, token, inv_dir)
        if p is not None:
            result["doc_xmls_summary"] = str(p)

        p = _fetch_parent_chain(appno, token, inv_dir)
        if p is not None:
            result["parent_chain"] = str(p)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"

    # 前置報告書の作成日 (補助ソース 経由)。失敗しても onboard 全体を落とさない。
    zenchi_p = inv_dir / "zenchi_drafting" / f"{appno}.json"
    if not zenchi_p.exists():
        try:
            import subprocess as _sp
            import sys as _sys
            keii_pub_root = Path(__file__).resolve().parent.parent
            proc = _sp.run(
                [_sys.executable, str(keii_pub_root / "06_fetch_zenchi_drafting.py"),
                 "--appno", appno],
                capture_output=True, text=True, timeout=120,
                cwd=str(keii_pub_root),
            )
            result["zenchi_fetched"] = (proc.returncode == 0 and zenchi_p.exists())
        except Exception as e:
            result["zenchi_fetched"] = False
            result["zenchi_error"] = f"{type(e).__name__}: {e}"
    else:
        result["zenchi_fetched"] = True

    # 06 の結果で「誤訳訂正書あり」フラグが立っていれば、補助ソース全書類取得
    # スクリプト (07) を呼び出して fallback 経路でも書類日付を確保しておく。
    # dates.py の get_doc_dates_with_source が誤訳訂正書のみ補完に使う。
    try:
        if zenchi_p.exists():
            import json as _json
            zd = _json.loads(zenchi_p.read_text(encoding="utf-8"))
            if zd.get("found_errata_link"):
                aux_p = inv_dir / "aux_dates" / f"{appno}.json"
                if not aux_p.exists():
                    import subprocess as _sp
                    import sys as _sys
                    keii_pub_root = Path(__file__).resolve().parent.parent
                    proc = _sp.run(
                        [_sys.executable, str(keii_pub_root / "07_fetch_aux_fallback.py"),
                         "--appno", appno],
                        capture_output=True, text=True, timeout=180,
                        cwd=str(keii_pub_root),
                    )
                    result["aux_fetched"] = (proc.returncode == 0 and aux_p.exists())
                else:
                    result["aux_fetched"] = True
    except Exception as e:
        result["aux_error"] = f"{type(e).__name__}: {e}"

    result["elapsed_sec"] = round(time.time() - t0, 2)
    return result


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("appno")
    ap.add_argument("--inventory-dir", default=None)
    args = ap.parse_args()
    r = onboard_appno(args.appno, args.inventory_dir)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
