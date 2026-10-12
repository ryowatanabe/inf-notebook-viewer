# inf-notebook-viewer

inf-notebookのプレイ記録を楽曲名で検索し、ベスト記録・使用オプション・プレイ履歴をスマホから確認するWebビューアーです。SP・DP・DP BATTLEと全難易度に対応します。

[公開サイト](https://ryowatanabe.github.io/inf-notebook-viewer/)

PCで記録を閲覧用JSONへ変換し、データ更新PRを自動作成します。必須チェック成功後に自動マージされ、GitHub Pagesへ公開されます。公開した記録はPCを停止しても閲覧できます。ログインは不要で、閲覧用データはリポジトリとサイトに公開されます。

- [初回セットアップ](#初回セットアップ)
- [手動で同期する](#手動で同期する)
- [ローカルで確認する](#ローカルで確認する)
- [同期で困ったとき](#同期で困ったとき)

## 初回セットアップ

### 必要なもの

| 用途 | 必要なもの |
| --- | --- |
| 記録の変換・検証・サイト生成 | Python 3.10以降（外部パッケージ不要） |
| 記録の同期 | Git、[GitHub CLI](https://cli.github.com/)、更新先リポジトリへの書き込み権限 |
| PowerShell版の同期コマンド | PowerShell 7（`pwsh`）。Pythonから直接実行する場合は不要 |
| 元データ | PC上のinf-notebookの`records`。レベル表示には楽曲テーブルも使用 |
| 閲覧 | オンライン環境のブラウザ。iOS/Safari・Android/Chromeを想定 |
| JavaScriptのテスト | Node.js（閲覧・同期には不要） |

すでにローカルで動作確認・GitHub認証・リポジトリ設定を済ませている場合は、[手動で同期する](#手動で同期する)へ進んでください。

### 1. リポジトリを用意する

自分の記録を公開するpublicリポジトリを使用し、ローカルの`origin`に登録します。ほかの利用者はこのリポジトリをforkし、自分のforkをcloneしてください。下記の`YOUR_USERNAME`は自分のGitHubユーザー名に置き換えます。

```powershell
git clone https://github.com/YOUR_USERNAME/inf-notebook-viewer.git
cd inf-notebook-viewer
git remote -v
```

fetch先とpush先は同じリポジトリにします。Gitのコミット用の名前・メールアドレスも設定済みである必要があります。同期ツールはデフォルトブランチが`main`のリポジトリを対象にしています。

fork直後はfork元のdataもコピーされます。自分のデータへの更新PRをマージしてからPagesを公開してください。forkでActionsが無効になっている場合は、GitHubのActions画面からワークフローを有効にします。

### 2. PCの設定を作る

viewerのルートで、設定例をコピーします。すでに`config.local.json`がある場合は上書きしないでください。

```powershell
Copy-Item config.example.json config.local.json
```

```json
{
  "recordsPath": "../inf-notebook/records",
  "musicTablePath": "../inf-notebook/resources/musictable1.2.res",
  "outputPath": "data"
}
```

| 設定 | 内容 |
| --- | --- |
| `recordsPath` | inf-notebookが記録を保存しているディレクトリ |
| `musicTablePath` | レベル情報を取得する楽曲テーブル。不要なら`null` |
| `outputPath` | 通常は`data`。同期ではviewer直下のdataを指定する必要がある |

相対パスは**設定ファイルがあるディレクトリ**を基準に解決します。上の例はinf-notebookとviewerが同じ親ディレクトリにある場合の設定です。楽曲テーブルのファイル名・バージョンは自分のPCにあるものを指定してください。自動選択はしません。

楽曲テーブルはpickleを読み込むため、信頼できるローカルのinf-notebookのファイルだけを指定してください。`musicTablePath`が`null`の場合、レベルは不明として表示します。inf-notebook本体の変更や、同期中の起動は不要です。

`config.local.json`はGit管理対象外です。設定・認証情報・元のrecords・画像を同期ツールがGitHubへアップロードすることはありません。

### 3. GitHub CLIにログインする

```powershell
gh auth login
gh auth status
```

GitHub CLIで更新先リポジトリを操作でき、Gitでも更新ブランチをpushできる状態にしてください。HTTPSのGit認証が未設定の場合は`gh auth setup-git`を使用できます。

### 4. GitHubのルールを設定する

**Settings → General → Pull Requests → Allow auto-merge**を有効にします。マージ方法は、許可されているものからsquash・merge commit・rebaseの順に使用します。

mainの保護ルールは次の設定を推奨します。

- PR経由のマージを必須にする。
- ステータスチェック`build`の成功を必須にする。
- 管理者のバイパスも許可しない。
- force pushとブランチ削除を禁止する。
- 自分ひとりで自動同期する場合、他者の承認レビューは必須にしない。

自動マージは保護ルールを回避しません。他者の承認を必須にした場合、データ更新PRも承認されるまで待機します。`build`が候補に出ない場合は、既存のワークフローを実行してチェックを作成してから設定してください。

コードやドキュメントの改修も、変更を始める前に作業ブランチを作成し、そのブランチでコミットしてください。mainへの直接コミット・pushは行わず、PRと`build`チェックを経てマージします。エージェント向けの作業指示は[AGENTS.md](AGENTS.md)に記載しています。

```powershell
git switch main
git pull --ff-only
git switch -c codex/your-change
```

既に作業ブランチで改修中の場合は、そのブランチで作業を続けてください。

### 5. GitHub Pagesを有効にする

**Settings → Pages → Build and deployment → Source**を**GitHub Actions**に設定します。forkの場合は自分の記録へ置き換えてから有効にしてください。

PRをmainへマージすると、Actionsがテスト・データ検証・サイト生成を行い、成功した場合だけPagesへ公開します。PRでは検証のみ実行し、main以外からの手動実行でも公開しません。Source設定前にデプロイが失敗した場合は、設定後にActionsで再実行できます。

公開URLは通常`https://YOUR_USERNAME.github.io/inf-notebook-viewer/`です。リポジトリ名を変更した場合はその部分も読み替えてください。サイトのルートから`web/`へ移動して検索画面が開きます。

## 手動で同期する

### 1. mainを最新にする

以下はこのPCのパスです。別の場所にcloneした場合は読み替えてください。

```powershell
cd C:\Users\ryo\git\inf-notebook-viewer
git status --short
```

変更が表示された場合は、内容を確認し、必要な変更を作業ブランチのPRで取り込んでから進んでください。同期は、ステージ済み・未コミット・未送信の変更がないmainで実行します。

```powershell
git switch main
git pull --ff-only
```

同期ツールの改修PRをマージした後も、この手順で新しいコードを取り込んでください。

### 2. 同期コマンドを実行する

```powershell
pwsh -File tools/sync.ps1
```

PowerShell 7がない場合は、同じ処理をPythonから実行できます。

```powershell
python tools/sync_records.py --config config.local.json
```

**同期前に`export_records.py`を別途実行する必要はありません。** 同期コマンドが生成・検証まで行います。単独のexportコマンドはmainのdataを変更するため、そのままでは同期を実行できなくなります。

別の設定やPythonの実行名は次のように指定できます。`-Config`の相対パスはviewerのルートから解決し、その設定内の相対パスは設定ファイルの場所から解決します。

```powershell
pwsh -File tools/sync.ps1 -Config other-config.json -Python py
```

### 3. PRのマージと公開を確認する

記録に変更があれば、コマンドが更新PRのURLとActionsのURLを表示します。

1. 更新PRで必須チェック`build`が成功し、PRがマージされることを確認します。
2. GitHubの**Actions → Validate and deploy Pages**で、mainの実行の`deploy`が成功することを確認します。
3. 公開サイトを開き、必要に応じて「データを更新」を押します。

PR上の`deploy`がスキップされるのは正常です。デプロイはmainへのマージ後に実行します。コマンドが成功しても、PRのマージ・Pagesの公開はまだ進行中の場合があります。

確認先: [PR一覧](https://github.com/ryowatanabe/inf-notebook-viewer/pulls)、[Actions](https://github.com/ryowatanabe/inf-notebook-viewer/actions)、[公開サイト](https://ryowatanabe.github.io/inf-notebook-viewer/)。forkでは自分のリポジトリ・サイトを確認してください。

### 次回の同期

同じ手順を繰り返します。更新待ちのPRがある場合は、同じブランチ・PRへ追加の記録を送信します。マージ済みなら新しいPRを作成します。変更がなければ新しいPRは作りません。更新待ちの記録がmainと同じ内容に戻った場合は、不要になったPRを閉じます。

mainへ直接pushすることはありません。mainのdataはPRマージ後のpull、または次回同期時のfast-forwardで更新されます。自動マージ後すぐにローカルのdataも確認したい場合は`git pull --ff-only`を実行してください。

## ローカルで確認する

GitHubへの送信やサーバーの外部公開をせず、同じPCのブラウザで確認できます。次の2種類はアクセス先が異なります。

### ソースの画面を確認する

```powershell
cd C:\Users\ryo\git\inf-notebook-viewer
python -m http.server 8000 --bind 127.0.0.1
```

[http://127.0.0.1:8000/web/](http://127.0.0.1:8000/web/)を開きます。**末尾の`/web/`が必要です。** この起動方法でルートURLを開くとDirectory Listingになります。画面のコードを変更したら再読み込みしてください。

独立したローカル変換を試す場合は、以下を実行できます。ただし、この操作はGit管理対象のdataを変更します。変更は作業ブランチのPRで取り込むか、同期とは別の検証用checkoutで行ってください。

```powershell
python tools/export_records.py --config config.local.json
python tools/validate_data.py data
```

### 公開用サイトを確認する

```powershell
python tools/build_site.py --output .preview/site
python -m http.server 8000 --bind 127.0.0.1 --directory .preview/site
```

[http://127.0.0.1:8000/](http://127.0.0.1:8000/)を開きます。この方法ではルートURLから検索画面へ移動します。**ソースやdataを変更した場合は、buildコマンドを再実行してから再読み込みしてください。** `.preview/site`はGit管理対象外です。

どちらもPowerShellを起動したままにし、終了は`Ctrl+C`で行います。8000番ポートが使用中なら8001等へ変更し、URLのポートも合わせてください。HTMLの直接起動（`file://`）はJavaScriptモジュールとJSONの読み込み制限があるため対応していません。

## 画面の使い方

1. 楽曲名を入力し、「検索方法」で部分一致（初期設定）または完全一致を選びます。短い曲名だけを探す場合は完全一致が便利です。どちらも英字の大小、全角・半角、ひらがな・カタカナ、ラテン文字のアクセントを同一視し、濁点・半濁点は区別します。入力の前後の空白は無視し、検索語が空の場合は全曲を表示します。
2. SP・DP・DP BATTLEとB/N/H/A/Lを選択します。検索対象は記録のある楽曲です。選択した譜面に記録がない場合は、その旨を表示します。
3. 楽曲を選ぶと、ベストスコア・最少BPと各オプション、日付順のプレイ履歴を表示します。

履歴と検索結果は50件ずつ追加表示できます。十分な幅がある場合は履歴を1件1行で表示し、収まらない場合は2段の配置へ折り返します。長いオプションは必要に応じて折り返します。中くらいのウィンドウ幅では楽曲一覧と履歴を上下に配置し、履歴の横幅を確保します。スコア・BP・速度は列を揃えて表示します。

クリアランプは色付きマークで表示します。マウスを合わせるとランプ名を確認でき、スクリーンリーダー用の名前も保持しています。

| ランプ | 色 |
| --- | --- |
| FAILED | 赤 |
| A-CLEAR | ピンク |
| E-CLEAR | 緑 |
| CLEAR | 水色 |
| H-CLEAR | 白 |
| EXH-CLEAR | 赤・黄 |
| F-COMBO | 虹色 |
| 不明・未対応の値 | 灰色 |

ベスト値はinf-notebookの保存値を使用し、履歴から再計算しません。オプション不明と正規は区別し、数値の0もそのまま表示します。DP BATTLEのレベルはSPの譜面に準拠します。

プレイスタイル・難易度・検索方法はブラウザに保存します。選択した曲・譜面・検索語・検索方法はURLにも反映するため、ブックマークできます。「データ生成」はJSONを生成した日時、「最新プレイ」は履歴の最新日時であり、Pages公開日時とは異なります。

## 同期で困ったとき

| 状況 | 対応 |
| --- | --- |
| main以外にいる | 必要な変更をPRで取り込んだ後、`git switch main`・`git pull --ff-only`を実行する |
| ステージ済み・未コミット・未送信の変更がある | `git status`とコミット履歴を確認する。必要な変更を作業ブランチのPRへ保存し、mainを最新にする |
| GitHub CLIがない・認証に失敗する | ghをインストールし、`gh auth login`・`gh auth status`で確認する |
| 自動マージが無効 | Settings → GeneralでAllow auto-mergeを有効にする |
| push・PR作成・自動マージ予約に失敗した | 接続・認証・設定を確認し、同じ同期コマンドを再実行する。ブランチとコミットは保持される |
| 更新PRがマージせず閉じられている | 続けて使用するならGitHubでPRを再開してから再実行する |
| `build`が失敗する | Actionsの失敗したステップを確認する。元の記録が不正な場合は修正して再同期する |
| 自動マージが待機したまま | PRで必須チェック・承認・競合など、未完了のマージ条件を確認する |
| 履歴が分岐した・競合している | Gitの状態を確認して手動で解決する。ツールはforce pushや変更破棄を行わない |
| 同期ツールの更新を取り込んだため再実行を求められた | 同じコマンドをもう一度実行する |
| 同期成功後も画面が古い | PRのマージとmainのdeploy成功を確認し、「データを更新」を押す。CSS/JSの変更は`Ctrl+F5`、公開用プレビューは再buildする |

PR作成後に応答が失われた場合も、次回は既存PRを検索して重複作成を避けます。自動マージは検証したコミットを指定して予約し、データ更新PRへコード変更が混ざった場合はローカルとCIで止めます。

同期状態と専用worktreeはGit管理領域に保存します。mainの作業内容は生成・コミットで書き換えません。マージ済みworktreeは変更がないことを確認して片付け、ローカルの更新ブランチとコミットは復旧用に残します。

```powershell
git rev-parse --git-path viewer-sync-worktree
git rev-parse --git-path viewer-sync-state.json
```

専用worktreeの競合や手動変更は、通常のcheckoutなら`git -C .git/viewer-sync-worktree status`で確認して解決します。状態ファイルとworktreeは、保持している更新を確認せずに削除しないでください。

同時起動はロックで抑止します。強制終了してロックが残った場合だけ、同期プロセスが動いていないことを確認してから、表示されたロックファイルを削除します。

```powershell
git rev-parse --git-path viewer-sync.lock
# 通常のcheckoutの場合。上で表示されたパスが異なる場合は読み替える
Remove-Item -LiteralPath .git/viewer-sync.lock
```

## データと実装

- `data/index.json`: 記録のある楽曲の検索一覧、譜面の有無、レベル、件数。
- `data/songs/<SHA-256>.json`: 1楽曲の全スタイル・全難易度のベストと履歴。
- `data/manifest.json`: 形式バージョン、生成日時、最新プレイ日時、件数、内容ハッシュ。
- `tools/export_records.py`: inf-notebookの記録を読む単独の変換ツール。
- `tools/validate_data.py`: 元の記録なしで、形式・日時・参照・履歴の順序・件数・内容ハッシュを検証。
- `tools/sync.ps1` / `tools/sync_records.py`: 更新PRの作成・再利用・自動マージ予約。
- `tools/build_site.py`: 検証済みdataと必要な画面ファイルだけを公開用サイトへ配置。
- `.github/workflows/pages.yml`: PRの検証と、mainのPagesデプロイ。

変換ツールはinf-notebookのモジュールをimportせず、元の記録を変更しません。summary/recentや画像は取り込みません。全ファイルを読み取り・検証した後に出力を入れ替え、不正なJSONや履歴欠損時は既存出力を保持します。入力が変わらなければ生成日時を含め出力は変更しません。

保存中の記録は再試行しますが、全楽曲の厳密な同時点スナップショットは保証しません。確実な更新には記録の保存が落ち着いてから実行してください。

サイトにはwebの必要な4ファイルと閲覧用JSONを配置します。ローカル設定やPythonツールは配信しません。Gitには過去のデータも残るため、現在のdataから削除しても過去のコミットからは閲覧可能です。

GitHub Actionsは標準Ubuntu runnerを使用し、Pages用artifactの保持期間は1日です。publicリポジトリで無料運用する構成です。

## 検証と今後の対応

```powershell
python -m unittest discover -s tests -v
node --test tests/format.test.mjs
```

Pythonのテストには一時的なローカルGitリポジトリと疑似GitHubを使った同期テストが含まれます。テストで実際のGitHubへのpushやPR作成は行いません。

実データ1,776曲・14,875履歴を変換し、出力約5.0MB、再実行時の変更なしを確認済みです。ローカルブラウザの検索・履歴表示、mainへのpush禁止下のPR同期、失敗後の再試行、squashマージ後の次回同期も検証しています。

Windowsタスクスケジューラへの自動登録、ハッシュ付き配信用URLによるキャッシュ管理、オフライン対応は未実装です。スマホ実機での表示確認も今後の対象です。現在の同期はコマンドによる手動実行です。

詳細は[設計書](docs/architecture.md)を参照してください。
