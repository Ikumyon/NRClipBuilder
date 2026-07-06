# NRClipBuilder

PySide6で作った、GISデータ（N05 / SHP / GeoJSON）用の地図プレビュー・フィルタリング兼エクスポーターです。
地図上に描画された路線データをキーワードや属性で絞り込み、NIMBY Rails用のクリップボードデータ（`.nrclip`）や、Turnout（Rust版）の `import_orm` に引き渡せる Overpass風JSON などを書き出すことができます。

## 主な機能

- **各種GISデータの入力**:
  - 国土数値情報（N05等）の ZIP ファイル
  - Shapefile (`.shp`)
  - GeoJSON (`.geojson` / `.json`)
- **高度なフィルタリング**:
  - 属性名、キーワード、除外キーワードによるOR/AND条件抽出
  - 線データ（LineString等）のみの抽出オプション
- **地図上でのプレビュー**:
  - QWebEngineView と Leaflet.js を使用したインタラクティブな地図プレビュー
  - 国土地理院地図（標準・写真）および OpenStreetMap (OSM) の背景切り替え
- **多様なエクスポートフォーマット**:
  - GeoJSON
  - Turnout用 Overpass JSON風データ
  - **NIMBY Rails クリップボード形式 (`.nrclip`)** の直接書き出し (zstd圧縮 + wyhash_nrc1チェックサム自動計算)

## インストール

Windows環境にて、以下のバッチファイルを実行してください。

```bat
install_requirements.bat
```

または、手動で仮想環境に依存ライブラリをインストールします。

```bat
python -m pip install -r requirements.txt
```

## 起動方法

```bat
run.bat
```

または、Pythonコマンドで直接起動します。

```bat
python nrclip_builder.py
```

データファイルを引数として指定して、直接開くことも可能です。

```bat
python nrclip_builder.py C:\path\to\N05-20_GML.zip
```

---

## 謝辞とライセンス (Credits & License)

本プロジェクトは、Alex Sørlie 氏が開発した Rust 製ツール [Turnout](https://github.com/itsalex/Turnout) のデータ構造、チェックサムアルゴリズム、およびシリアライズロジックを参考に Python (PySide6) で再実装・機能拡張したものです。

- 元プロジェクト: [Turnout](https://github.com/itsalex/Turnout) (Copyright (c) 2025 Alex Sørlie, MIT License)
- 本ソフトウェアは MIT License の下で公開されています。詳細は `LICENSE` ファイルを参照してください。
