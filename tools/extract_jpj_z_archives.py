"""extract_jpj_z_archives.py — 公報経緯コーパスの増分抽出

`claude_user_io/archived_download_data*.zip` を全件スキャンして、
拒絶査定不服Z審決のうち「第１→第２」構造を持つものから
  - 公報経緯テキスト (corpus/jpj_{appno}__jpj_official.keii.txt)
  - appno 一覧 (z_appno_list.json)
を生成する.

入力: --archive で個別 zip を指定、または既定の `claude_user_io/archived_download_data*.zip` 全件
出力: --out-corpus / --out-appno-list で指定. 既存ファイルがあれば差分のみ追加.

将来的に新しいアーカイブ (archived_download_data(N).zip) が増えても本スクリプトは無変更で動く.
99_onboard_daily.py は更新後の z_appno_list.json から差分 onboard する.

CLI:
  python tools/extract_jpj_z_archives.py \
    --archive-glob 'G:\マイドライブ\pg\claude\claude_appeal\claude_user_io\archived_download_data*.zip' \
    --out-corpus /tmp/jpj_corpus_z_full \
    --out-appno-list /tmp/z_appno_list.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import re
import sys
import zipfile
from pathlib import Path

kind_re = re.compile(r"<jptrd:Kind>([^<]+)</jptrd:Kind>")
para_re = re.compile(r"<jptrd:Paragraph(?:\s[^>]*)?>([^<]*)</jptrd:Paragraph>", re.DOTALL)
concl_re = re.compile(r"<jptrd:ConclusionPart>(.*?)</jptrd:ConclusionPart>", re.DOTALL)
reason_re = re.compile(r"<jptrd:ReasonPart>(.*?)</jptrd:ReasonPart>", re.DOTALL)
appno_re = re.compile(r"<jptrd:ApplicationNumber[^>]*>([^<]+)</jptrd:ApplicationNumber>")
PAT_Z = re.compile(r"本件審判の請求は、?\s*成り立たない")


def extract_one(xml_text: str):
    """1XML から (appno, keii_text) または None を返す."""
    m_kind = kind_re.search(xml_text)
    if not m_kind or "拒絶査定不服" not in m_kind.group(1):
        return None
    m_c = concl_re.search(xml_text)
    if not m_c:
        return None
    conc = " ".join(para_re.findall(m_c.group(1)))
    if not PAT_Z.search(conc):
        return None
    m_r = reason_re.search(xml_text)
    if not m_r:
        return None
    paras = para_re.findall(m_r.group(1))
    start = None
    for i, p in enumerate(paras):
        if "手続の経緯" in p:
            start = i
            break
    if start is None:
        return None
    end = None
    for j in range(start + 1, len(paras)):
        if re.match(r"\s*第２", paras[j]):
            end = j
            break
    if end is None:
        return None  # 「第２」マーカー必須 (品質重視)
    keii = "\n".join(p.strip() for p in paras[start:end] if p.strip())
    m_app = appno_re.search(xml_text)
    if not m_app:
        return None
    raw = m_app.group(1).strip()
    digits = re.sub(r"[^0-9０-９]", "", raw).translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    if len(digits) < 10:
        return None
    return digits[-10:], keii


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--archive-glob",
        default=str(Path.home() / ".." / ".." / "mnt" / "g" / "マイドライブ" / "pg" / "claude" / "claude_appeal" / "claude_user_io" / "archived_download_data*.zip"),
        help="入力アーカイブの glob パターン (複数zip OK)",
    )
    ap.add_argument("--out-corpus", required=True, help="公報経緯テキストの出力ディレクトリ")
    ap.add_argument("--out-appno-list", required=True, help="appno 一覧 JSON の出力パス")
    ap.add_argument("--keep-existing", action="store_true",
                    help="既存出力を温存し差分のみ追加 (既定: 上書き)")
    args = ap.parse_args()

    out_corpus = Path(args.out_corpus)
    out_corpus.mkdir(parents=True, exist_ok=True)
    out_list = Path(args.out_appno_list)
    out_list.parent.mkdir(parents=True, exist_ok=True)

    archives = sorted(glob.glob(args.archive_glob))
    if not archives:
        print(f"NO archive matched: {args.archive_glob}", file=sys.stderr)
        sys.exit(2)
    print(f"対象アーカイブ {len(archives)} 件:", file=sys.stderr)
    for a in archives:
        size_mb = Path(a).stat().st_size / 1024 / 1024
        print(f"  {a}  ({size_mb:.0f} MB)", file=sys.stderr)

    # 既存 appno (差分処理用)
    existing = set()
    if args.keep_existing and out_list.exists():
        try:
            existing = set(json.loads(out_list.read_text(encoding="utf-8")))
            print(f"既存 appno {len(existing)} 件を読み込み", file=sys.stderr)
        except Exception:
            pass

    appnos: set[str] = set(existing)
    saved_new = 0
    skipped_existing = 0
    total_xml = 0

    for arch in archives:
        print(f"\n--- 処理中: {arch} ---", file=sys.stderr)
        with zipfile.ZipFile(arch) as outer:
            sub_zips = [n for n in outer.namelist() if n.endswith(".ZIP")]
            for sub_name in sub_zips:
                with outer.open(sub_name) as fp:
                    sub_data = fp.read()
                with zipfile.ZipFile(io.BytesIO(sub_data)) as inner:
                    xml_names = [n for n in inner.namelist()
                                 if n.endswith(".xml") and ("/J_PC/" in n or "/J_PX/" in n)]
                    for n in xml_names:
                        total_xml += 1
                        txt = inner.read(n).decode("utf-8", errors="replace")
                        rec = extract_one(txt)
                        if rec is None:
                            continue
                        appno, keii = rec
                        if appno in appnos:
                            skipped_existing += 1
                            continue
                        appnos.add(appno)
                        (out_corpus / f"jpj_{appno}__jpj_official.keii.txt").write_text(
                            keii, encoding="utf-8"
                        )
                        saved_new += 1
                print(f"  {sub_name}: 累計 corpus {len(appnos)} 件 (今回 +{saved_new})",
                      file=sys.stderr)

    out_list.write_text(
        json.dumps(sorted(appnos), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n=== 完了 ===", file=sys.stderr)
    print(f"  XML 走査数:  {total_xml}", file=sys.stderr)
    print(f"  既存 skip:   {skipped_existing}", file=sys.stderr)
    print(f"  新規 saved:  {saved_new}", file=sys.stderr)
    print(f"  appno 総数:  {len(appnos)}", file=sys.stderr)
    print(f"  corpus dir:  {out_corpus}", file=sys.stderr)
    print(f"  appno list:  {out_list}", file=sys.stderr)


if __name__ == "__main__":
    main()
