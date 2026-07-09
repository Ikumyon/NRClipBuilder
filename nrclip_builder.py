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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QFile, QLocale
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QTableWidgetItem,
    QDialog,
    QFormLayout,
    QLineEdit,
    QDialogButtonBox,
    QVBoxLayout,
    QTreeWidgetItem,
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


@dataclass
class LayerEntry:
    """ツリーに表示される各レイヤーの共通構造。"""
    name: str
    checked: bool = True
    removable: bool = True
    on_check_changed: Callable[[bool], None] | None = None
    on_remove: Callable[[], None] | None = None


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


class AddMapDialog(QDialog):
    def __init__(self, parent=None, translation=None):
        super().__init__(parent)
        self.translation = translation or {}
        self.setWindowTitle(self.tr_msg("dialog_add_map_title", "背景地図を追加"))
        self.resize(400, 200)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(self.tr_msg("dialog_map_name_placeholder", "例: 国土地理院地図"))
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(self.tr_msg("dialog_map_url_placeholder", "例: https://example.com/{z}/{x}/{y}.png"))
        self.attr_edit = QLineEdit(self)
        self.attr_edit.setPlaceholderText(self.tr_msg("dialog_map_attr_placeholder", "例: &copy; Map providers"))

        form.addRow(self.tr_msg("dialog_label_map_name", "背景地図名"), self.name_edit)
        form.addRow(self.tr_msg("dialog_label_map_url", "タイルURL"), self.url_edit)
        form.addRow(self.tr_msg("dialog_label_map_attr", "著作権表記"), self.attr_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def tr_msg(self, key: str, fallback: str) -> str:
        val = self.translation.get(key, fallback)
        return val if val else fallback

    def accept(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url:
            QMessageBox.warning(self, self.tr_msg("msg_validation_error", "警告"), self.tr_msg("msg_validation_empty", "名前とURLを入力してください。"))
            return
        if not all(p in url for p in ["{z}", "{x}", "{y}"]):
            QMessageBox.warning(self, self.tr_msg("msg_validation_error", "警告"), self.tr_msg("msg_validation_invalid_url", "タイルURLには {z}, {x}, {y} のプレースホルダーを含める必要があります。"))
            return
        super().accept()

    def get_data(self) -> tuple[str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.url_edit.text().strip(),
            self.attr_edit.text().strip()
        )



class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = FeatureStore()
        self.filtered: list[dict[str, Any]] = []
        self.last_output_dir = Path.cwd()

        # UIのロード
        loader = UiLoader(self)
        icon_path = get_resource_path("icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
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
        # self.apply_btn.clicked.connect(self.apply_filter)

        # BBox 関連の初期化とシグナル接続
        self.selected_bbox = None
        self.map_loaded = False
        self.history_file = get_executable_dir() / "bbox_history.json"
        self.history_data = []

        # 背景地図の初期化
        self.custom_maps: list[dict[str, str]] = []
        self.active_maps: set[str] = {"OpenStreetMap (OSM)"}
        
        # 独立したレイヤーの管理
        self.layers: dict[str, FeatureStore] = {}
        self.layers_filtered: dict[str, list[dict[str, Any]]] = {}
        self.active_layers: set[str] = set()
        self.show_history_bounds = False

        # 統一レイヤーエントリ
        self.layer_entries: list[LayerEntry] = []

        self.add_map_btn.clicked.connect(self.on_add_map_clicked)
        self.add_line_btn.clicked.connect(self.on_add_line_clicked)
        self.add_history_btn.clicked.connect(self.on_add_history_clicked)
        self.remove_layer_btn.clicked.connect(self.on_remove_layer_clicked)
        
        self.layer_tree.itemChanged.connect(self.on_layer_item_changed)
        self.layer_tree.itemSelectionChanged.connect(self.on_layer_selection_changed)


        if HAS_WEBENGINE:
            self.map_widget.web.titleChanged.connect(self.on_title_changed)
            self.map_widget.web.loadFinished.connect(self.on_map_load_finished)
        self.clear_bbox_btn.clicked.connect(self.clear_bbox)

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
        self.action_open.setVisible(False)
        self.action_export_geojson.triggered.connect(self.export_geojson)
        self.action_export_turnout.triggered.connect(self.export_turnout_json)
        self.action_export_nrclip.triggered.connect(self.export_nrclip)
        self.action_open_html.triggered.connect(self.open_preview_in_browser)
        self.action_quit.triggered.connect(self.close)

        # 初期ローカライズの適用
        self.retranslate_ui()
        self.update_layer_tree()

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
        self.export_group.setTitle(self.tr_msg("export_group"))
        self.label_keyword.setText(self.tr_msg("label_keyword"))
        self.label_fields.setText(self.tr_msg("label_fields"))
        self.label_exclude.setText(self.tr_msg("label_exclude"))
        self.exclude_edit.setPlaceholderText(self.tr_msg("exclude_placeholder"))
        self.label_condition.setText(self.tr_msg("label_condition"))
        self.or_radio.setText(self.tr_msg("or_radio"))
        self.and_radio.setText(self.tr_msg("and_radio"))
        self.label_option.setText(self.tr_msg("label_option"))
        self.regex_check.setText(self.tr_msg("regex_check"))
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
        self.add_line_btn.setText(self.tr_msg("add_line_btn"))
        self.add_history_btn.setText(self.tr_msg("add_history_btn"))
        self.add_map_btn.setText(self.tr_msg("add_map_btn"))
        self.remove_layer_btn.setText(self.tr_msg("remove_layer_btn"))
        self.layer_group.setTitle(self.tr_msg("layer_group"))
        
        self.railway_rail_check.setText(self.tr_msg("railway_rail"))
        self.railway_subway_check.setText(self.tr_msg("railway_subway"))
        self.railway_tram_check.setText(self.tr_msg("railway_tram"))
        self.railway_light_rail_check.setText(self.tr_msg("railway_light_rail"))
        self.railway_monorail_check.setText(self.tr_msg("railway_monorail"))
        self.railway_funicular_check.setText(self.tr_msg("railway_funicular"))
        self.railway_abandoned_check.setText(self.tr_msg("railway_abandoned"))

        
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
        
        if hasattr(self, "map_widget"):
            self.map_widget.retranslate_map(self.current_lang.split("-")[0], self.translation)

    def update_window_title(self) -> None:
        active_layer_name = self.get_selected_layer_name()
        src_name = active_layer_name if active_layer_name else ""
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
        self.statusBar().showMessage(msg, 5000)

    def load_path(self, path: Path) -> None:
        try:
            self.log_msg(self.tr_msg("msg_loading").format(path=path))
            QApplication.setOverrideCursor(Qt.WaitCursor)
            store = load_any(path)
            QApplication.restoreOverrideCursor()
            
            layer_name = path.name
            for f in store.features:
                if "properties" not in f:
                    f["properties"] = {}
                f["properties"]["_source"] = layer_name
                
            self.layers[layer_name] = store
            self.active_layers.add(layer_name)
            self.last_output_dir = path.parent
            
            self.log_msg(self.tr_msg("msg_load_success").format(count=len(store.features)))
            if store.crs_note:
                self.log_msg(store.crs_note)
            
            self.update_layer_tree()
            self.select_tree_layer(layer_name)
            self.apply_filter()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.log_msg(self.tr_msg("dialog_error") + str(exc))
            QMessageBox.critical(self, self.tr_msg("msg_load_error_title"), f"{exc}\n\n{traceback.format_exc()}")

    def apply_filter(self) -> None:
        try:
            total_count = 0
            for name, store in self.layers.items():
                features = [f for f in store.features if geometry_is_line(f.get("geometry") or {})]
                filtered = filter_features(
                    features,
                    keywords_text=self.keyword_edit.text(),
                    fields_text=self.field_edit.text(),
                    regex=self.regex_check.isChecked(),
                    match_all=self.and_radio.isChecked(),
                    exclude_text=self.exclude_edit.text(),
                )
                self.layers_filtered[name] = filtered
                total_count += len(store.features)

            map_features = []
            filtered_count = 0
            for name in self.active_layers:
                if name in self.layers_filtered:
                    map_features.extend(self.layers_filtered[name])
                    filtered_count += len(self.layers_filtered[name])
            self._refresh_map(map_features)

            self.log_msg(self.tr_msg("msg_filtered_count").format(filtered=filtered_count, total=total_count))

            active_layer_name = self.get_selected_layer_name()
            if active_layer_name and active_layer_name in self.layers_filtered:
                self._refresh_table(self.layers_filtered[active_layer_name])
            else:
                self.table.clear()
                self.table.setRowCount(0)
                self.table.setColumnCount(0)
                
            self.update_window_title()
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_filter_error_title"), f"{exc}\n\n{traceback.format_exc()}")

    def apply_active_map(self) -> None:
        configs = []
        if "OpenStreetMap (OSM)" in self.active_maps:
            configs.append({
                "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": "&copy; OpenStreetMap contributors"
            })
        for custom_map in self.custom_maps:
            name = custom_map.get("name", "")
            if name in self.active_maps:
                configs.append({
                    "url": custom_map.get("url", ""),
                    "attribution": custom_map.get("attribution", "")
                })
        self.map_widget.set_tile_configs(configs)
        self.map_widget.reload_map()

    def on_add_map_clicked(self) -> None:
        dialog = AddMapDialog(self, self.translation)
        if dialog.exec() == QDialog.Accepted:
            name, url, attr = dialog.get_data()
            existing_idx = -1
            for i, custom_map in enumerate(self.custom_maps):
                if custom_map.get("name") == name:
                    existing_idx = i
                    break
            
            map_data = {"name": name, "url": url, "attribution": attr}
            if existing_idx >= 0:
                self.custom_maps[existing_idx] = map_data
            else:
                self.custom_maps.append(map_data)
            
            self.active_maps.add(name)
            self.apply_active_map()
            self.save_config_to_file()
            self.update_layer_tree()

    def on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self.map_loaded = True
            self.update_map_history_bboxes()
            self.update_map_active_bbox()

    def on_coordinate_edited(self) -> None:
        try:
            w = float(self.min_lon_edit.text())
            s = float(self.min_lat_edit.text())
            e = float(self.max_lon_edit.text())
            n = float(self.max_lat_edit.text())
            self.selected_bbox = (w, s, e, n)
            self.update_map_active_bbox()
        except ValueError:
            pass

    def update_map_active_bbox(self) -> None:
        if not getattr(self, "map_loaded", False):
            return
        if self.selected_bbox:
            w, s, e, n = self.selected_bbox
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript(f"window.setActiveBounds({w}, {s}, {e}, {n});")
        else:
            if HAS_WEBENGINE:
                self.map_widget.web.page().runJavaScript("window.clearActiveBounds();")

    def update_map_history_bboxes(self) -> None:
        if not getattr(self, "map_loaded", False):
            return
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
            "line_only": True,
            "use_bbox": True,
            "bbox": self.selected_bbox,
            "scale_x": self.scale_x_spin.value(),
            "scale_y": self.scale_y_spin.value(),
            "spline_tolerance": self.spline_tolerance_spin.value(),
            "junction_spacing": self.junction_spacing_spin.value(),
            "max_spacing": self.max_spacing_spin.value(),
            "straight_tolerance": self.straight_tolerance_spin.value(),
            "custom_maps": self.custom_maps,
            "active_maps": list(self.active_maps),
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
            
            bbox = config.get("bbox")
            if bbox and len(bbox) == 4:
                self.selected_bbox = tuple(bbox)
                self.min_lon_edit.setText(f"{bbox[0]:.7f}")
                self.min_lat_edit.setText(f"{bbox[1]:.7f}")
                self.max_lon_edit.setText(f"{bbox[2]:.7f}")
                self.max_lat_edit.setText(f"{bbox[3]:.7f}")
            
            self.scale_x_spin.setValue(config.get("scale_x", 1.0))
            self.scale_y_spin.setValue(config.get("scale_y", 1.0))
            self.spline_tolerance_spin.setValue(config.get("spline_tolerance", 5.0))
            self.junction_spacing_spin.setValue(config.get("junction_spacing", 30.0))
            self.max_spacing_spin.setValue(config.get("max_spacing", 200.0))
            self.straight_tolerance_spin.setValue(config.get("straight_tolerance", 0.5))
            
            # Tile custom maps configuration
            custom_maps = config.get("custom_maps", config.get("custom_layers", []))
            self.custom_maps = custom_maps
            
            active_maps = config.get("active_maps")
            if active_maps is not None:
                self.active_maps = set(active_maps)
            else:
                legacy_active = config.get("active_map", config.get("active_layer", "OpenStreetMap (OSM)"))
                self.active_maps = {legacy_active}
            self.update_layer_tree()
            self.apply_active_map()
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
        if not self.selected_bbox:
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
        name = self.get_selected_layer_name()
        if not name or name not in self.layers_filtered:
            QMessageBox.warning(self, self.tr_msg("msg_export_title"), "出力対象の線データレイヤーをツリーで選択してください。")
            return
        if not self.check_bbox_required():
            return
        filtered = self.layers_filtered[name]
        if not filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = []
        for f in filtered:
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
        name = self.get_selected_layer_name()
        if not name or name not in self.layers_filtered:
            QMessageBox.warning(self, self.tr_msg("msg_export_title"), "出力対象の線データレイヤーをツリーで選択してください。")
            return
        if not self.check_bbox_required():
            return
        filtered = self.layers_filtered[name]
        if not filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = []
        for f in filtered:
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
        name = self.get_selected_layer_name()
        if not name or name not in self.layers_filtered:
            QMessageBox.warning(self, self.tr_msg("msg_export_title"), "出力対象の線データレイヤーをツリーで選択してください。")
            return
        if not self.check_bbox_required():
            return
        filtered = self.layers_filtered[name]
        if not filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = []
        for f in filtered:
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
        for store in self.layers.values():
            store.cleanup()
        super().closeEvent(event)

    def on_add_line_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_msg("dialog_open_file_title"),
            str(self.last_output_dir) if hasattr(self, "last_output_dir") else "",
            "GIS data (*.zip *.shp *.geojson *.json);;ZIP (*.zip);;Shapefile (*.shp);;GeoJSON (*.geojson *.json);;All files (*.*)"
        )
        if path_str:
            self.load_path(Path(path_str))

    def on_add_history_clicked(self) -> None:
        # 既にツリーにあれば何もしない
        if any(e.name == "過去の出力履歴" for e in self.layer_entries):
            return
        self.show_history_bounds = True
        self.update_layer_tree()
        self.update_map_history_bboxes()

    def on_remove_layer_clicked(self) -> None:
        selected = self.layer_tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        idx = item.data(0, Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        if not entry.removable:
            return
        if entry.on_remove:
            entry.on_remove()
        self.layer_entries.pop(idx)
        self.update_layer_tree()
        self.log_msg(f"削除しました: {entry.name}")

    def _rebuild_layer_entries(self) -> None:
        """現在の状態から layer_entries を再構築する。"""
        entries: list[LayerEntry] = []

        # --- 背景地図 ---
        def _make_map_check(map_name: str) -> Callable[[bool], None]:
            def on_check(checked: bool) -> None:
                if checked:
                    self.active_maps.add(map_name)
                else:
                    self.active_maps.discard(map_name)
                self.apply_active_map()
                self.save_config_to_file()
            return on_check

        # OSM（削除不可）
        osm_entry = LayerEntry(
            name="OpenStreetMap (OSM)",
            checked=("OpenStreetMap (OSM)" in self.active_maps),
            removable=False,
        )
        osm_entry.on_check_changed = _make_map_check("OpenStreetMap (OSM)")
        entries.append(osm_entry)

        # カスタムマップ
        for cm in self.custom_maps:
            cm_name = cm.get("name", "")

            def _make_map_remove(n: str = cm_name) -> Callable[[], None]:
                def remove() -> None:
                    self.custom_maps = [m for m in self.custom_maps if m.get("name") != n]
                    self.active_maps.discard(n)
                    self.apply_active_map()
                    self.save_config_to_file()
                return remove

            entry = LayerEntry(
                name=cm_name,
                checked=(cm_name in self.active_maps),
                removable=True,
                on_remove=_make_map_remove(),
            )
            entry.on_check_changed = _make_map_check(cm_name)
            entries.append(entry)

        # --- 線データ ---
        for ln in list(self.layers.keys()):
            def _make_line_check(layer_name: str = ln) -> Callable[[bool], None]:
                def on_check(checked: bool) -> None:
                    if checked:
                        self.active_layers.add(layer_name)
                    else:
                        self.active_layers.discard(layer_name)
                    self.apply_filter()
                return on_check

            def _make_line_remove(layer_name: str = ln) -> Callable[[], None]:
                def remove() -> None:
                    if layer_name in self.layers:
                        self.layers[layer_name].cleanup()
                        del self.layers[layer_name]
                    self.layers_filtered.pop(layer_name, None)
                    self.active_layers.discard(layer_name)
                    self.apply_filter()
                return remove

            entries.append(LayerEntry(
                name=ln,
                checked=(ln in self.active_layers),
                removable=True,
                on_check_changed=_make_line_check(),
                on_remove=_make_line_remove(),
            ))

        # --- 履歴 ---
        # 既存の layer_entries に履歴が含まれている場合のみ追加
        has_history = any(e.name == "過去の出力履歴" for e in self.layer_entries)
        if has_history:
            def _history_check(checked: bool) -> None:
                self.show_history_bounds = checked
                if checked:
                    self.update_map_history_bboxes()
                else:
                    if HAS_WEBENGINE:
                        self.map_widget.web.page().runJavaScript("window.clearHistoryBounds();")

            def _history_remove() -> None:
                self.show_history_bounds = False
                if HAS_WEBENGINE:
                    self.map_widget.web.page().runJavaScript("window.clearHistoryBounds();")

            entries.append(LayerEntry(
                name="過去の出力履歴",
                checked=self.show_history_bounds,
                removable=True,
                on_check_changed=_history_check,
                on_remove=_history_remove,
            ))

        self.layer_entries = entries

    def update_layer_tree(self) -> None:
        self._rebuild_layer_entries()
        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()
        for i, entry in enumerate(self.layer_entries):
            item = QTreeWidgetItem(self.layer_tree, [entry.name])
            item.setCheckState(0, Qt.Checked if entry.checked else Qt.Unchecked)
            item.setData(0, Qt.UserRole, i)
        self.layer_tree.blockSignals(False)

    def on_layer_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        idx = item.data(0, Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        checked = (item.checkState(0) == Qt.Checked)
        entry.checked = checked
        if entry.on_check_changed:
            entry.on_check_changed(checked)

    def on_layer_selection_changed(self) -> None:
        self.update_window_title()
        active_layer_name = self.get_selected_layer_name()
        if active_layer_name and active_layer_name in self.layers_filtered:
            self._refresh_table(self.layers_filtered[active_layer_name])
        else:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)

    def get_selected_layer_name(self) -> Optional[str]:
        selected = self.layer_tree.selectedItems()
        if selected:
            item = selected[0]
            idx = item.data(0, Qt.UserRole)
            if idx is not None and idx < len(self.layer_entries):
                entry = self.layer_entries[idx]
                if entry.name in self.layers:
                    return entry.name
        return None

    def select_tree_layer(self, name: str) -> None:
        self.layer_tree.blockSignals(True)
        for i, entry in enumerate(self.layer_entries):
            if entry.name in self.layers and entry.name == name:
                item = self.layer_tree.topLevelItem(i)
                if item:
                    self.layer_tree.setCurrentItem(item)
                    item.setSelected(True)
                break
        self.layer_tree.blockSignals(False)


def main() -> int:
    if sys.platform == "win32":
        import ctypes
        try:
            myappid = "Ikumyon.NRClipBuilder.Version1"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

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
