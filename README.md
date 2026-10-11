# inf-notebook-viewer

inf-notebookの記録を、楽曲名で検索して確認するWebビューアーです。SP・DP・DP BATTLEと全難易度に対応し、ベストスコア・最少BP・使用オプション・日付順のプレイ履歴を表示します。

JSON変換・形式検証・ローカル画面に加え、記録を手動で同期するコマンドとGitHub Pagesへのデプロイワークフローを実装しています。スマホ実機での確認、配信用ファイル名によるキャッシュ管理、自動更新は後続の段階です。

## ローカルで確認する

Python 3.10以降を使用します。外部Pythonパッケージのインストールは不要です。

初回のみ、設定例をコピーしてください。すでに`config.local.json`がある場合は上書きせず、その内容を使います。

```powershell
cd C:\Users\ryo\git\inf-notebook-viewer
Copy-Item config.example.json config.local.json
```

設定ファイル内のパスは、設定ファイルのあるディレクトリを基準に解決します。

```json
{
  "recordsPath": "../inf-notebook/records",
  "musicTablePath": "../inf-notebook/resources/musictable1.2.res",
  "outputPath": "data"
}
```

`musicTablePath`は信頼できるローカルのinf-notebook楽曲テーブルを指定してください。pickleを読み込むため、第三者から受け取った不明なファイルは指定しないでください。`null`にすると、レベル情報なしで生成できます。

以下のコマンドで記録を生成・検証し、サーバーを起動します。

```powershell
python tools/export_records.py --config config.local.json
python tools/validate_data.py data
python -m http.server 8000 --bind 127.0.0.1
```

ブラウザで **http://127.0.0.1:8000/web/** を開きます。PowerShellは起動したままにしてください。終了するときは`Ctrl+C`を押します。8000番ポートが使用中なら8001等へ変更し、URLのポート番号も合わせてください。

この起動方法では同じPCから確認できます。HTMLファイルをダブルクリックして開く方法（`file://`）は、JavaScriptモジュールとJSON読み込みへのブラウザ制限があるため対応していません。

記録が増えたら変換コマンドを再実行し、画面の「データを更新」を押してください。サーバー起動中は別のPowerShellから変換コマンドを実行できます。

## GitHub Pagesへ公開する

publicリポジトリを使用します。初回のみ、GitHubのリポジトリの **Settings → Pages → Build and deployment → Source** を **GitHub Actions** に設定してください。PCのGitは同じリポジトリを`origin`として登録し、認証してpushできる状態にします。

`.github/workflows/pages.yml`を含むコードをmainにpushすると、Actionsがテスト・データ検証・サイト生成を行い、成功した場合だけPagesへ公開します。PRでは検証のみ実行します。main以外から手動実行した場合も公開しません。

公開URLは通常 **https://YOUR_USERNAME.github.io/inf-notebook-viewer/** です。ルートから`web/`へ移動し、楽曲検索画面が開きます。このリポジトリの場合は **https://ryowatanabe.github.io/inf-notebook-viewer/** です。forkではユーザー名とリポジトリ名を読み替えてください。

forkして利用する場合、最初はfork元の閲覧用データもコピーされます。自分のPCに合わせて`config.local.json`を作り、自分の記録からdataを再生成・検証し、コミット・pushしてからPagesを有効にしてください。

公開が完了したかはGitHubの **Actions → Validate and deploy Pages** の実行結果で確認できます。Source設定前にpushして公開が失敗した場合は、設定後に失敗した実行の **Re-run all jobs** を選ぶか、mainを選んで **Run workflow** を実行してください。

ビルドでは`web/`の必要な4ファイルと検証済み`data/`だけをコピーします。設定ファイル、Pythonツール、元のrecordsやリソースは配信サイトに含めません。GitHub-hostedの標準Ubuntu runnerを使い、Pages用artifactの保持期間は1日です。

### 手動で記録を同期する

コードの変更は先に通常のコミット・pushで送信し、その後に以下を実行します。

```powershell
cd C:\Users\ryo\git\inf-notebook-viewer
pwsh -File tools/sync.ps1
```

PowerShell 7の`pwsh`がない場合や、Pythonから直接実行したい場合も、同じ処理を使えます。

```powershell
python tools/sync_records.py --config config.local.json
```

設定ファイルは通常viewerのルートの`config.local.json`を使います。別の設定は`pwsh -File tools/sync.ps1 -Config other-config.json`で指定できます。相対パスはカレントディレクトリではなくviewerのルートから解決します。Pythonの実行名を変える場合は`-Python py`等を指定できます。

同期は次の順に行います。

1. main、origin、Gitの変更状態を確認する。
2. origin/mainを取得する。ローカルが遅れている場合はfast-forwardだけを行う。
3. PCの記録を生成し、閲覧用JSON全体を検証する。
4. dataだけに変更がある場合、「プレイ記録を更新」としてコミットする。
5. 新規または未送信のデータコミットがある場合にpushする。

同期の出力先はリポジトリ直下の`data`に限定します。ステージ済みの変更やdata以外の未コミット変更がある場合は停止します。未送信コミットにコード変更がある場合も停止するため、先に通常の`git push`を行ってください。

履歴が分岐した場合、生成が失敗した場合、remoteにも更新がありローカルのdataも変更されている場合は、変更内容を確認してから再実行してください。同期ツールをfast-forwardで更新した場合は、新しいコードで実行するため再実行を案内します。force pushや未コミット変更の破棄は行いません。

pushに失敗した場合は、生成データと未送信コミットを保持します。接続・認証の問題を解消して同じコマンドを再実行すると、記録に新たな変更がなくても未送信コミットを送信します。push成功は公開完了を意味しないため、表示されたActionsのリンクで結果を確認してください。Pagesに記録が反映された後、画面の「データを更新」を押します。

同時に複数の同期を起動すると後から起動したものは停止します。同期が強制終了してロックが残った場合は、同期プロセスが動いていないことを確認してから以下を実行してください。

```powershell
git rev-parse --git-path viewer-sync.lock
# 上で表示されたファイルだけを削除（通常のcheckoutの場合）
Remove-Item -LiteralPath .git/viewer-sync.lock
```

自動更新はまだ登録しません。後続の段階で、この同期コマンドをWindowsタスクスケジューラから実行する仕組みを追加します。

### 公開用サイトをローカルで確認する

```powershell
python tools/build_site.py --output .preview/site
python -m http.server 8000 --bind 127.0.0.1 --directory .preview/site
```

**http://127.0.0.1:8000/** を開きます。`.preview/site`はGit管理対象外です。公開用サイトのルートと、`web/`・`data/`の相対パスの配置を確認できます。

## 画面の使い方

1. 楽曲名の一部を入力します。英字の大小、全角・半角、ひらがな・カタカナ、ラテン文字のアクセントを同一視します。濁点・半濁点は区別します。
2. プレイスタイルと難易度を選びます。楽曲は名前で絞り込み、選んだ譜面の記録の有無とレベルを一覧に表示します。
3. 楽曲を選ぶと、ベスト記録と履歴を表示します。記録のない譜面はその旨を表示します。

ベスト値はinf-notebookの保存値を使用します。オプション不明と正規は区別し、数値の0もそのまま表示します。履歴は50件ずつ追加表示でき、検索結果も50曲ずつ追加表示できます。曲名やオプションの長い文字列は折り返して表示します。

プレイスタイル・難易度はブラウザに保存します。選択した曲・譜面・検索語はURLにも反映するため、ブックマークできます。

## データと検証

- `data/index.json`: 記録のある楽曲の検索一覧、譜面の有無、レベル、件数。
- `data/songs/<SHA-256>.json`: 1楽曲の全スタイル・難易度のベストと全履歴。
- `data/manifest.json`: 形式バージョン、生成日時、最新プレイ日時、件数、内容ハッシュ。

変換ツールはinf-notebookを起動・importせず、元の記録を変更しません。summary/recentや画像は取り込みません。全ファイルを読み取り・検証した後に出力を入れ替えるため、不正なJSONや履歴欠損時は既存出力を保持します。入力が変わらなければ、生成日時を含め出力は変更しません。出力先は生成データ専用ディレクトリにしてください。

保存中の記録は再試行しますが、全楽曲の厳密な同時点スナップショットは保証しません。確実な更新にはプレイ記録の保存が落ち着いてから実行してください。

`validate_data.py`は元の記録がなくても実行でき、値の型、日時、一覧と楽曲ファイルの参照、履歴の順序、件数、内容ハッシュを検証します。

## テスト

```powershell
python -m unittest discover -s tests -v
node --test tests/format.test.mjs
```

Node.jsは検索・表示関数のテストにのみ必要です。ビューアーの実行には不要です。Pythonのテストには一時的なローカルGitリポジトリ間での同期テストを含むため、Gitが必要です。テストでGitHubへのpushは行いません。

実データで1,776曲・14,875件を変換し、出力約5.0MB、再実行時の変更なしを確認しました。PCのブラウザで部分一致検索、DPの譜面選択、ベスト記録と履歴の表示を確認済みです。iOS/Safari・Android/Chromeの実機検証、Pages配信時のハッシュ付きURLは後続段階で確認・実装します。

詳細は[設計書](docs/architecture.md)を参照してください。
