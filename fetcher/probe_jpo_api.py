#!/usr/bin/env python3
"""
JPO API エンドポイント探索プロトタイプ
- 既知のエンドポイントでデータ構造を確認
- ファミリー・分割情報の取得可否を調査

認証情報は外部ディレクトリの設定ファイルから実行時に読み込む。
本ファイルには平文の資格情報を一切含めない（絶対）。

設定:
  環境変数 `JPO_API_DIR` で認証情報ディレクトリを指定できる。
  未設定時はデフォルト（作者ローカル）を使用（後方互換のため維持）。
  ディレクトリ配下に jpo_api_cred.json（キー: username, password）を置く。
"""
import json, os, sys, time, re
from pathlib import Path
from urllib import request, error
import urllib.parse

# 環境変数 JPO_API_DIR で上書き可能。未設定時のデフォルトは作者ローカル環境。
JPO_API_DIR = Path(os.environ.get("JPO_API_DIR", r"G:\マイドライブ\pg\AI_examiner\jpo_api"))
TOKEN_URL = "https://ip-data.jpo.go.jp/auth/token"
TOKEN_PATH = JPO_API_DIR / "jpo_api_token.json"


def _read_credentials() -> tuple[str, str]:
    """JPO APIの認証情報を外部ファイルから読み出す。

    このコードには平文の資格情報を書かない。
    参照元は pg/AI_examiner/jpo_api/ 配下のみ。
    """
    cred_path = JPO_API_DIR / "jpo_api_cred.json"
    if cred_path.exists():
        cred = json.loads(cred_path.read_text(encoding="utf-8"))
        return cred["username"], cred["password"]
    bat = (JPO_API_DIR / "get_token.bat").read_text(encoding="utf-8", errors="ignore")
    m_user = re.search(r'username=(\S+?)"', bat)
    m_pass = re.search(r'password=(\S+?)"', bat)
    if not m_user or not m_pass:
        raise RuntimeError(
            f"JPO API credentials not found under {JPO_API_DIR}. "
            "Place jpo_api_cred.json with keys 'username' and 'password'."
        )
    return m_user.group(1), m_pass.group(1)


def get_access_token() -> str:
    """JPO APIのBearerトークンを取得（有効なら再利用、期限切れならリフレッシュ）"""
    if TOKEN_PATH.exists():
        j = json.loads(TOKEN_PATH.read_text(encoding="utf-8", errors="ignore"))
        tok = j.get("access_token")
        exp = j.get("expires_in", 0)
        mtime = int(TOKEN_PATH.stat().st_mtime)
        if tok and (mtime + int(exp) - int(time.time())) > 120:
            return tok
    # リフレッシュ
    username, password = _read_credentials()
    body = urllib.parse.urlencode({
        "grant_type": "password",
        "username": username,
        "password": password,
    }).encode()
    req = request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed: HTTP {e.code} {e.reason}") from e
    TOKEN_PATH.write_text(raw, encoding="utf-8")
    return json.loads(raw)["access_token"]

API_BASE = "https://ip-data.jpo.go.jp/api/patent/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def api_get(endpoint: str, token: str) -> dict:
    """GETリクエスト。JSONを返す。ZIPの場合はステータスのみ返す。"""
    url = f"{API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": UA,
        "Accept": "application/json",
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=20) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct:
                return json.loads(resp.read().decode("utf-8"))
            else:
                return {"_note": f"non-JSON response: {ct}", "_size": len(resp.read())}
    except error.HTTPError as e:
        return {"_error": e.code, "_reason": str(e.reason), "_url": url}
    except Exception as e:
        return {"_error": str(e), "_url": url}

def main():
    # テスト用出願番号（案件11: 特願2021-045847）
    APP = "2021045847"
    # 分割出願（特願2024-198840）
    APP_DIV = "2024198840"

    token = get_access_token()
    print(f"Token acquired: {token[:20]}...")

    # ---- 既知のエンドポイント ----
    known_endpoints = {
        "app_progress": f"app_progress/{APP}",
        "aux_fixed_address": f"aux_fixed_address/{APP}",
    }

    # ---- 推定エンドポイント（JPO APIドキュメントから推測）----
    guess_endpoints = {
        # 書誌情報系
        "app_biblio": f"app_bibliographic_info/{APP}",
        "bibliographic": f"bibliographic/{APP}",
        "bibliography": f"bibliography/{APP}",
        # ファミリー・分割系
        "family": f"family/{APP}",
        "app_family": f"app_family/{APP}",
        "patent_family": f"patent_family/{APP}",
        "division": f"division/{APP}",
        "app_division": f"app_division/{APP}",
        "related_app": f"related_application/{APP}",
        # 経過情報系
        "app_status": f"app_status/{APP}",
        "prosecution": f"prosecution_history/{APP}",
        "legal_status": f"legal_status/{APP}",
        # 登録情報系
        "registration": f"registration/{APP}",
        "patent_right": f"patent_right/{APP}",
        "granted": f"granted/{APP}",
        # 請求項
        "claims": f"claims/{APP}",
        "app_claims": f"app_claims/{APP}",
    }

    results = {}
    print("\n=== Known Endpoints ===")
    for name, ep in known_endpoints.items():
        time.sleep(0.5)
        r = api_get(ep, token)
        status = r.get("result", {}).get("statusCode") if "result" in r else r.get("_error", "?")
        print(f"  {name}: status={status}")
        results[name] = r

    print("\n=== Guessed Endpoints ===")
    for name, ep in guess_endpoints.items():
        time.sleep(0.5)
        r = api_get(ep, token)
        status = r.get("result", {}).get("statusCode") if "result" in r else r.get("_error", "?")
        hit = "HIT" if status == "100" else f"miss({status})"
        print(f"  {name}: {hit}")
        if status == "100":
            results[name] = r

    # ---- app_progress の中にファミリー・分割情報がないか確認 ----
    print("\n=== app_progress data keys ===")
    prog = results.get("app_progress", {})
    data = prog.get("result", {}).get("data", {})
    if data:
        for k in sorted(data.keys()):
            v = data[k]
            vtype = type(v).__name__
            if isinstance(v, list):
                print(f"  {k}: list[{len(v)}]")
                if v:
                    if isinstance(v[0], dict):
                        print(f"    keys: {sorted(v[0].keys())}")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={sorted(v.keys())}")
            else:
                print(f"  {k}: {vtype} = {str(v)[:80]}")

    # 分割出願のapp_progressも取得
    print(f"\n=== Division app_progress ({APP_DIV}) ===")
    time.sleep(0.5)
    div_prog = api_get(f"app_progress/{APP_DIV}", token)
    div_data = div_prog.get("result", {}).get("data", {})
    if div_data:
        for k in sorted(div_data.keys()):
            v = div_data[k]
            if isinstance(v, list):
                print(f"  {k}: list[{len(v)}]")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={sorted(v.keys())}")
            else:
                print(f"  {k}: {str(v)[:80]}")

    # 結果を保存
    out_path = Path(__file__).parent / "probe_jpo_api_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved: {out_path}")

if __name__ == "__main__":
    main()
