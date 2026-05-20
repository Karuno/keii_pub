# keii_python テスト仕様書

最終更新: 2026-05-20
対象: `92_batch_generate.py` + `_compare_A1.py` + `A1_classify_check.py` ほか検証ツール群

---

## 0. このドキュメントの目的

「手続の経緯」生成ロジックを更新したとき、

- **何が壊れていないか**（regression が無いか）
- **何が良くなったか**（改善ケースの分布）
- **何が依然問題か**（diff の残存パターン）

を機械的に確認するための手順を、コードを読まずに追跡できるよう記述する。

---

## 1. テスト全体像

テストは **「実起案コーパス（48 件）との文字列一致率」** を主指標とする回帰テスト方式。
正解集合は `corpus/` 配下の `.keii.txt` ファイル群。
これらは確定済み起案 docx から抽出した「手続の経緯」節のテキスト。

### 1.1 評価指標

| 指標 | 計算方法 | 意味 |
|---|---|---|
| **ratio** | `difflib.SequenceMatcher(None, gen, corpus).ratio()` | 0.0〜1.0、1.0=完全一致 |
| **mean ratio** | 全 valid 案件の ratio 平均 | 全体的な再現性 |
| **median ratio** | 同・中央値 | 外れ値に強い再現性 |
| **valid count** | ratio > 0 の案件数 | 生成・比較が成立した件数 |

### 1.2 正規化

corpus と生成出力の比較前に以下を正規化する:

- 行頭の全角空白を除去
- 行末の空白を除去
- 空行を削除
- 連続空白を 1 つに

これにより「見た目が同じだが微妙にスペースが違う」差異は同一視される。
実装: `92_batch_generate.py:normalize()`

### 1.3 テスト対象案件

`inventory/case_appno_map.tsv` に `status=ok` で登録された 44 件を対象とする
（同一 appno のダブル登録 = corpus 内 48 件のうち 4 件は別行で生成されない）。

case_appno_map.tsv の各列:

| 列 | 内容 |
|---|---|
| `case_key` | 案件キー（例: `2024-002462`）。corpus ファイル名や出力ファイル名のキー |
| `appno` | 10 桁出願番号 |
| `src_path` | 元の docx ファイルパス（参考。実行時は使わない） |
| `status` | `ok` のみ生成対象に含める |
| `note` | 自由記述 |

---

## 2. 主要ツール一覧

| スクリプト | 用途 | 入力 | 出力 |
|---|---|---|---|
| `92_batch_generate.py` | 全件生成 + corpus 比較 | なし（自動的に case_appno_map.tsv） | `inventory/batch_generate/{case_key}.{gen,diff}.txt` + `_batch_log.tsv` |
| `A1_classify_check.py` | A-1 該当 8 件の pattern 判定確認 | なし（スクリプト内ハードコート） | 標準出力（pattern 判定結果） |
| `_compare_A1.py` | A-1 修正前後の per-case 比較 | `_batch_log_baseline.tsv` と `_batch_log.tsv` | 標準出力 |
| `93_diff_categorize.py` | diff の自動分類 | `inventory/batch_generate/*.diff.txt` | 標準出力 |

検証用の `A1_*.py` / `A2_*.py` / `B1_*.py` は個別仮説検証用（A-1, A-2, B-1 等の単発タスクで作成済）。
通常テストでは 92_batch_generate.py が主役。

---

## 3. 標準テストワークフロー

ロジックを更新したときの標準手順:

```
[Step 1] 修正前のベースライン取得
   ↓
[Step 2] ロジック修正
   ↓
[Step 3] 修正後のバッチ実行
   ↓
[Step 4] 改善・改悪の per-case 比較
   ↓
[Step 5] 該当案件の diff 全文確認
   ↓
[Step 6] 残差を裁量例外 (④) として記録 or 次のタスクに繰り越し
```

### 3.1 Step 1: ベースライン取得

```bash
python3 92_batch_generate.py
cp inventory/batch_generate/_batch_log.tsv inventory/batch_generate/_batch_log_baseline.tsv
```

- 実行時間: 約 13 分（Google Drive 経由 IO がボトルネック）
- 出力: `inventory/batch_generate/` 配下に per-case `{case_key}.gen.txt` / `.diff.txt`、サマリ `_batch_log.tsv`
- バックアップとして `_batch_log_baseline.tsv` を確保

### 3.2 Step 2: ロジック修正

`generator/classify.py` / `generator/intro.py` / `generator/chronology.py` / `templates/*.yaml` を編集。

### 3.3 Step 3: 修正後のバッチ実行

```bash
python3 92_batch_generate.py
```

サマリ出力の見方:

```
=== summary ===
  valid: 44/44                       ← 44 案件すべて比較成立
  ratio mean=0.881 median=0.903       ← 全体の再現性
  ratio min=0.558 max=0.956           ← 散らばり
  ratio >= 0.99: 0                    ← 完全一致に近い件数
```

**valid 数が baseline より減っていたら何かが壊れている**。要調査。

### 3.4 Step 4: 改善・改悪の per-case 比較

```bash
python3 _compare_A1.py
```

出力例:

```
=== A-1 該当 8 件 ===
case_key      pattern (base→post)              base    post       Δ
2023-017801   パリ条約優先＋外国語書面出願 → 分割_PCT原出願   0.767   0.850  +0.082 ★
…
=== 全体 ===
baseline: n=44 mean=0.8717
post-fix: n=44 mean=0.8807

=== 改悪 (regression) 2 件 ===
  2023-019613: 0.941 → 0.000  pattern: パリ条約優先権 → 
=== 改善 (improvement) 5 件 ===
  …
```

「**改悪 0 件**」が標準的な合格基準。0 件でなければ pattern 列の変化や `ratio = 0.000` 失敗を必ず調査する。

`_compare_A1.py` は A-1 ハードコードなので、別タスク用には `_compare_A1.py` を雛形にコピーして書き換える、または直接 TSV を Python で比較する。

### 3.5 Step 5: 該当案件の diff 全文確認

```bash
cat inventory/batch_generate/2023-017801.diff.txt
```

unified diff 形式で生成と corpus の差分が見える。
`---` が生成側、`+++` が corpus 側。

### 3.6 Step 6: 残差を分類

- **①プログラム不足**: 今回の修正対象から漏れた。次のタスクで対応
- **②情報フィールド不足**: API/J-PlatPat 経路に該当データが無い。取得スクリプト拡張が必要
- **③既存フィールドの値欠落**: データソースで値が空。データ品質問題
- **④起案者裁量例外**: corpus 側の任意性。`inventory/_diff_findings.md §B` に記録して許容
- **⑤事実ズレ**: corpus と原典が食い違う（コピペミス等）。仕様外として保留

`inventory/_diff_findings.md` に分類結果を残し、A-1〜A-N の追跡 index に組み込む。

---

## 4. 個別案件のスポットチェック

`92_batch_generate.py` には `--cases` 引数で個別実行できる:

```bash
python3 92_batch_generate.py --cases 2023-017801 2024-011442
```

- 数件で 30〜60 秒
- ロジック修正の影響範囲が分かっているときの高速確認に有用

### 4.1 単体生成の確認

`generator/keii.py` を直接 CLI 起動すると 1 件生成できる:

```bash
python3 -m generator.keii 2018244177
python3 -m generator.keii 2018244177 --kind z_no_kakka
python3 -m generator.keii 2018244177 --prio-def-style C
```

corpus との比較はしないが、生成結果を目視確認したいときに使う。

---

## 5. 分類判定の確認

冒頭文の pattern 判定だけを確認したいとき:

```bash
python3 A1_classify_check.py
```

出力例:

```
case_id        appno        apptype is_div gen parent_pct pattern
2022-018254    2021085350   C     True   1   True       分割_PCT原出願
2023-017801    2022030555   C     True   1   True       分割_PCT原出願
…
```

A-1 該当 8 件をハードコードしているため、他案件の分類確認は新規スクリプトを書く必要がある。
最小限の確認スクリプト雛形:

```python
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.classify import detect_pattern, get_data

raw = json.loads(Path("inventory/doc_history_collected/2022030555.json").read_text(encoding="utf-8"))
cls = detect_pattern(get_data(raw))
print(cls)
```

---

## 6. 期待される ratio 水準（現状ベースライン）

2026-05-20 時点:

| 指標 | 値 |
|---|---|
| 全 44 件 mean | 0.881 |
| 全 44 件 median | 0.903 |
| 全 44 件 max | 0.956 |
| 全 44 件 min | 0.558 |
| ratio >= 0.99 完全一致 | 0 件 |

**「完全一致 0 件」は問題なし**。理由:
- corpus は起案者ごとのスタイル差（西暦/和暦併記、出願番号括弧の有無、優先日定義位置 等）があり、生成器の 1 スタイル選択ではどうしても文字列レベルで差が出る
- 1 部門ルール準拠でない裁量記述（条文番号挿入等）は生成器が出さない
- 上記の差は `inventory/_diff_findings.md §B` に「裁量例外」として登録され、生成器を直す対象から外している

意味のある改善は **mean +0.01 以上、median 上昇、改悪 0 件** で判断。

### 6.1 値が低い案件（min 付近）の特徴

- `ratio < 0.7` の案件は corpus 側の独特スタイル or 事実ズレ（doc_history と corpus の不整合）の可能性が高い
- まず `inventory/_diff_findings.md` で当該案件の分類を確認

---

## 7. テスト中の落とし穴

### 7.1 Google Drive のキャッシュ

Google Drive 経由でファイルが書かれた直後は、別プロセスからの読み込みが間に合わないことがある。
バッチ完了後すぐの per-case ファイル参照では数秒待つと安全。

### 7.2 `inventory/` がデータ不在

`02_inventory_doc_history.py` で `case_appno_map.tsv` を生成しても、各 appno の
`doc_history_collected/{appno}.json` が無ければ generator は `<<参照エラー>>` で
ratio=0 になる。
データ取得 (04→05→08→10→06) を先に通すこと。

### 7.3 `inputs_fallback_dir` 設定の有無

`settings.yaml` の `inputs_fallback_dir` が無設定だと、`inventory/doc_history_collected/` に
無い appno はすべて `ratio=0`。担当者配布の inputs フォルダを併用する環境では設定が必要。

### 7.4 corpus が古い

`corpus/` は `01_collect_corpus.py` で再生成可能。
docx の正本側を更新した場合は corpus も更新すること。

---

## 8. 新規案件をテスト対象に追加する

```
1. inventory/case_appno_map.tsv に行追加
   case_key{tab}appno{tab}src_path{tab}ok{tab}note

2. corpus に該当 case_key の .keii.txt を追加
   corpus/{case_key}__{author}_*.keii.txt
   （docx から該当節を抽出。手動 or 01_collect_corpus.py 経由）

3. データ取得
   python3 04_fetch_doc_history.py
   python3 05_fetch_app_docs.py
   python3 08_fetch_parent_chain.py
   python3 10_fetch_jpp_app_info.py
   python3 06_fetch_zenchi_drafting.py  # 前置報告書がある案件のみ

4. テスト
   python3 92_batch_generate.py --cases {case_key}
```

---

## 9. テスト用ファイルの位置関係

```

├── 92_batch_generate.py           ← バッチ実行スクリプト
├── A1_classify_check.py           ← 分類確認スクリプト
├── _compare_A1.py                 ← per-case 比較スクリプト
├── 93_diff_categorize.py          ← diff 自動分類
├── corpus/                        ← 正解集合（.keii.txt 群）
├── inventory/
│   ├── case_appno_map.tsv         ← テスト対象案件マスタ
│   ├── doc_history_collected/     ← 各案件の doc_history.json
│   ├── doc_xmls/                  ← 各案件の庁作成書類 XML
│   ├── jpp_app_info/              ← 各案件の出願情報（J-PlatPat）
│   ├── zenchi_drafting/           ← 各案件の前置報告書作成日（J-PlatPat）
│   ├── batch_generate/            ← バッチ実行成果
│   │   ├── _batch_log.tsv         ← 最新サマリ
│   │   ├── _batch_log_baseline.tsv ← ベースラインサマリ
│   │   ├── {case_key}.gen.txt     ← 生成結果
│   │   └── {case_key}.diff.txt    ← corpus との diff
│   └── _diff_findings.md          ← 残差異 index
└── settings.yaml                  ← 個人環境設定（git 管理外）
```

---

## 10. ハードコード排除の確認（頒布前監査結果）

| 項目 | 状態 |
|---|---|
| API 認証情報 | ハードコートなし（外部ファイル `jpo_api_cred.json` から読み込み） |
| API 認証情報の場所 | 環境変数 / settings.yaml で上書き可能 |
| データ取得スクリプトの絶対パス | なし |
| 生成器の絶対パス | なし |
| テストスクリプトの絶対パス | なし |
| corpus / inventory のパス | 全て `Path(__file__).resolve()` 相対 |

`SPEC_overall.md §14` も併せて参照。
