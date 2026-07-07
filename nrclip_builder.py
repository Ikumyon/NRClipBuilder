#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N05 / SHP / GeoJSON Map Filter Exporter

A small PySide6 desktop app that loads railway line data, previews it on a Leaflet map,
filters by attributes, and exports filtered GeoJSON or Turnout-compatible Overpass JSON.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QFile
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QTableWidgetItem,
)

from core.geojson import geojson_to_overpass
from core.nrclip import geojson_to_nrclip_bytes

# リファクタリングによる分割モジュールのインポート
from core.geo_loader import FeatureStore, load_any, collect_fields, safe_str, write_json
from core.geo_filter import (
    filter_features,
    geometry_is_line,
    clip_geometry_to_bbox,
)
from core.widgets import UiLoader, MapWidget, HAS_WEBENGINE

APP_TITLE = "NRClipBuilder"
MAX_TABLE_ROWS = 300


def get_resource_path(relative_path: str) -> Path:
    """PyInstallerの一時展開先フォルダ(_MEIPASS)を考慮してリソースの絶対パスを取得する"""
    try:
        base_path = Path(sys._MEIPASS)
    except AttributeError:
        base_path = Path(__file__).parent
    return base_path / relative_path


def get_executable_dir() -> Path:
    """PyInstallerでパッケージ化されている場合は実行ファイルの場所、開発時はスクリプトの場所を返す"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = FeatureStore()
        self.filtered: list[dict[str, Any]] = []
        self.last_output_dir = Path.cwd()

        # UIのロード
        loader = UiLoader(self)
        ui_path = get_resource_path("ui/main_window.ui")
        ui_file = QFile(str(ui_path))
        if not ui_file.open(QFile.ReadOnly):
            raise RuntimeError(f"UIファイルを開けませんでした: {ui_path}")
        loader.load(ui_file)
        ui_file.close()

        # テーブルのプロパティ設定
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        # 地図プレビューウィジェットの追加
        self.map_widget = MapWidget()
        self.map_tab.layout().addWidget(self.map_widget)

        # イベントの接続
        self.apply_btn.clicked.connect(self.apply_filter)

        # BBox 関連の初期化とシグナル接続
        self.selected_bbox = None
        self.history_file = get_executable_dir() / "bbox_history.json"
        self.config_file = get_executable_dir() / "app_config.json"
        self.history_data = []

        if HAS_WEBENGINE:
            self.map_widget.web.titleChanged.connect(self.on_title_changed)
            self.map_widget.web.loadFinished.connect(self.on_map_load_finished)
        self.clear_bbox_btn.clicked.connect(self.clear_bbox)
        self.use_bbox_check.stateChanged.connect(self.on_use_bbox_changed)

        self.min_lon_edit.editingFinished.connect(self.on_coordinate_edited)
        self.min_lat_edit.editingFinished.connect(self.on_coordinate_edited)
        self.max_lon_edit.editingFinished.connect(self.on_coordinate_edited)
        self.max_lat_edit.editingFinished.connect(self.on_coordinate_edited)

        # 履歴テーブルの初期設定
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["名前", "最小経度", "最小緯度", "最大経度", "最大緯度", "出力日時"])
        self.history_table.itemSelectionChanged.connect(self.on_history_selected)
        self.delete_history_btn.clicked.connect(self.delete_history)

        self.load_history_from_file()
        self.load_config_from_file()

        # メニューアクションの接続
        self.action_open.triggered.connect(self.open_file)
        self.action_export_geojson.triggered.connect(self.export_geojson)
        self.action_export_turnout.triggered.connect(self.export_turnout_json)
        self.action_export_nrclip.triggered.connect(self.export_nrclip)
        self.action_open_html.triggered.connect(self.open_preview_in_browser)
        self.action_quit.triggered.connect(self.close)

        # ステータスバーと初期化
        self.statusBar().showMessage("ファイルを開いてください。対応: N05 ZIP / SHP / GeoJSON")
        self._refresh_map([])

    def log_msg(self, msg: str) -> None:
        self.log.appendPlainText(msg)
        self.statusBar().showMessage(msg)

    def open_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "データを開く",
            str(self.last_output_dir),
            "GIS data (*.zip *.shp *.geojson *.json);;All files (*.*)",
        )
        if not path_str:
            return
        self.load_path(Path(path_str))

    def load_path(self, path: Path) -> None:
        try:
            self.store.cleanup()
            self.setWindowTitle(f"{APP_TITLE} - {path.name}")
            self.log.clear()
            self.log_msg(f"読み込み中: {path}")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.store = load_any(path)
            QApplication.restoreOverrideCursor()
            self.last_output_dir = path.parent
            self.log_msg(f"読み込み完了: {len(self.store.features):,} features")
            if self.store.fields:
                self.log_msg("フィールド: " + ", ".join(self.store.fields[:60]) + (" ..." if len(self.store.fields) > 60 else ""))
            self.log_msg(self.store.crs_note)
            self.update_info_label()
            self.apply_filter()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.log_msg("エラー: " + str(exc))
            QMessageBox.critical(self, "読み込みエラー", f"{exc}\n\n{traceback.format_exc()}")

    def update_info_label(self) -> None:
        src = self.store.source_path or Path("-")
        self.info_label.setText(
            f"元ファイル: {src}\n"
            f"全件: {len(self.store.features):,}\n"
            f"抽出: {len(self.filtered):,}\n"
            f"フィールド数: {len(self.store.fields)}\n"
            f"{self.store.crs_note}"
        )

    def apply_filter(self) -> None:
        try:
            features = self.store.features
            if self.line_only_check.isChecked():
                features = [f for f in features if geometry_is_line(f.get("geometry") or {})]
            self.filtered = filter_features(
                features,
                keywords_text=self.keyword_edit.text(),
                fields_text=self.field_edit.text(),
                regex=self.regex_check.isChecked(),
                match_all=self.and_radio.isChecked(),
                exclude_text=self.exclude_edit.text(),
            )
            self.log_msg(f"抽出: {len(self.filtered):,} / {len(self.store.features):,} features")
            self.update_info_label()
            self._refresh_map(self.filtered)
            self._refresh_table(self.filtered)
        except Exception as exc:
            QMessageBox.critical(self, "フィルターエラー", f"{exc}\n\n{traceback.format_exc()}")

    def on_use_bbox_changed(self, state: int) -> None:
        if self.use_bbox_check.isChecked():
            self.on_coordinate_edited()
        else:
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self.update_map_history_bboxes()
            self.update_map_active_bbox()

    def on_coordinate_edited(self) -> None:
        try:
            w = float(self.min_lon_edit.text())
            s = float(self.min_lat_edit.text())
            e = float(self.max_lon_edit.text())
            n = float(self.max_lat_edit.text())
            self.selected_bbox = (w, s, e, n)
            self.use_bbox_check.setChecked(True)
            self.update_map_active_bbox()
        except ValueError:
            pass

    def update_map_active_bbox(self) -> None:
        if self.selected_bbox:
            w, s, e, n = self.selected_bbox
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript(f"window.setActiveBounds({w}, {s}, {e}, {n});")
        else:
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def update_map_history_bboxes(self) -> None:
        if not HAS_WEBENGINE:
            return
        page = self.map_widget.web.page()
        page.runJavaScript("window.clearHistoryBounds();")
        for item in self.history_data:
            bbox = item.get("bbox")
            name = item.get("name", "")
            if bbox and len(bbox) == 4:
                w, s, e, n = bbox
                name_esc = name.replace("'", "\\'")
                page.runJavaScript(f"window.addHistoryBounds({w}, {s}, {e}, {n}, '{name_esc}');")

    def on_title_changed(self, title: str) -> None:
        if title.startswith("BBOX:"):
            parts = title[5:].split(",")
            if len(parts) == 4:
                try:
                    w, s, e, n = map(float, parts)
                    self.selected_bbox = (w, s, e, n)
                    self.min_lon_edit.setText(f"{w:.7f}")
                    self.min_lat_edit.setText(f"{s:.7f}")
                    self.max_lon_edit.setText(f"{e:.7f}")
                    self.max_lat_edit.setText(f"{n:.7f}")
                    self.use_bbox_check.setChecked(True)
                except ValueError:
                    pass
        elif title.startswith("SELECT_HISTORY:"):
            parts = title[15:].split(",")
            if len(parts) == 5:
                name = parts[0]
                try:
                    w, s, e, n = map(float, parts[1:])
                    self.selected_bbox = (w, s, e, n)
                    self.min_lon_edit.setText(f"{w:.7f}")
                    self.min_lat_edit.setText(f"{s:.7f}")
                    self.max_lon_edit.setText(f"{e:.7f}")
                    self.max_lat_edit.setText(f"{n:.7f}")
                    self.use_bbox_check.setChecked(True)
                    self.update_map_active_bbox()
                    self.select_history_row_by_name(name)
                except ValueError:
                    pass

    def select_history_row_by_name(self, name: str) -> None:
        for r in range(self.history_table.rowCount()):
            item = self.history_table.item(r, 0)
            if item and item.text() == name:
                self.history_table.selectRow(r)
                break

    def clear_bbox(self) -> None:
        self.selected_bbox = None
        self.min_lon_edit.clear()
        self.min_lat_edit.clear()
        self.max_lon_edit.clear()
        self.max_lat_edit.clear()
        self.use_bbox_check.setChecked(False)
        if HAS_WEBENGINE:
            self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def save_config_to_file(self) -> None:
        config = {
            "keywords": self.keyword_edit.text(),
            "fields": self.field_edit.text(),
            "exclude": self.exclude_edit.text(),
            "regex": self.regex_check.isChecked(),
            "match_all": self.and_radio.isChecked(),
            "line_only": self.line_only_check.isChecked(),
            "use_bbox": self.use_bbox_check.isChecked(),
            "bbox": self.selected_bbox,
            "scale_x": self.scale_x_spin.value(),
            "scale_y": self.scale_y_spin.value(),
        }
        try:
            self.config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def load_config_from_file(self) -> None:
        if not self.config_file.exists():
            return
        try:
            config = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.keyword_edit.setText(config.get("keywords", ""))
            self.field_edit.setText(config.get("fields", ""))
            self.exclude_edit.setText(config.get("exclude", ""))
            self.regex_check.setChecked(config.get("regex", False))
            if config.get("match_all", True):
                self.and_radio.setChecked(True)
            else:
                self.or_radio.setChecked(True)
            self.line_only_check.setChecked(config.get("line_only", False))
            
            bbox = config.get("bbox")
            if bbox and len(bbox) == 4:
                self.selected_bbox = tuple(bbox)
                self.min_lon_edit.setText(f"{bbox[0]:.7f}")
                self.min_lat_edit.setText(f"{bbox[1]:.7f}")
                self.max_lon_edit.setText(f"{bbox[2]:.7f}")
                self.max_lat_edit.setText(f"{bbox[3]:.7f}")
            
            self.use_bbox_check.setChecked(config.get("use_bbox", False))
            self.scale_x_spin.setValue(config.get("scale_x", 1.0))
            self.scale_y_spin.setValue(config.get("scale_y", 1.0))
        except Exception:
            pass

    def load_history_from_file(self) -> None:
        self.history_data = []
        if self.history_file.exists():
            try:
                self.history_data = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.refresh_history_table()

    def save_history_to_file(self) -> None:
        try:
            self.history_file.write_text(json.dumps(self.history_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def refresh_history_table(self) -> None:
        self.history_table.setRowCount(0)
        for item in self.history_data:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            bbox = item.get("bbox", [0, 0, 0, 0])
            w, s, e, n = bbox
            self.history_table.setItem(row, 0, QTableWidgetItem(item.get("name", "")))
            self.history_table.setItem(row, 1, QTableWidgetItem(f"{w:.7f}"))
            self.history_table.setItem(row, 2, QTableWidgetItem(f"{s:.7f}"))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{e:.7f}"))
            self.history_table.setItem(row, 4, QTableWidgetItem(f"{n:.7f}"))
            self.history_table.setItem(row, 5, QTableWidgetItem(item.get("timestamp", "")))
        self.history_table.resizeColumnsToContents()

    def on_history_selected(self) -> None:
        selected = self.history_table.selectedRanges()
        if not selected:
            return
        row = selected[0].topRow()
        if 0 <= row < len(self.history_data):
            item = self.history_data[row]
            bbox = item.get("bbox")
            if bbox and len(bbox) == 4:
                w, s, e, n = bbox
                self.selected_bbox = (w, s, e, n)
                self.min_lon_edit.setText(f"{w:.7f}")
                self.min_lat_edit.setText(f"{s:.7f}")
                self.max_lon_edit.setText(f"{e:.7f}")
                self.max_lat_edit.setText(f"{n:.7f}")
                self.use_bbox_check.setChecked(True)
                self.update_map_active_bbox()

    def delete_history(self) -> None:
        selected = self.history_table.selectedRanges()
        if not selected:
            QMessageBox.information(self, "削除", "削除する履歴を選択してください。")
            return
        row = selected[0].topRow()
        if 0 <= row < len(self.history_data):
            deleted = self.history_data.pop(row)
            self.save_history_to_file()
            self.refresh_history_table()
            self.update_map_history_bboxes()
            self.log_msg(f"履歴を削除しました: {deleted.get('name')}")
            if self.selected_bbox == tuple(deleted.get("bbox", [])):
                self.clear_bbox()

    def check_bbox_required(self) -> bool:
        if not self.use_bbox_check.isChecked() or not self.selected_bbox:
            QMessageBox.warning(
                self,
                "警告",
                "出力範囲が設定されていないか、有効になっていません。\n"
                "地図上の「範囲選択」ボタンを押してドラッグで囲むか、"
                "数値を入力して「出力範囲を使用」にチェックを入れてください。"
            )
            return False
        return True

    def add_to_history(self, name: str, bbox: tuple[float, float, float, float]) -> None:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.history_data = [item for item in self.history_data if item.get("name") != name and tuple(item.get("bbox", [])) != bbox]
        self.history_data.insert(0, {
            "name": name,
            "bbox": list(bbox),
            "timestamp": now
        })
        self.save_history_to_file()
        self.refresh_history_table()
        self.update_map_history_bboxes()

    def _refresh_map(self, features: list[dict[str, Any]]) -> None:
        title = "抽出結果" if features else "No data"
        if self.store.source_path:
            title = self.store.source_path.name
        self.map_widget.set_geojson({"type": "FeatureCollection", "features": features}, title=title)

    def _refresh_table(self, features: list[dict[str, Any]]) -> None:
        fields = collect_fields(features) if features else self.store.fields
        if not fields:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        display_fields = fields[:80]
        rows = min(len(features), MAX_TABLE_ROWS)
        self.table.clear()
        self.table.setColumnCount(len(display_fields))
        self.table.setHorizontalHeaderLabels(display_fields)
        self.table.setRowCount(rows)
        for r, feat in enumerate(features[:rows]):
            props = feat.get("properties") or {}
            for c, field_name in enumerate(display_fields):
                item = QTableWidgetItem(safe_str(props.get(field_name, "")))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def export_geojson(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = []
        for f in self.filtered:
            clipped_geom = clip_geometry_to_bbox(f.get("geometry") or {}, self.selected_bbox)
            if clipped_geom:
                new_feat = f.copy()
                new_feat["geometry"] = clipped_geom
                export_features.append(new_feat)
        if not export_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内にデータがありません。")
            return
        default = self.last_output_dir / "filtered_lines.geojson"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "GeoJSONを保存",
            str(default),
            "GeoJSON (*.geojson);;JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            write_json(path, {"type": "FeatureCollection", "features": export_features}, pretty=True)
            self.last_output_dir = path.parent
            self.add_to_history(path.stem, self.selected_bbox)
            self.log_msg(f"GeoJSON保存: {path} ({len(export_features)} features)")
            QMessageBox.information(self, "保存完了", f"GeoJSONを保存しました。\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_turnout_json(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = []
        for f in self.filtered:
            clipped_geom = clip_geometry_to_bbox(f.get("geometry") or {}, self.selected_bbox)
            if clipped_geom:
                new_feat = f.copy()
                new_feat["geometry"] = clipped_geom
                export_features.append(new_feat)
        line_features = [f for f in export_features if geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内に線データがありません。")
            return
        default = self.last_output_dir / "turnout_tracks.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Turnout用JSONを保存",
            str(default),
            "JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            data = geojson_to_overpass(line_features)
            write_json(path, data, pretty=False)
            self.last_output_dir = path.parent
            self.add_to_history(path.stem, self.selected_bbox)
            elements = data.get("elements", [])
            ways = sum(1 for e in elements if e.get("type") == "way")
            nodes = sum(1 for e in elements if e.get("type") == "node")
            self.log_msg(f"Turnout用JSON保存: {path} ({nodes} nodes, {ways} ways)")
            QMessageBox.information(self, "保存完了", f"Turnout用JSONを保存しました。\n{path}\n{nodes} nodes, {ways} ways")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def export_nrclip(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, "出力", "出力対象がありません。")
            return
        export_features = []
        for f in self.filtered:
            clipped_geom = clip_geometry_to_bbox(f.get("geometry") or {}, self.selected_bbox)
            if clipped_geom:
                new_feat = f.copy()
                new_feat["geometry"] = clipped_geom
                export_features.append(new_feat)
        line_features = [f for f in export_features if geometry_is_line(f.get("geometry") or {})]
        if not line_features:
            QMessageBox.information(self, "出力", "選択された出力範囲内に線データがありません。")
            return
        default = self.last_output_dir / "tracks.nrclip"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "nrclipを保存",
            str(default),
            "NIMBY Rails Clipboard (*.nrclip);;All files (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            name = path.stem
            scale_x = self.scale_x_spin.value()
            scale_y = self.scale_y_spin.value()
            data = geojson_to_nrclip_bytes(line_features, name, scale_x, scale_y)
            path.write_bytes(data)
            self.last_output_dir = path.parent
            self.add_to_history(name, self.selected_bbox)
            self.log_msg(f"nrclip保存: {path} ({len(line_features)} features)")
            QMessageBox.information(self, "保存完了", f"nrclipを保存しました。\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", f"{exc}\n\n{traceback.format_exc()}")

    def open_preview_in_browser(self) -> None:
        import webbrowser
        if self.map_widget.last_html_path and self.map_widget.last_html_path.exists():
            webbrowser.open(self.map_widget.last_html_path.as_uri())
        else:
            QMessageBox.information(self, "プレビュー", "まだプレビューHTMLがありません。")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.save_config_to_file()
        self.store.cleanup()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = MainWindow()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists():
            win.load_path(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
