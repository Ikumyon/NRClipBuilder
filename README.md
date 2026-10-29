# NRClipBuilder

[English](README.en.md)

Windows x64／Ubuntu 24.04以降のLinux x64向けの、鉄道GIS編集・NIMBY Railsエクスポート用デスクトップアプリです。

## 利用

配布版は[GitHub Releases](https://github.com/Ikumyon/NRClipBuilder/releases)のAssetsからダウンロードできます。Pythonのインストールは不要です。ZIP／tar.gzを展開し、フォルダー全体を保持してNRClipBuilderを起動してください。

以下では、ソースコードから起動し、変更やビルドを行うための手順を説明します。

- N05 ZIP、Shapefile、GeoJSONを読み込み、属性・鉄道種別・BBoxで絞り込みます。
- Leaflet地図で線を描画し、頂点、地上／橋／トンネルを編集できます。Ctrl+Zで編集単位を元に戻します。描画中は右クリックで最後の点を取り消し、ダブルクリックまたは「線を確定」で保存します。
- 背景地図の追加、レイヤーの表示・順序・透明度、BBox履歴、過去の出力履歴の再出力、多言語表示に対応します。
- GeoJSON、Turnout向けOverpass JSON、NIMBY RailsのNRC1形式（モデル226）を出力します。書出しは選択レイヤーとBBoxに対して行います。
- Leaflet本体を同梱しています。背景タイル、OSM取得、標高取得にはネットワークが必要です。

## ソースから起動する

GitHubの「Code → Download ZIP」からソースコードをダウンロードして展開するか、次のコマンドで取得します。以降のコマンドはプロジェクトのルートで実行してください。

```text
git clone https://github.com/Ikumyon/NRClipBuilder.git
cd NRClipBuilder
```

Python 3.14の通常版、Rust、Node.js 24、C/C++ビルド環境を用意し、グローバルのパッケージを継承する仮想環境（`python -m venv --system-site-packages .venv`）を作成し、有効にして実行します。WindowsのC/C++ビルドにはVisual Studio Build Tools、LinuxにはGCC等が必要です。

```text
python -m pip install -r packaging/requirements-build.txt
npm --prefix src/web ci
npm --prefix src/web run build
python -m maturin develop --release --locked
python -m nrclip_builder
```

Python依存は原則として同じマイナーバージョン内の更新を許可します（例：`PySide6~=6.12.0`は6.12系）。Rust・npm依存はcaret範囲を使い、1以上は同じメジャー内、0系は同じマイナー内の更新を許可します。`Cargo.lock`と`package-lock.json`は解決済みバージョンを記録し、更新は`cargo update`／`npm --prefix src/web update`で行います。

データファイルを引数として渡せます。`python -m nrclip_builder example.geojson`

## 変更とビルド

Pythonのコードを変更したら、アプリを再起動します。地図UIを変更したら`npm --prefix src/web run build`、Rustのコードを変更したら`python -m maturin develop --release --locked`を実行してから起動してください。

変更を確認するためのテストと、配布用アプリのビルドは次のコマンドで実行できます。Windows向けはWindows、Linux向けはLinuxでビルドしてください。

```text
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --all --check
npm --prefix src/web test
npm --prefix src/web run format:check
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean packaging/nrclip_builder.spec
```

配布用アプリは`dist/NRClipBuilder/`に生成されます。実行ファイルだけでなく、このフォルダー全体が必要です。

配布物の画面テストには動作するQtWebEngine環境が必要です。Windowsは`dist/NRClipBuilder/NRClipBuilder.exe --smoke-test`、Linuxのヘッドレス実行は`xvfb-run -a dist/NRClipBuilder/NRClipBuilder --smoke-test`を使用します。

ソースからのビルドでは、バージョンは`0.0.0-dev`です。`python -m nrclip_builder --version`で確認できます。

## コード構成

- `src/python/nrclip_builder/`：画面、アプリ状態、サービス、GIS読込・OSM通信。
- `src/native/src/`：型付きデータセット、検索・クリップ、路線接続・曲線計算、NRC1変換、OS固有処理。
- `src/web/src/`：React＋TypeScriptの地図UI、Leafletの描画・編集、型付きQt WebChannel通信。CSSは地図と操作ボタンの表示に使用します。
- `tests/`：バージョンとリリース成果物の検証。`packaging/`は配布設定・ランチャー・ビルド用依存関係・バージョン設定と確認スクリプトです。
- Webの生成物は`src/python/nrclip_builder/resources/map/`へビルドし、wheelと配布物に同梱します。生成物を直接編集せず、`src/web/`のソースを変更して再ビルドしてください。Node.jsは開発・CI専用で、利用者には不要です。
- PyO3の必須拡張をmaturinでビルドします。Rust計算中はPythonから離脱し、Qtのワーカーで実行します。
- データをRust側で保持し、未変更の地物を共有します。選択結果は変更前のスナップショットを保持でき、削除や編集が進んでも実行中の出力は影響されません。
- 地図はQt WebChannelで更新します。ページの再読込やタイトル文字列による通信は使用しません。属性表は必要なページの属性だけを取得します。
- 正規表現はRustの`regex`構文です。先読み・後読み・後方参照などの未対応構文はエラーになります。
- SHPはPRJがあればEPSG:4326に変換し、PRJなしはWGS84として扱います。DBFはCPGを優先し、CPGなしではUTF-8／CP932を判定します。

## 保存と復旧

設定・レイヤーデータ・履歴・描画途中の線は、アプリのルート直下にある`.userdata/session-v1.json`にまとめて自動保存します。開発時はプロジェクトのルート、配布版は実行ファイルのあるフォルダーが基準で、起動時の作業フォルダーには依存しません。ブラウザー用プレビューも`.userdata/`に保存します。保存先の解決・Windowsの隠し属性・ファイルの置換はRustが担当します。保存に失敗すると通知し、以前の保存データを維持します。出力履歴への追加はファイルの出力成功後に行います。

旧Python実装、旧設定・復旧形式、旧起動スクリプトには対応しません。旧ユーザーデータは自動移行も削除もしません。読めない新形式セッションも自動上書きせず、エラーを表示します。

## ライセンス

MIT。`LICENSE`と`THIRD_PARTY_NOTICES.md`を参照してください。NRC1と路線計算の一部はAlex Sørlie氏の[Turnout](https://github.com/SuperManifolds/Turnout)を基にしています。Leafletのライセンスは同梱しています。
