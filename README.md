# keii_pub — 「手続の経緯」自動生成

審決起案の「手続の経緯」節を、Python ルールベース（LLM フリー）で自動生成する。
入力は **出願番号のみ**。分割出願（多世代）／優先権（パリ・国内）／PCT 国内段階／外国語書面出願 とそれらの複合に対応する。

仕様の詳細は次のファイルに分かれている：

- [`SPEC_overall.md`](SPEC_overall.md) — 全体仕様（生成ロジック・データ取得経路・出力テンプレート 11 パターン）
- [`SPEC_testing.md`](SPEC_testing.md) — テスト仕様（92_batch_generate.py の使い方と回帰確認手順）
- [`SPEC_A1.md`](SPEC_A1.md) — 分割_PCT原出願 系の修正詳細（リファレンス）

---

## 動作要件

- Python 3.10 以上
- 特許庁 JPO API の利用契約（ID／パスワード）
  - 認証情報の入手は [https://ip-data.jpo.go.jp/](https://ip-data.jpo.go.jp/) を参照
- 標準ライブラリ以外の依存:
  - 任意: `PyYAML`（無くても動く。簡易 YAML パーサで `settings.yaml` を読む）
  - 任意: `playwright`（J-PlatPat スクレイピング系スクリプトを使う場合のみ）

---

## ディレクトリ構成

```
keii_pub/
├── README.md                     ← 本ファイル
├── SPEC_overall.md               ← 全体仕様
├── SPEC_testing.md               ← テスト仕様
├── SPEC_A1.md                    ← A-1 修正詳細
├── settings.example.yaml         ← 設定テンプレ
├── settings.yaml                 ← 個人環境設定（.gitignore 済。各自作成）
├── .gitignore
│
├── 0X_*.py / 1X_*.py             ← データ取得スクリプト群
├── 91_generate_intro.py          ← 単体生成（試験用）
├── 92_batch_generate.py          ← バッチ生成 + corpus 比較
├── 93_diff_categorize.py         ← diff 自動分類
│
├── generator/                    ← 生成エンジン本体
│   ├── classify.py               ← 案件性質分類（11 パターン）
│   ├── intro.py                  ← 冒頭文生成
│   ├── chronology.py             ← 経過リスト整形
│   ├── dates.py                  ← 書類日付取得（抽象層）
│   ├── keii.py                   ← 統合エントリーポイント
│   ├── jp_dates.py               ← 西暦・和暦変換
│   ├── api_remain.py             ← JPO API 残量管理
│   └── settings.py               ← 設定ローダ
│
├── templates/                    ← 仕様カタログ（人が読む）
│   ├── keii_model.yaml           ← 全体モデル
│   └── rules_developer.yaml      ← 11 パターン定義
│
├── fetcher/                      ← JPO API クライアント
│   └── probe_jpo_api.py
│
├── corpus/                       ← 正解集合（.gitignore 済。各自準備）
└── inventory/                    ← 取得済みデータ（.gitignore 済。fetch で生成）
```

---

## 初期セットアップ

### 1. 設定ファイルの作成

```
cp settings.example.yaml settings.yaml
```

`settings.yaml` を編集して、以下を埋める：

```yaml
# JPO API 認証情報の格納ディレクトリ（必須・データ取得時）
jpo_api_credentials_dir: ~/.config/keii_pub/jpo_api

# 既存 doc_history.json を補助的に探すディレクトリ（オプション）
inputs_fallback_dir: null
```

### 2. JPO API 認証情報ファイルの作成

`jpo_api_credentials_dir` で指定したディレクトリに、`jpo_api_cred.json` を作成：

```json
{
  "username": "your_jpo_api_username",
  "password": "your_jpo_api_password"
}
```

### 3. 設定診断

```
python3 generator/settings.py
```

設定ファイルの存在、認証情報ファイルの存在、各パスの解決結果が表示される。

### 4. ディレクトリ作成

```
mkdir -p inventory corpus
```

`inventory/` は fetch スクリプトが populate する。`corpus/` には正解の `.keii.txt` を配置する（後述）。

---

## 使い方

### 単体生成

ある出願番号 1 件の「手続の経緯」を生成する：

```
python3 -m generator.keii 2018244177
python3 -m generator.keii 2018244177 --kind z_no_kakka       # 「１ 手続の経緯」見出し
python3 -m generator.keii 2018244177 --prio-def-style C      # 優先日定義を独立段落で出力
```

事前に当該案件の `inventory/doc_history_collected/{appno}.json` 等が必要。
無ければ次節のデータ取得を行う。

### 新規案件のデータ取得

```
1. inventory/case_appno_map.tsv に新規案件を追記
   case_key{tab}appno{tab}src_path{tab}ok{tab}note

2. python3 04_fetch_doc_history.py     ← doc_history.json
3. python3 05_fetch_app_docs.py        ← doc_xmls/
4. python3 08_fetch_parent_chain.py    ← parent_chains/
5. python3 10_fetch_jpp_app_info.py    ← jpp_app_info/ (J-PlatPat, playwright 必要)
6. python3 06_fetch_zenchi_drafting.py ← zenchi_drafting/ (前置報告書がある案件のみ)
```

### バッチ実行 + corpus 比較

複数案件を一括生成し、正解（corpus）と比較する：

```
python3 92_batch_generate.py
```

- 出力: `inventory/batch_generate/{case_key}.gen.txt` + `.diff.txt` + `_batch_log.tsv`
- 全 N 件の平均一致率（mean ratio）と中央値が表示される
- 詳細は `SPEC_testing.md` 参照

---

## corpus の準備

`corpus/` には、確定済み起案 docx から抽出した「手続の経緯」節を `.keii.txt` として置く。
ファイル名規約：

```
corpus/{case_key}__{自由文字列}.keii.txt
```

例:

```
corpus/2023-019613__起案_final.keii.txt
```

各 .keii.txt は単純テキスト：

```
第１　手続の経緯
　本願は、平成３０年１２月２７日（パリ条約による優先権主張　２０１８年１月４日、欧州特許庁）の出願であって、その手続の経緯の概略は、次のとおりである。
令和４年　８月１７日付け：拒絶理由通知書
…
```

corpus が無くても単体生成は動くが、`92_batch_generate.py` の corpus 比較機能は使えない。

---

## 設定の優先順位

```
環境変数 (KEII_*) > settings.yaml > 組み込みデフォルト (None)
```

| 環境変数 | 上書きする設定キー |
|---|---|
| `KEII_JPO_API_DIR` | `jpo_api_credentials_dir` |
| `KEII_INPUTS_FALLBACK_DIR` | `inputs_fallback_dir` |
| `JPO_API_DIR` | `probe_jpo_api.py` が直接参照（通常は自動セット） |

---

## ライセンス

未定（公開時に追加）。

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `<<参照エラー: doc_history.json not found ...>>` | 当該 appno のデータ未取得 | `04_fetch_doc_history.py` を実行 |
| `RuntimeError: JPO API credentials directory is not configured` | settings.yaml / 環境変数未設定 | `settings.yaml` を作成し `jpo_api_credentials_dir` を指定 |
| `JPO API credentials not found under ...` | jpo_api_cred.json が無い | 認証情報ディレクトリに `jpo_api_cred.json` を作成 |
| バッチで一部案件が `ratio=0.000` | corpus の `.keii.txt` が無い、または appno のデータ未取得 | `corpus/` と `inventory/doc_history_collected/` の両方を確認 |
| 全件 `ratio` が極端に低い | template / generator のバグ | `SPEC_testing.md` の手順で diff を確認 |
