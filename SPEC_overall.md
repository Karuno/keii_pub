# 「手続の経緯」自動生成 全体仕様書

最終更新: 2026-05-20
対象: `` 全体（冒頭文 + 経過リスト + 多世代分割対応）

---

## 0. このドキュメントの目的

審決起案の「手続の経緯」節を、**出願番号 1 件だけを入力**にして
Python ルールベースで自動生成する仕組みについて、

- どの情報を、どの情報源から、どのスクリプトで取得しているか
- 取得した情報の何を見て、どの分岐に入るか
- どんな表記で出力されるか
- 法令・基準・開発者ルールのどこに根拠があるか

を、コードを読まずに追跡できるよう記述する。

---

## 1. システムの全体フロー

```
入力: 出願番号 (10桁)
   │
   ▼
[Step 1] データ取得 ────────────────────┐
   ├─ doc_history.json (JPO API)        │
   ├─ doc_xmls/{appno}/*.xml (JPO API)  │ 既取得済みの
   ├─ zenchi_drafting/ (補助ソース)      │ inventory/
   ├─ aux_appinfo/ (補助ソース)         │ から参照する
   └─ aux_dates/ (フォールバック)   │
                                          │
   ▼                                     ▼
[Step 2] 案件性質の分類 (pattern 判定)
   └─ 11 パターンのいずれかに振り分け
   ▼
[Step 3] 冒頭文 (INTRO) を生成
   └─ 1 行: 「本願は、...次のとおりである。」
   ▼
[Step 4] 書類の日付リスト取得
   ├─ Type A (庁作成: 拒絶理由通知書等) の起案日
   ├─ Type B (出願人提出: 意見書等) の受領日
   └─ Type C (送達: 拒絶査定の謄本送達) の発送日
   ▼
[Step 5] 経過リスト (CHRONO) を生成
   ├─ 並び順整理
   ├─ 同日結合
   ├─ 同年・同月の重複表記の省略
   └─ 桁揃え
   ▼
[Step 6] 見出し + INTRO + (多世代分割の場合は DIV_CHAIN) + CHRONO を結合
   ▼
出力: 「第１ 手続の経緯 …」の完成テキスト
```

---

## 2. 入力

**入力は出願番号 1 件のみ**（10 桁、例: `2022030555`）。

PDF や担当者配布資料には依存しない（誤訳訂正書の有無確認のみ例外）。
すべての情報は事前取得済みの `inventory/` 配下から読む。

---

## 3. 情報源（inventory/ 配下）

### 3.1 `doc_history_collected/{appno}.json` — 中核データ

| 項目 | 内容 |
|---|---|
| 情報源 | 特許庁 JPO API の「経過情報」エンドポイント |
| 取得スクリプト | `04_fetch_doc_history.py` |
| ファイル形式 | JSON |

主要フィールド：

| パス | 意味 | 使用箇所 |
|---|---|---|
| `applicationNumber` | 本願出願番号 | 全般 |
| `filingDate` | 本願の出願日 | INTRO |
| `inventionTitle` | 発明の名称 | （ヘッダー外） |
| `internationalApplicationNumber` | 国際出願番号（PCT 国内段階の場合） | INTRO 分類 |
| `priorityRightInformation[]` | 優先権情報配列 | INTRO 分類・出力 |
| `priorityRightInformation[].parisPriorityDate` | パリ優先の最先日 | INTRO 分類・出力 |
| `priorityRightInformation[].parisPriorityCountryCd` | 優先権受理国コード | INTRO 出力 |
| `priorityRightInformation[].nationalPriorityDate` | 国内優先の最先日 | INTRO 分類・出力 |
| `parentApplicationInformation` | 親出願情報（分割案件用） | INTRO 分類 |
| `divisionalApplicationInformation[]` | 分割系列情報（祖父・親含む） | INTRO 分類 |
| `bibliographyInformation[].documentList[]` | 書類一覧（出願人提出 + 庁作成） | CHRONO 各種・翻訳文判定 |
| `bibliographyInformation[].documentList[].documentCode` | 書類コード（A131, A02, A53 等） | CHRONO 分類 |
| `bibliographyInformation[].documentList[].documentDescription` | 書類名 | CHRONO 分類 |
| `bibliographyInformation[].documentList[].legalDate` | 書類の受領日／発送日 | CHRONO Type B/C |

### 3.2 `doc_xmls/{appno}/*.xml` + `_summary.json` — Type A 書類の起案日

| 項目 | 内容 |
|---|---|
| 情報源 | 特許庁 JPO API の「庁作成書類本文」エンドポイント |
| 取得スクリプト | `05_fetch_app_docs.py` |
| ファイル形式 | XML（書類本文） + JSON（サマリ） |

各 XML の `<jp:drafting-date>` タグから、拒絶理由通知書・拒絶査定・補正の却下・特許査定の
**起案日**を取得する。

「発送日」（doc_history の legalDate）ではなく「起案日」を使う理由:
- 実測で 7 日前後の系統ズレがあり、コーパス（確定済み起案）の日付と一致しない
- コーパスは「起案日」を採用しているため

### 3.3 `zenchi_drafting/{appno}.json` — 前置報告書の作成日

| 項目 | 内容 |
|---|---|
| 情報源 | 補助情報源の経過情報ページ |
| 取得スクリプト | `06_fetch_zenchi_drafting.py` |
| ファイル形式 | JSON |

前置報告書（書類コード `A913`）の本文に記載される「**作成日 元号N年M月D日**」を取得。
JPO API は本書類の本文配信対象外のため 補助ソースを使う。

### 3.4 `aux_appinfo/{appno}.json` — 分割チェーン

| 項目 | 内容 |
|---|---|
| 情報源 | 補助ソース |
| 取得スクリプト | `10_fetch_aux_appinfo.py` |
| ファイル形式 | JSON |

多世代分割案件で、本願 → 親 → 祖父 → ...の出願番号と出願日のチェーンを取得：

```json
{
  "chain": [
    {"appno": "2021168340", "filing_date": "2021-10-13"},   ← 本願 (chain[0])
    {"appno": "2020087874", "filing_date": "2020-05-20"},   ← 親 (chain[1])
    {"appno": "2018557229", "filing_date": "2017-02-20"}    ← 祖父 (chain[-1])
  ]
}
```

`divisionalApplicationInformation` の最先エントリには `filingDate` がデータソース仕様上
含まれないため、最先の出願日は本データから取る。

### 3.5 `aux_dates/{appno}.json` — フォールバック

JPO API 不通時のフォールバック。補助ソースを全件スクレイピングして、
書類名と日付の一覧を取得する（精度は API より低い）。

| 取得スクリプト | `07_fetch_external_fallback.py` |

### 3.6 取得スクリプト一覧（参考）

| 番号 | スクリプト | 役割 |
|---|---|---|
| 04 | `04_fetch_doc_history.py` | doc_history.json |
| 05 | `05_fetch_app_docs.py` | doc_xmls/ |
| 06 | `06_fetch_zenchi_drafting.py` | 前置報告書作成日 |
| 07 | `07_fetch_external_fallback.py` | フォールバック全書類日付 |
| 08 | `08_fetch_parent_chain.py` | 親出願チェーン |
| 09 | `09_fetch_aux_division_chain.py` | 分割チェーン（旧経路） |
| 10 | `10_fetch_aux_appinfo.py` | 分割チェーン（現行経路） |

---

## 4. 出力構造

```
第１　手続の経緯                                ← 見出し (HEAD)
　本願は、…次のとおりである。                  ← 冒頭文 1 行 (INTRO)
[「１　出願分割の経緯の概略」+ 系列リスト         ← 多世代分割の場合のみ (DIV_CHAIN)
 ＋ 「２　本願の手続の経緯の概略」]
令和●年●月●日付け：拒絶理由通知書             ← 経過 1 行目 (CHRONO)
同年●月●日　　：意見書、手続補正書の提出      ← 経過 2 行目
…
[なお、●年●月●日を以下「優先日」という。]    ← 優先日定義（あれば）
```

---

## 5. 冒頭文（INTRO）の生成

### 5.1 11 パターンの全分類

冒頭文は「本願の性質」によって 11 パターンに分かれる。`generator/classify.py` の
`detect_pattern()` 関数が、`doc_history.json` のフィールドを見て分類する。

```
[Step A] 分割案件か？ (parentApplicationInformation あり、かつ出願日が本願と異なる)
   │
   ├─ Yes（分割） ──┐
   │                │
   │      ┌─────────┴────────┐
   │  世代≥3 → 「分割_第3世代以降」
   │  世代=2 で 最先がPCT → 「分割_PCT原出願_第2世代」 ★新設
   │  世代=2 → 「分割_第2世代」
   │  親がPCT or 最先がPCT → 「分割_PCT原出願」
   │  それ以外 → 「分割_基本形」
   │
   └─ No（非分割） ─┐
                    │
[Step B] 本願が PCT 国内段階か？ (internationalApplicationNumber あり、
                                  または 本願 appno の 5 桁目が "5")
   ├─ Yes（PCT 本願）──┐
   │           │
   │   ┌───────┴────────┐
   │  パリ優先あり → 「パリ条約優先＋ＰＣＴ」
   │  国内優先あり → 「国内優先＋日本語ＰＣＴ」
   │  優先権なし   → 上記いずれか（apptype で振り分け）
   │
   └─ No（内国出願）──┐
                       │
[Step C] 優先権の有無で振り分け
   ├─ パリ優先あり → 「パリ条約優先権」
   ├─ 国内優先あり → 「国内優先権」
   └─ なし         → 「通常内国出願」

[最後の上書き] 非分割 かつ apptype=C（外国語書面出願）かつ パリ優先あり
   → 「パリ条約優先＋外国語書面出願」
```

### 5.2 主要な判定条件と情報源

| 判定条件 | 情報源 / 計算方法 |
|---|---|
| 分割案件か | `parentApplicationInformation.parentApplicationNumber` が空でなく、`parentApplicationInformation.filingDate` ≠ `filingDate` |
| 世代数 | `aux_appinfo/{appno}.json` の `chain` の長さ − 1 |
| 親が PCT 国内段階か | 親 appno の 5 桁目が `"5"` |
| 最先が PCT 国内段階か | `divisionalApplicationInformation` の `divisionalGeneration=0` の `applicationNumber` 5 桁目が `"5"` または `internationalApplicationNumber` が空でない |
| 本願が PCT 国内段階か | `internationalApplicationNumber` あり、または 本願 appno の 5 桁目が `"5"` |
| パリ優先あり | `priorityRightInformation[].parisPriorityDate` が 1 件以上ある |
| 国内優先あり | `priorityRightInformation[].nationalPriorityDate` が 1 件以上ある |
| apptype = C（外国語書面） | 本願 appno が PCT でない、かつ `documentList` に翻訳文書類あり |

### 5.3 各パターンの出力テンプレート

| pattern | 出力文の骨格 | 法的位置付け |
|---|---|---|
| 通常内国出願 | 「本願は、{出願日}の出願であって、…」 | 36 条 1 項 |
| 国内優先権 | 「本願は、{出願日}（優先権主張　{優先日}）の出願であって、…」 | 41 条 |
| 国内優先＋日本語ＰＣＴ | 「本願は、{国際出願日}を国際出願日とする日本語特許出願であって（優先権主張　{優先日}）、…」 | 184 条の 6 + 41 条 |
| パリ条約優先権 | 「本願は、{出願日}（パリ条約による優先権主張　{優先日}、{受理国}）の出願であって、…」 | 43 条 |
| パリ条約優先＋ＰＣＴ | 「本願は、{国際出願日}（パリ条約による優先権主張外国庁受理{優先日}、{受理国}）を国際出願日とする外国語特許出願であって、…」 | 184 条の 4 + 43 条 |
| パリ条約優先＋外国語書面出願 | 「本願は、{出願日}の外国語書面出願（パリ条約による優先権主張、{優先日}、{受理国}）であって、…」 | 36 条の 2 + 43 条 |
| 分割_基本形 | 「本願は、{親出願日}に出願した特願{親番号}号の一部を{本願出願日}に新たな特許出願としたものであって、…」 | 44 条 1 項 |
| 分割_第2世代 | 「本願は、{祖父出願日}に出願した特願{祖父番号}号の一部を{親出願日}に新たな特許出願とした特願{親番号}号の一部を{本願出願日}に新たな特許出願としたものであって、…」 | 44 条 1 項 ×2 |
| 分割_第3世代以降 | 「本願は、{本願出願日}にされた特許法 44 条 1 項の規定による特許出願であって、{最先出願日}に出願した特願{最先番号}号を最先の出願とする、いわゆる第N世代の分割出願であるところ、出願の分割の経緯は、次のとおりである。」※系列リストが続く | 44 条 1 項 ×N |
| 分割_PCT原出願 | 「本願は、{親国際出願日}を国際出願日とする{言語フレーズ}（特願{親番号}号）の一部を、{本願出願日}に新たな{終端フレーズ}としたものであって（パリ条約による優先権主張　…）、…」 | 184 条の 4 or 6 + 44 条 1 項 |
| 分割_PCT原出願_第2世代 | 「本願は、{祖父国際出願日}を国際出願日とする{言語フレーズ}（特願{祖父番号}号）の一部を、{親出願日}に新たな特許出願とした特願{親番号}号の一部を、{本願出願日}に新たな{終端フレーズ}としたものであって（パリ条約による優先権主張　…）、…」 | 184 条の 4/6 + 44 条 1 項 ×2 |

**`{言語フレーズ}` の決定**（`divisionalApplicationInformation[gen=0].internationalApplicationNumber` の接頭辞）:

| 接頭辞 | 表記 |
|---|---|
| `JP*` | 日本語特許出願（184 条の 6） |
| その他（`US/EP/IB/CN/KR/...`） | 外国語特許出願（184 条の 4） |
| 空 | 特許出願（情報不足） |

**`{終端フレーズ}` の決定**（本願自身の翻訳文提出有無）:

| 翻訳文 | 表記 |
|---|---|
| あり（A631 / A632、または documentDescription に「翻訳」を含む） | 外国語書面出願 |
| なし | 特許出願 |

### 5.4 全パターン定義の参照先

`templates/rules_developer.yaml` の `honyo_intro_patterns:` 配下。
このファイルは「人が読むカタログ」（実コードは intro.py が持つ）。

---

## 6. 経過リスト（CHRONO）の生成

### 6.1 書類の 3 タイプ

| Type | 内容 | 例 | 出力接尾辞 |
|---|---|---|---|
| **A** | 庁作成書類 | 拒絶理由通知書、拒絶査定、補正の却下の決定、特許査定、前置報告書 | 「日付け：」 |
| **B** | 出願人提出書類 | 意見書、手続補正書、審判請求書、上申書、応対記録、翻訳文の提出 | 「日　　：」 |
| **C** | 送達情報 | 「原査定の謄本の送達」 | 括弧書き「（{日付}　　：…）」（日付部は §6.5 リダクションが適用） |

### 6.2 日付の取得元（Type ごと）

| Type | Primary（API 経路） | Fallback（API 不通時） |
|---|---|---|
| **A**（庁作成）通常 | `doc_xmls/{appno}/_summary.json` → 各 XML の `<jp:drafting-date>` | `aux_dates/{appno}.json` |
| **A** 前置報告書 (A913) | `zenchi_drafting/{appno}.json` の `drafting_dates_all` | （同上） |
| **B**（提出） | `doc_history_collected/{appno}.json` の各書類の `legalDate` | `aux_dates/{appno}.json` の `table_date` |
| **C**（送達） | `doc_history_collected/{appno}.json` の `A02`（拒絶査定）の `legalDate` | （同上） |

### 6.3 拾う書類（Allow-List）

#### Type A
- `A131` 拒絶理由通知書
- `A02` 拒絶査定
- `A502` 補正の却下の決定
- `A03` 特許査定
- `A913` 前置報告書

#### Type B
- `A632` 翻訳文の提出
- `A53` 意見書
- `A523` 手続補正書（**「（方式）」付きは除外**）
- `A971015` 応対記録
- `C60` 審判請求書
- `A781` 上申書（**前置報告書より前にあるものは除外**：開発者ルール docx コメント [7]）

これ以外の書類は生成器が出力しない。

### 6.4 並び順

1. 日付昇順
2. 同日内では Type A → Type B → Type C の順
3. 同日 Type A 同士:「補正の却下の決定」→「拒絶査定」→「前置報告書」→「拒絶理由通知書」→「特許査定」の順（コーパス調査による経験則）

### 6.5 同日 Type B の結合

同じ日に提出された Type B 書類は 1 行にまとめる。

- 結合内順序（コーパス頻度に基づく）:
  - 誤訳訂正書 → 審判請求書 → 上申書 → 意見書 → 手続補正書 → 翻訳文の提出 → 応対記録
- 連結語: 「Ａ及びＢ」「Ａ、Ｂ及びＣ」（公用文準拠）
  - 例: 「意見書及び手続補正書の提出」「審判請求書、手続補正書及び上申書の提出」

### 6.6 同年・同月・同日リダクション

| 直前行と比較した結果 | 出力 |
|---|---|
| 年が同じ | 「同年 ●月●日」 |
| 年月が同じ | 「同月 ●日」 |
| 完全に同じ日 | 「同日」 |

（「同年同月●日」とは書かない。「同月」は年含む概念のため／開発者ルール docx コメント）

### 6.7 桁揃え

13 全角文字 + `：` で揃える。例：
```
令和　５年　１月　１１日付け：拒絶理由通知書
　　　同年　４月　１３日　　：意見書の提出
```

### 6.8 「（最後）」付与

拒絶理由通知書の本文 XML に「最後の拒絶理由」の記載があれば、書類名に「（最後）」を付ける。
判定は `doc_xmls/{appno}/<xml_filename>` の `<jp:drafting-body>` 内をテキスト検索。

### 6.9 拒絶査定の「（以下「原査定」という。）」付与

最初の拒絶査定行に自動付加する（後段の「原査定」表現の典拠を作るため）。
1 部門ルールで規定。

### 6.10 上申書フィルタ

1 部門ルール docx コメント [7] により、**前置報告書より後の上申書のみ出力**（前置報告書がない案件では上申書をすべて省略）。
理由: 前置報告書に対するリプライ的な上申書のみ手続経緯に書く慣行のため。

---

## 7. オプション項目

### 7.1 起案種別 (HEAD)

| 引数 | 出力する見出し | 用途 |
|---|---|---|
| `z_kakka` (デフォルト) | 「第１　手続の経緯」 | 却下審決（補正の却下を含む審決） |
| `z_no_kakka` | 「１　手続の経緯」 | 却下無し審決 |

判定の自動化は未実装（C-2 課題）。現状はバッチ実行時に手動指定。

### 7.2 優先日定義のスタイル

優先権主張案件で「●年●月●日を以下「優先日」という。」をどこに書くかを 3 スタイルから選択。

| スタイル | 挿入位置 | 例 |
|---|---|---|
| **A**（デフォルト） | INTRO 末尾に「なお、…」で追加 | 「…次のとおりである。なお、2016 年 6 月 24 日を以下「優先日」という。」 |
| **B** | INTRO 内括弧ネスト | 「…（パリ条約による優先権主張　…（以下「優先日」という。）、米国）、…」 |
| **C** | CHRONO 末尾の独立段落 | 経過リストの後に独立して「以下、2016 年 6 月 24 日を「優先日」という。」 |

A が 1 部門ルール準拠の推奨デフォルト。

### 7.3 多世代分割（第 3 世代以降）の DIV_CHAIN

`分割_第3世代以降` の場合、INTRO の後に分割系列リストを挿入：

```
１　出願分割の経緯の概略
　本願は、{本願出願日}にされた…いわゆる第Ｎ世代の分割出願であるところ、…
　最先の出願　：特願●●●●－●●●●●●号（{最先出願日}）
　第１世代分割：特願●●●●－●●●●●●号（{世代1出願日}）
　…
　本願　　　　：特願●●●●－●●●●●●号（{本願出願日}）

２　本願の手続の経緯の概略
　本願の出願後の手続の経緯の概略は、次のとおりである。
{CHRONO 行群}
```

---

## 8. エクスキューズ集（揺れの記録）

複数の起案スタイルが存在し、本生成器がどれかを採用した場合、出力末尾に「採用したルールとその根拠」を薄文字で記録する。

| rule_id | 採用値 | 根拠 |
|---|---|---|
| `prio_def_style` | A（INTRO 末尾「なお接続」） | 1 部門ルール / P1-history.md v0.4 推奨 |
| `no_shutsugan_pattern` | パターンA（「の出願」+ 出願日直後の括弧） | 1 部門ルール / コーパス 13 件 vs 7 件 |
| `connector_oyobi` | 「Ａ及びＢ」「Ａ、Ｂ、Ｃ及びＤ」 | 公用文作成の考え方 |
| `zenchi_inclusion` | 出力する | 1 部門ルール準拠（暫定） |

生成テキストとは別フィールド `text_with_excuses` に出力される。

---

## 9. 主要モジュール一覧

| モジュール | 役割 |
|---|---|
| `generator/keii.py` | 統合エントリーポイント。HEAD + INTRO + CHRONO を組み立てる |
| `generator/classify.py` | 本願の性質判定（11 パターンへの分類） |
| `generator/intro.py` | 冒頭文（INTRO）の文字列展開 |
| `generator/dates.py` | 書類日付の取得（情報源を抽象化） |
| `generator/chronology.py` | 経過リスト（CHRONO）の整形 |
| `generator/jp_dates.py` | 西暦・和暦変換、全角数字変換 |
| `generator/api_remain.py` | JPO API 残量管理（取得スクリプト用） |
| `templates/keii_model.yaml` | 出力モデル全体仕様（人が読むカタログ） |
| `templates/rules_developer.yaml` | 1 部門ルール準拠の 11 パターンテンプレ定義 |

### 9.1 検証・分析ツール

| スクリプト | 用途 |
|---|---|
| `91_generate_intro.py` | 個別案件の INTRO のみ生成（試験用） |
| `92_batch_generate.py` | 全 44 件の手続経緯を生成し、corpus と比較 |
| `93_diff_categorize.py` | diff の自動分類 |
| `A1_classify_check.py` | A-1 該当 8 件の pattern 判定確認 |
| `A2_verify_pct_recall.py` | PCT 国内段階判定の recall 検証 |
| `_compare_A1.py` | A-1 修正前後の per-case 比較 |

---

## 10. 法令・基準・ルールの根拠

| 仕様要素 | 根拠 |
|---|---|
| 出願の分割 | 特許法 44 条 1 項 |
| パリ条約優先権 | パリ条約第 4 条 + 特許法 43 条 |
| パリ条約の例による優先権（台湾） | 特許法 43 条の 3 |
| 国内優先権 | 特許法 41 条 |
| 外国語書面出願 | 特許法 36 条の 2 第 1 項 |
| PCT 日本語特許出願 | 特許法 184 条の 6 第 1 項 |
| PCT 外国語特許出願 + 翻訳文 | 特許法 184 条の 4 第 1 項 |
| 「同月」は年含む概念 | 1 部門ルール docx コメント |
| 翻訳文提出日を経緯に書くのが望ましい | 1 部門ルール docx コメント |
| 国内優先は西暦併記不要 | 1 部門ルール docx コメント |
| 上申書は前置報告書後のみ書く | 1 部門ルール docx コメント [7] |
| 連結語「Ａ及びＢ」 | 公用文作成の考え方 L1041-1051 |
| 手続補正書（方式）は除外 | 1 部門ルール（実体補正のみ書く） |
| PCT 国内段階の判定（5 桁目=5） | 出願番号付番ルール（A1_verify_pct_5kt_rule.py で precision 100% 検証済） |

ルール出典ファイル:
- `knowledge/rules/_comments_extracted.txt` (上記 docx 内のコメントを抽出)
- `knowledge/standards/公用文作成の考え方.txt`
- `knowledge/standards/審決の書き方(2)資料編１（審判便覧）.txt`

---

## 11. 入出力の実例

### 11.1 通常内国出願（最も単純な例）

入力: `2018244177`（パリ条約優先権の通常出願）

doc_history の状態:
- `filingDate` = `20181227`
- `internationalApplicationNumber` = （空）
- `parentApplicationInformation` = （空）
- `priorityRightInformation[]` = `[{parisPriorityDate: "20180104", parisPriorityCountryCd: "EP"}]`

判定:
- 分割 → No
- 本願 PCT → No
- パリ優先 → Yes
→ pattern = **「パリ条約優先権」**

出力:
```
第１　手続の経緯
　本願は、平成３０年１２月２７日（パリ条約による優先権主張　２０１８年１月４日、欧州特許庁）の出願であって、その手続の経緯の概略は、次のとおりである。なお、２０１８年（平成３０年）１月４日を以下「優先日」という。
令和４年　８月１７日付け：拒絶理由通知書
　　　同年　９月２８日　　：意見書及び手続補正書の提出
令和５年　１月２３日付け：拒絶理由通知書（最後）
…
```

### 11.2 分割案件（PCT 原出願・第 2 世代）

入力: `2021168340`

doc_history の状態:
- `filingDate` = `20211013`
- `parentApplicationInformation` = `{parentApplicationNumber: "2020087874", filingDate: "20200520"}`
- `divisionalApplicationInformation[gen=0]` = `{applicationNumber: "2018557229", internationalApplicationNumber: "IB2017050954"}`
- `priorityRightInformation[]` = パリ優先 EP 2016-02-19
- `documentList` に `A631` 翻訳文提出書あり

判定:
- 分割 → Yes
- 世代 = 2
- 最先 PCT → Yes（appno 5 桁目=5 / IB* あり）
- → pattern = **「分割_PCT原出願_第2世代」**
- 言語フレーズ = 「外国語特許出願」（intl=`IB*`）
- 終端フレーズ = 「外国語書面出願」（A631 あり）

出力:
```
第１　手続の経緯
　本願は、２０１７年（平成２９年）２月２０日を国際出願日とする外国語特許出願（特願２０１８－５５７２２９号）の一部を、令和２年５月２０日に新たな特許出願とした特願２０２０－０８７８７４号の一部を、令和３年１０月１３日に新たな外国語書面出願としたものであって（パリ条約による優先権主張　２０１６年２月１９日、欧州特許庁）、その手続の経緯の概略は、次のとおりである。なお、２０１６年（平成２８年）２月１９日を以下「優先日」という。
…
```

---

## 12. 動作確認

```bash
# 個別案件
python -m generator.keii 2018244177
python -m generator.keii 2018244177 --kind z_no_kakka
python -m generator.keii 2018244177 --prio-def-style C

# 全 44 件のバッチ実行 + corpus 比較
python 92_batch_generate.py

# 指定案件のみ
python 92_batch_generate.py --cases 2024-002462 2023-020552

# A-1 修正前後の per-case 比較
python _compare_A1.py
```

実行結果:
- 個別: 標準出力に手続経緯テキスト
- バッチ: `inventory/batch_generate/{case_key}.{gen,diff}.txt` + `_batch_log.tsv`

---

## 13. 現状の精度（2026-05-20 時点）

| 指標 | 値 |
|---|---|
| 全 44 件 ratio mean | 0.881 |
| 全 44 件 ratio median | 0.903 |
| 全 44 件 ratio max | 0.956 |
| 全 44 件 ratio min | 0.558 |
| Type A 起案日（API XML 経由） | 87.1% (122/140) |
| Type B 受領日（doc_history `legalDate`） | 94.3% (247/262) |
| PCT 国内段階判定 | precision 95% / recall 100% |

詳細は `inventory/skeleton_analysis.md` / `inventory/_pct_recall_verify.md` 等を参照。

---

## 14. 配布と環境設定

### 14.1 配布対象と前提

本パッケージはデータ取得プログラムとして配布する。受領者は

- JPO API の利用契約（ID / パスワード）を持っていること
- Python 3.10+ がインストールされていること

を前提とする。

### 14.2 ディレクトリ構成（配布物）

```

├── 0X_*.py / 9X_*.py / AX_*.py    ← データ取得・生成・検証スクリプト
├── generator/                       ← 生成エンジン本体
│   ├── classify.py / intro.py / chronology.py / dates.py / keii.py
│   └── settings.py                  ← 設定ローダ（← 後述）
├── templates/                       ← パターン定義（yaml）
├── corpus/                          ← 確定起案抽出（.gitignore）
├── inventory/                       ← 取得済みデータ（doc_history_collected/, doc_xmls/, …）
├── settings.example.yaml            ← 設定テンプレ（git 管理）
└── settings.yaml                    ← 個人設定（git 管理外）

fetcher/
└── probe_jpo_api.py                 ← JPO API クライアント（外部モジュール）
```

### 14.3 設定ファイル: `settings.yaml`

配布直後にやるべきこと:

```
cp settings.example.yaml settings.yaml
```

その後、`settings.yaml` を自分の環境に合わせて編集する。

#### 設定キー

| キー | 必須／オプション | 内容 |
|---|---|---|
| `jpo_api_credentials_dir` | **必須**（データ取得時） | JPO API 認証情報の格納ディレクトリ。配下に `jpo_api_cred.json`（キー: `username`, `password`）を置く。トークンキャッシュ `jpo_api_token.json` も同所に保存される |
| `inputs_fallback_dir` | オプション | `doc_history.json` の補助探索ディレクトリ。`inventory/doc_history_collected/` で見つからない場合の二次探索先。担当者配布案件と並行運用する場合のみ設定 |

#### 環境変数による上書き

| 環境変数 | 上書きするキー |
|---|---|
| `KEII_JPO_API_DIR` | `jpo_api_credentials_dir` |
| `KEII_INPUTS_FALLBACK_DIR` | `inputs_fallback_dir` |
| `JPO_API_DIR` | `probe_jpo_api.py` が直接読む変数（通常は settings 経由で自動セット） |

優先順位: **環境変数 > settings.yaml > 組み込みデフォルト**。

### 14.4 認証情報ファイルの形式

`jpo_api_credentials_dir` 配下に下記 JSON を置く:

```json
{
  "username": "your_jpo_api_username",
  "password": "your_jpo_api_password"
}
```

ファイル名: `jpo_api_cred.json`。
**このファイルは git 管理しない**（受領者各自で作成）。

### 14.5 設定診断コマンド

```
python3 generator/settings.py
```

設定読み込み結果と各パスの存在判定が表示される。トラブルシュート時に使う。

### 14.6 ファイル間のパス関係（実行ファイルとの関係）

| 実行ファイル | 入力（runtime） | 設定参照 |
|---|---|---|
| `generator/keii.py` (個別生成) | **出願番号のみ**（CLI 引数） | `inputs_fallback_dir` のみ（オプション。設定なくても動く） |
| `92_batch_generate.py` (バッチ) | なし（`inventory/case_appno_map.tsv` を読む） | `inputs_fallback_dir` のみ |
| `04_fetch_doc_history.py` (API 取得) | なし（同マップ） | **`jpo_api_credentials_dir` 必須** |
| `05_fetch_app_docs.py` (API 取得) | なし | **`jpo_api_credentials_dir` 必須** |
| `08_fetch_parent_chain.py` (API 取得) | なし | **`jpo_api_credentials_dir` 必須** |

生成のみであれば API 設定は不要。ただし `inventory/doc_history_collected/{appno}.json` 等の事前取得データが必要。

### 14.7 取得 → 生成 のフロー（新規案件追加時）

```
1. inventory/case_appno_map.tsv に新規案件を追記（case_key, appno を 1 行）
2. python3 04_fetch_doc_history.py     ← doc_history.json 取得
3. python3 05_fetch_app_docs.py        ← doc_xmls/{appno}/ 取得
4. python3 08_fetch_parent_chain.py    ← parent_chains/ 取得
5. python3 10_fetch_aux_appinfo.py    ← aux_appinfo/ 取得（補助ソース）
6. python3 06_fetch_zenchi_drafting.py ← zenchi_drafting/ 取得（補助ソース、前置報告書のみ）
7. python -m generator.keii {appno}    ← 生成
```

補助ソース 取得（06, 10）は API 不要だが、Playwright のセットアップが必要。

### 14.8 ハードコード排除の確認

頒布前監査で以下を確認済み:

- API 認証情報の本体: ハードコートなし（外部ファイル `jpo_api_cred.json` から実行時読み込み）
- API 認証情報ディレクトリのパス: 環境変数 / settings.yaml で上書き可能
- 生成エンジン内の絶対パス: なし（全て `Path(__file__).resolve()` 相対）
- データ取得スクリプト内の絶対パス: なし（settings 経由）

---

## 15. 用語集

| 用語 | 定義 |
|---|---|
| 本願 | 審判の対象出願 |
| 親出願 / 祖父出願 | 分割の元になった出願（直接の親 / その親） |
| PCT 国内段階 | 国際出願を日本に移行した出願（appno 5 桁目=5） |
| 外国語 PCT 出願 | 外国語で行った国際出願。日本で翻訳文提出が必要（184 条の 4） |
| 日本語 PCT 出願 | 日本語で行った国際出願。翻訳文不要（184 条の 6） |
| 外国語書面出願 | 外国語で願書を提出した内国出願（36 条の 2 第 1 項） |
| 分割出願 | 出願の一部を新たな出願として分けたもの（44 条 1 項） |
| パリ条約優先権 | 外国出願を基礎とする優先権主張（43 条） |
| 国内優先権 | 日本国内の先願を基礎とする優先権主張（41 条） |
| Type A 書類 | 庁作成書類（通知書・査定など） |
| Type B 書類 | 出願人提出書類 |
| Type C 書類 | 送達情報 |
| corpus | 確定済み起案文を抽出して保存したもの。生成出力の比較基準 |
| pattern | 11 種類の冒頭文類型のいずれか |
| ratio | 生成文と corpus の文字列一致率（`difflib.SequenceMatcher`） |
| エクスキューズ | 起案ルールに複数の選択肢があるとき、生成器が採用した方とその根拠 |
| doc_history.json | JPO API の経過情報レスポンス |
