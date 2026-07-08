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

    result.update(ensure_aux_sources(appno, inv_dir))

    result["elapsed_sec"] = round(time.time() - t0, 2)
    return result


# ============================================================================
# 補助ソース (06 zenchi / 07 aux) の必要時取得
# ============================================================================

def _doc_history_codes(appno: str, inv_dir: Path) -> set[str]:
    """doc_history_collected/{appno}.json に現れる documentCode の集合。"""
    p = inv_dir / "doc_history_collected" / f"{appno}.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    codes: set[str] = set()
    biblio = data.get("result", {}).get("data", {}).get("bibliographyInformation", []) or []
    for b in biblio:
        for d in b.get("documentList", []) or []:
            code = d.get("documentCode", "")
            if code:
                codes.add(code)
    return codes


def _probe_incomplete(p: Path) -> bool:
    """取得記録ファイルはあるが外部照会が完走していない (= 再試行対象) か。

    06 (zenchi): found_keika False = 経過参照ページに到達できていない
    07 (aux):    documents 空 + error あり = 例外中断の書き残し
    """
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if "found_keika" in rec or "found_zenchi_link" in rec:
        return not rec.get("found_keika")
    return bool(rec.get("error")) and not rec.get("documents")


def _run_aux_script(script_name: str, appno: str, timeout: int, force: bool) -> tuple[bool, str]:
    import subprocess as _sp
    import sys as _sys
    keii_pub_root = Path(__file__).resolve().parent.parent
    cmd = [_sys.executable, str(keii_pub_root / script_name), "--appno", appno]
    if force:
        cmd.append("--force")
    proc = _sp.run(cmd, capture_output=True, text=True, timeout=timeout,
                   cwd=str(keii_pub_root))
    return proc.returncode == 0, (proc.stderr or "")[-300:]


def ensure_aux_sources(appno: str, inventory_dir: Path | str | None = None) -> dict:
    """補助ソース由来の日付データを、必要な案件に限り確保する。

    - zenchi (06): doc_history に A913 (前置報告書) があるとき。
      前置報告書の「作成日」は JPO API 本文配信対象外 (legalDate は発送日) のため。
    - aux (07): doc_history に C13 (当審拒絶理由通知書) がある、または zenchi 記録に
      found_errata_link が立っているとき。C13 の起案日も API では発送日しか取れない。

    取得記録ファイルが既にあり照会が完走していれば外部アクセスしない。
    未完走の書き残し (ブラウザ死亡・例外中断) は --force で再試行する。
    失敗しても例外は上げず、結果 dict に記録して返す (呼び出し側の生成は続行)。
    LIEVITO_OFFLINE_MODE=1 のときは外部アクセスを完全にスキップする
    (進化ループの corpus 評価・FB 到達判定フェーズで使用。 補助ソース外部規約への
     配慮および評価時のデータ不変性を保つため)。
    """
    import os as _os
    if inventory_dir is None:
        inventory_dir = Path(__file__).resolve().parent.parent / "inventory"
    inv_dir = Path(inventory_dir)
    _offline = _os.environ.get("LIEVITO_OFFLINE_MODE", "").strip() in ("1", "true", "True", "yes")
    result: dict[str, Any] = {}

    codes = _doc_history_codes(appno, inv_dir)

    # 06: 前置報告書の作成日
    zenchi_p = inv_dir / "zenchi_drafting" / f"{appno}.json"
    if "A913" not in codes:
        result["zenchi_fetched"] = zenchi_p.exists()
        result["zenchi_skipped"] = "A913 なし (前置報告書が存在しない案件)"
    elif zenchi_p.exists() and not _probe_incomplete(zenchi_p):
        result["zenchi_fetched"] = True
    elif _offline:
        result["zenchi_fetched"] = False
        result["zenchi_skipped"] = "offline mode"
    else:
        try:
            ok, err = _run_aux_script("06_fetch_zenchi_drafting.py", appno,
                                      timeout=180, force=zenchi_p.exists())
            result["zenchi_fetched"] = (ok and zenchi_p.exists())
            if not ok and err:
                result["zenchi_error"] = err
        except Exception as e:
            result["zenchi_fetched"] = False
            result["zenchi_error"] = f"{type(e).__name__}: {e}"

    # 07: 全書類の起案日/受領日 (C13 起案日と誤訳訂正書の fallback に使用)
    aux_needed = "C13" in codes
    if not aux_needed and zenchi_p.exists():
        try:
            zd = json.loads(zenchi_p.read_text(encoding="utf-8"))
            aux_needed = bool(zd.get("found_errata_link"))
        except (OSError, json.JSONDecodeError):
            pass
    if aux_needed:
        aux_p = inv_dir / "aux_dates" / f"{appno}.json"
        if aux_p.exists() and not _probe_incomplete(aux_p):
            result["aux_fetched"] = True
        elif _offline:
            result["aux_fetched"] = False
            result["aux_skipped"] = "offline mode"
        else:
            try:
                ok, err = _run_aux_script("07_fetch_aux_fallback.py", appno,
                                          timeout=240, force=aux_p.exists())
                result["aux_fetched"] = (ok and aux_p.exists())
                if not ok and err:
                    result["aux_error"] = err
            except Exception as e:
                result["aux_fetched"] = False
                result["aux_error"] = f"{type(e).__name__}: {e}"

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
