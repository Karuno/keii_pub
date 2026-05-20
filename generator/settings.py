"""generator/settings.py — keii_python のローカル設定ローダ

設定値の優先順位:
  1. 環境変数 (KEII_*)
  2. settings.yaml
  3. 組み込みデフォルト (None)

公開 API:
  get_settings() -> Settings
  get_jpo_api_dir() -> Path     # JPO API 認証情報の格納ディレクトリ（必須）
  get_inputs_fallback_dir() -> Path | None

副作用:
  get_settings() 初回呼び出し時、jpo_api_credentials_dir が解決できれば
  環境変数 JPO_API_DIR を自動セット（tools/fetcher/probe_jpo_api.py が
  本変数を読むため）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
KEII_PYTHON_ROOT = HERE.parent
SETTINGS_PATH = KEII_PYTHON_ROOT / "settings.yaml"
SETTINGS_EXAMPLE_PATH = KEII_PYTHON_ROOT / "settings.example.yaml"


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """key: value 形式の単純な YAML を読む（PyYAML 非依存）。

    対応:
      - コメント (# 以降)
      - 文字列値 (クオートあり/なし)
      - null / ~ → None
      - 空値 → None
    非対応: ネスト、リスト、複数行。
    """
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        if line[:1] in (" ", "\t"):
            # ネストは未対応のためスキップ
            continue
        k, _, v = stripped.partition(":")
        k = k.strip()
        v = v.strip()
        if v == "" or v.lower() in ("null", "~"):
            out[k] = None
        elif (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            out[k] = v[1:-1]
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        result = yaml.safe_load(text)
        return result if isinstance(result, dict) else {}
    except ImportError:
        return _parse_simple_yaml(text)


def _resolve_path(value: str | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value).expanduser()


@dataclass(frozen=True)
class Settings:
    jpo_api_credentials_dir: Path | None
    inputs_fallback_dir: Path | None


def _load() -> Settings:
    raw = _load_yaml(SETTINGS_PATH)
    jpo_str = os.environ.get("KEII_JPO_API_DIR") or raw.get("jpo_api_credentials_dir")
    inputs_str = os.environ.get("KEII_INPUTS_FALLBACK_DIR") or raw.get(
        "inputs_fallback_dir"
    )
    return Settings(
        jpo_api_credentials_dir=_resolve_path(jpo_str),
        inputs_fallback_dir=_resolve_path(inputs_str),
    )


_cached: Settings | None = None


def get_settings() -> Settings:
    """設定を取得（初回呼び出しで読み込み、以降キャッシュ）。

    副作用: jpo_api_credentials_dir が解決できれば環境変数 JPO_API_DIR を
    setdefault する（tools/fetcher/probe_jpo_api.py が参照するため）。
    """
    global _cached
    if _cached is None:
        _cached = _load()
        if _cached.jpo_api_credentials_dir is not None:
            os.environ.setdefault(
                "JPO_API_DIR", str(_cached.jpo_api_credentials_dir)
            )
    return _cached


def get_jpo_api_dir() -> Path:
    """JPO API 認証情報の格納ディレクトリを返す。未設定なら RuntimeError。"""
    s = get_settings()
    if s.jpo_api_credentials_dir is None:
        raise RuntimeError(
            "JPO API credentials directory is not configured.\n"
            f"Set 'jpo_api_credentials_dir' in:\n  {SETTINGS_PATH}\n"
            f"(template: {SETTINGS_EXAMPLE_PATH})\n"
            "or set the KEII_JPO_API_DIR environment variable.\n"
            "The directory must contain jpo_api_cred.json with keys "
            "'username' and 'password'."
        )
    return s.jpo_api_credentials_dir


def get_inputs_fallback_dir() -> Path | None:
    """doc_history.json 探索の補助ディレクトリ（未設定なら None）。"""
    return get_settings().inputs_fallback_dir


# ============================================================================
# CLI: 設定診断
# ============================================================================

def _main() -> None:
    s = get_settings()
    print(f"settings.yaml:        {SETTINGS_PATH}")
    print(f"  exists:             {SETTINGS_PATH.exists()}")
    print(f"jpo_api_credentials_dir: {s.jpo_api_credentials_dir}")
    if s.jpo_api_credentials_dir is not None:
        print(f"  exists:             {s.jpo_api_credentials_dir.exists()}")
        cred = s.jpo_api_credentials_dir / "jpo_api_cred.json"
        print(f"  jpo_api_cred.json:  {cred.exists()}")
    print(f"inputs_fallback_dir:  {s.inputs_fallback_dir}")
    if s.inputs_fallback_dir is not None:
        print(f"  exists:             {s.inputs_fallback_dir.exists()}")
    print(f"env KEII_JPO_API_DIR: {os.environ.get('KEII_JPO_API_DIR', '(unset)')}")
    print(f"env JPO_API_DIR:      {os.environ.get('JPO_API_DIR', '(unset)')}")


if __name__ == "__main__":
    _main()
