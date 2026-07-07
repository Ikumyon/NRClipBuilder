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

from PySide6.QtCore import Qt, QFile, QLocale
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

DEFAULT_TRANSLATION: dict[str, str] = {}


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

        # 言語リストのスキャン
        self.config_file = get_executable_dir() / "app_config.json"
        self.available_langs = self.scan_languages()

        # 言語初期設定とロード
        self.current_lang = self.detect_initial_lang()
        self.translation: dict[str, Any] = {}
        self.load_localisation(self.current_lang)

        # コンボボックスの動的構築
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        for code, name in self.available_langs:
            self.lang_combo.addItem(name, code)
        
        # 初期選択の設定
        idx = self.lang_combo.findData(self.current_lang)
        if idx != -1:
            self.lang_combo.setCurrentIndex(idx)
        else:
            for i in range(self.lang_combo.count()):
                code = self.lang_combo.itemData(i)
                if code and (code.startswith(self.current_lang) or self.current_lang.startswith(code)):
                    self.lang_combo.setCurrentIndex(i)
                    break
            else:
                self.lang_combo.setCurrentIndex(0)
        self.lang_combo.blockSignals(False)
        self.lang_combo.currentIndexChanged.connect(self.on_lang_combo_changed)

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
        self.history_table.itemSelectionChanged.connect(self.on_history_selected)
        self.delete_history_btn.clicked.connect(self.delete_history)
        self.clear_history_btn.clicked.connect(self.clear_history)

        self.load_history_from_file()
        self.load_config_from_file()

        # メニューアクションの接続
        self.action_open.triggered.connect(self.open_file)
        self.action_export_geojson.triggered.connect(self.export_geojson)
        self.action_export_turnout.triggered.connect(self.export_turnout_json)
        self.action_export_nrclip.triggered.connect(self.export_nrclip)
        self.action_open_html.triggered.connect(self.open_preview_in_browser)
        self.action_quit.triggered.connect(self.close)

        # 初期ローカライズの適用
        self.retranslate_ui()
        self.info_label.setText(self.tr_msg("info_label_empty"))

        # ステータスバーと初期化
        self.statusBar().showMessage(self.tr_msg("msg_select_file"))
        self._refresh_map([])

    def scan_languages(self) -> list[tuple[str, str]]:
        loc_dir = get_executable_dir() / "localisation"
        langs = []
        if loc_dir.exists():
            for json_file in loc_dir.glob("*.json"):
                code = json_file.stem.lower()
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    name = data.get("lang_name", code)
                    langs.append((code, name))
                except Exception:
                    pass

        def sort_key(item):
            code, name = item
            if code == "ja-jp":
                return (0, name)
            if code == "en-us":
                return (1, name)
            return (2, name)
            
        return sorted(langs, key=sort_key)

    def detect_initial_lang(self) -> str:
        # スキャンされた言語コードの一覧を取得
        valid_codes = [code for code, _ in self.available_langs] if hasattr(self, "available_langs") else []
        
        # ヘルパー: 与えられた言語コードをスキャンされたリストとマッチングする
        def match_lang(target: str) -> str | None:
            target = target.lower().replace("_", "-")
            # 1. 完全一致
            if target in valid_codes:
                return target
            # 2. 前方一致 (言語コードの先頭部分、例: "ja" や "ja-jp")
            target_base = target.split("-")[0]
            for code in valid_codes:
                if code == target_base or code.split("-")[0] == target_base:
                    return code
            return None

        # 1. 設定ファイルからの読み込みとマッチング
        if self.config_file.exists():
            try:
                config = json.loads(self.config_file.read_text(encoding="utf-8"))
                if "lang" in config:
                    matched = match_lang(str(config["lang"]))
                    if matched:
                        return matched
            except Exception:
                pass
                
        # 2. システムロケールからの読み込みとマッチング
        sys_lang = QLocale.system().name().lower().replace("_", "-")
        matched = match_lang(sys_lang)
        if matched:
            return matched
            
        # 3. デフォルト (リストにある en-us を優先、無ければ最初の言語)
        if "en-us" in valid_codes:
            return "en-us"
        if valid_codes:
            return valid_codes[0]
        return "ja-jp"

    def load_localisation(self, lang: str) -> None:
        valid_codes = [code for code, _ in self.available_langs] if hasattr(self, "available_langs") else []
        # 短縮名（"ja"など）やシステムロケール名を正規化する
        lang_norm = lang.lower().replace("_", "-")
        if lang_norm not in valid_codes:
            # 前方一致で検索
            base = lang_norm.split("-")[0]
            for code in valid_codes:
                if code.split("-")[0] == base:
                    lang = code
                    break
            else:
                lang = lang_norm
        else:
            lang = lang_norm

        self.current_lang = lang
        loc_dir = get_executable_dir() / "localisation"
        loc_file = loc_dir / f"{lang}.json"
        self.translation = {}
        if loc_file.exists():
            try:
                self.translation = json.loads(loc_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Localisation load error: {e}")
        


    def tr_msg(self, key: str) -> str:
        return self.translation.get(key, DEFAULT_TRANSLATION.get(key, key))

    def retranslate_ui(self) -> None:
        self.label_lang.setText(self.tr_msg("lang_label"))
        self.filter_group.setTitle(self.tr_msg("filter_group"))
        self.label_keyword.setText(self.tr_msg("label_keyword"))
        self.label_fields.setText(self.tr_msg("label_fields"))
        self.label_exclude.setText(self.tr_msg("label_exclude"))
        self.exclude_edit.setPlaceholderText(self.tr_msg("exclude_placeholder"))
        self.label_condition.setText(self.tr_msg("label_condition"))
        self.or_radio.setText(self.tr_msg("or_radio"))
        self.and_radio.setText(self.tr_msg("and_radio"))
        self.label_option.setText(self.tr_msg("label_option"))
        self.regex_check.setText(self.tr_msg("regex_check"))
        self.line_only_check.setText(self.tr_msg("line_only_check"))
        self.apply_btn.setText(self.tr_msg("apply_btn"))
        self.label_bbox.setText(self.tr_msg("label_bbox"))
        self.use_bbox_check.setText(self.tr_msg("use_bbox_check"))
        self.label_bbox_min.setText(self.tr_msg("label_bbox_min"))
        self.min_lon_edit.setPlaceholderText(self.tr_msg("min_lon_placeholder"))
        self.min_lat_edit.setPlaceholderText(self.tr_msg("min_lat_placeholder"))
        self.label_bbox_max.setText(self.tr_msg("label_bbox_max"))
        self.max_lon_edit.setPlaceholderText(self.tr_msg("max_lon_placeholder"))
        self.max_lat_edit.setPlaceholderText(self.tr_msg("max_lat_placeholder"))
        self.clear_bbox_btn.setText(self.tr_msg("clear_bbox_btn"))
        self.label_scale.setText(self.tr_msg("label_scale"))
        self.label_spline_tolerance.setText(self.tr_msg("label_spline_tolerance"))
        self.label_junction_spacing.setText(self.tr_msg("label_junction_spacing"))
        self.label_max_spacing.setText(self.tr_msg("label_max_spacing"))
        self.label_straight_tolerance.setText(self.tr_msg("label_straight_tolerance"))
        self.info_group.setTitle(self.tr_msg("info_group"))
        self.log.setPlaceholderText(self.tr_msg("log_placeholder"))
        
        self.tabs.setTabText(0, self.tr_msg("tab_map"))
        self.tabs.setTabText(1, self.tr_msg("tab_table"))
        self.tabs.setTabText(2, self.tr_msg("tab_history"))
        self.delete_history_btn.setText(self.tr_msg("delete_history_btn"))
        self.clear_history_btn.setText(self.tr_msg("clear_history_btn"))
        
        self.menu_file.setTitle(self.tr_msg("menu_file"))
        self.action_open.setText(self.tr_msg("action_open"))
        self.action_export_geojson.setText(self.tr_msg("action_export_geojson"))
        self.action_export_turnout.setText(self.tr_msg("action_export_turnout"))
        self.action_export_nrclip.setText(self.tr_msg("action_export_nrclip"))
        self.action_open_html.setText(self.tr_msg("action_open_html"))
        self.action_quit.setText(self.tr_msg("action_quit"))
        
        headers = self.tr_msg("history_headers")
        if isinstance(headers, list):
            self.history_table.setHorizontalHeaderLabels(headers)
        
        self.update_window_title()
        self.update_info_label()
        
        if hasattr(self, "map_widget"):
            self.map_widget.retranslate_map(self.current_lang.split("-")[0], self.translation)

    def update_window_title(self) -> None:
        src_name = self.store.source_path.name if self.store.source_path else ""
        title_base = self.tr_msg("window_title")
        if src_name:
            self.setWindowTitle(f"{title_base} - {src_name}")
        else:
            self.setWindowTitle(title_base)

    def on_lang_combo_changed(self, index: int) -> None:
        lang = self.lang_combo.itemData(index)
        if lang:
            self.load_localisation(lang)
            self.retranslate_ui()
            self.save_config_to_file()


    def log_msg(self, msg: str) -> None:
        self.log.appendPlainText(msg)
        self.statusBar().showMessage(msg)

    def open_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_msg("dialog_open_file_title"),
            str(self.last_output_dir),
            "GIS data (*.zip *.shp *.geojson *.json);;All files (*.*)",
        )
        if not path_str:
            return
        self.load_path(Path(path_str))

    def load_path(self, path: Path) -> None:
        try:
            self.store.cleanup()
            self.update_window_title()
            self.log.clear()
            self.log_msg(self.tr_msg("msg_loading").format(path=path))
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.store = load_any(path)
            QApplication.restoreOverrideCursor()
            self.last_output_dir = path.parent
            self.log_msg(self.tr_msg("msg_load_success").format(count=len(self.store.features)))
            if self.store.fields:
                fields_str = ", ".join(self.store.fields[:60]) + (" ..." if len(self.store.fields) > 60 else "")
                self.log_msg(self.tr_msg("msg_fields").format(fields=fields_str))
            if self.store.crs_note:
                self.log_msg(self.store.crs_note)
            self.update_info_label()
            self.apply_filter()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.log_msg(self.tr_msg("dialog_error") + str(exc))
            QMessageBox.critical(self, self.tr_msg("msg_load_error_title"), f"{exc}\n\n{traceback.format_exc()}")

    def update_info_label(self) -> None:
        if not hasattr(self, "translation") or not self.translation:
            return
        if not self.store.features and not self.store.source_path:
            self.info_label.setText(self.tr_msg("info_label_empty"))
            return
        src = self.store.source_path or Path("-")
        fmt = self.tr_msg("info_label_format")
        self.info_label.setText(
            fmt.format(
                src=src,
                total=len(self.store.features),
                filtered=len(self.filtered),
                fields=len(self.store.fields),
                crs=self.store.crs_note
            )
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
            self.log_msg(self.tr_msg("msg_filtered_count").format(filtered=len(self.filtered), total=len(self.store.features)))
            self.update_info_label()
            self._refresh_map(self.filtered)
            self._refresh_table(self.filtered)
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_filter_error_title"), f"{exc}\n\n{traceback.format_exc()}")

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
            "lang": self.current_lang,
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
            "spline_tolerance": self.spline_tolerance_spin.value(),
            "junction_spacing": self.junction_spacing_spin.value(),
            "max_spacing": self.max_spacing_spin.value(),
            "straight_tolerance": self.straight_tolerance_spin.value(),
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
            self.spline_tolerance_spin.setValue(config.get("spline_tolerance", 5.0))
            self.junction_spacing_spin.setValue(config.get("junction_spacing", 30.0))
            self.max_spacing_spin.setValue(config.get("max_spacing", 200.0))
            self.straight_tolerance_spin.setValue(config.get("straight_tolerance", 0.5))
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
        selected_ranges = self.history_table.selectedRanges()
        if not selected_ranges:
            QMessageBox.information(self, self.tr_msg("msg_delete_history_title"), self.tr_msg("msg_delete_history_warn"))
            return
        
        selected_rows = set()
        for r in selected_ranges:
            for row in range(r.topRow(), r.bottomRow() + 1):
                selected_rows.add(row)
                
        if not selected_rows:
            return

        deleted_names = []
        # インデックスがズレないように降順で削除
        for row in sorted(selected_rows, reverse=True):
            if 0 <= row < len(self.history_data):
                deleted = self.history_data.pop(row)
                deleted_names.append(deleted.get("name", ""))
                
        self.save_history_to_file()
        self.refresh_history_table()
        self.update_map_history_bboxes()
        
        if len(deleted_names) == 1:
            self.log_msg(self.tr_msg("msg_history_deleted").format(name=deleted_names[0]))
        else:
            msg = f"Deleted {len(deleted_names)} history entries" if self.current_lang.startswith("en") else f"履歴を {len(deleted_names)} 件削除しました。"
            self.log_msg(msg)
            
        self.clear_bbox()

    def clear_history(self) -> None:
        if not self.history_data:
            return
        confirm = QMessageBox.question(
            self,
            self.tr_msg("msg_clear_history_title"),
            self.tr_msg("msg_clear_history_confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            self.history_data = []
            self.save_history_to_file()
            self.refresh_history_table()
            self.update_map_history_bboxes()
            self.log_msg(self.tr_msg("msg_history_cleared"))
            self.clear_bbox()

    def check_bbox_required(self) -> bool:
        if not self.use_bbox_check.isChecked() or not self.selected_bbox:
            QMessageBox.warning(
                self,
                self.tr_msg("msg_bbox_warn_title"),
                self.tr_msg("msg_bbox_warn")
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
        title = self.tr_msg("msg_filtered_count").split(":")[0] if features else "No data"
        if self.store.source_path:
            title = self.store.source_path.name
        self.map_widget.set_geojson(
            {"type": "FeatureCollection", "features": features},
            title=title,
            lang=self.current_lang.split("-")[0],
            translation=self.translation
        )

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
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = []
        for f in self.filtered:
            clipped_geom = clip_geometry_to_bbox(f.get("geometry") or {}, self.selected_bbox)
            if clipped_geom:
                new_feat = f.copy()
                new_feat["geometry"] = clipped_geom
                export_features.append(new_feat)
        if not export_features:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_data_in_bbox"))
            return
        default = self.last_output_dir / "filtered_lines.geojson"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self.tr_msg("msg_save_geojson"),
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
            self.log_msg(self.tr_msg("msg_save_success_geojson").format(path=path).replace("\n", " "))
            QMessageBox.information(self, self.tr_msg("msg_save_success_title"), self.tr_msg("msg_save_success_geojson").format(path=path))
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_save_error_title"), str(exc))

    def export_turnout_json(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
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
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_line_in_bbox"))
            return
        default = self.last_output_dir / "turnout_tracks.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self.tr_msg("msg_save_turnout"),
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
            self.log_msg(self.tr_msg("msg_save_success_turnout").format(path=path, nodes=nodes, ways=ways).replace("\n", " "))
            QMessageBox.information(self, self.tr_msg("msg_save_success_title"), self.tr_msg("msg_save_success_turnout").format(path=path, nodes=nodes, ways=ways))
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_save_error_title"), str(exc))

    def export_nrclip(self) -> None:
        if not self.check_bbox_required():
            return
        if not self.filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
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
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_line_in_bbox"))
            return
        default = self.last_output_dir / "tracks.nrclip"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            self.tr_msg("msg_save_nrclip"),
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
            spline_tolerance = self.spline_tolerance_spin.value()
            junction_spacing = self.junction_spacing_spin.value()
            max_spacing = self.max_spacing_spin.value()
            straight_tolerance = self.straight_tolerance_spin.value()
            data = geojson_to_nrclip_bytes(
                line_features, name, scale_x, scale_y,
                spline_tolerance=spline_tolerance,
                junction_spacing=junction_spacing,
                max_spacing=max_spacing,
                straight_tolerance=straight_tolerance
            )
            path.write_bytes(data)
            self.last_output_dir = path.parent
            self.add_to_history(name, self.selected_bbox)
            self.log_msg(self.tr_msg("msg_save_success_nrclip").format(path=path).replace("\n", " "))
            QMessageBox.information(self, self.tr_msg("msg_save_success_title"), self.tr_msg("msg_save_success_nrclip").format(path=path))
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_save_error_title"), f"{exc}\n\n{traceback.format_exc()}")

    def open_preview_in_browser(self) -> None:
        import webbrowser
        if self.map_widget.last_html_path and self.map_widget.last_html_path.exists():
            webbrowser.open(self.map_widget.last_html_path.as_uri())
        else:
            QMessageBox.information(self, self.tr_msg("msg_preview_title"), self.tr_msg("msg_no_preview_html"))

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
