#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N05 / SHP / GeoJSON Map Filter Exporter

A small PySide6 desktop app that loads railway line data, previews it on a Leaflet map,
filters by attributes, and exports filtered GeoJSON or Turnout-compatible Overpass JSON.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QFile, QLocale, QSize, QByteArray, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPalette, QKeySequence, QShortcut
from PySide6.QtSvg import QSvgRenderer
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
    QListWidgetItem,
    QMenu,
    QToolButton,
    QInputDialog,
    QComboBox,
    QWidget,
    QButtonGroup,
)

from core.geojson import geojson_to_overpass
from core.nrclip import geojson_to_nrclip_bytes

# リファクタリングによる分割モジュールのインポート
from core.geo_loader import FeatureStore, load_any, collect_fields, safe_str, write_json
from core.osm_loader import fetch_osm_railways
from core.geo_filter import (
    filter_features,
    geometry_is_line,
    clip_geometry_to_bbox,
)
from core.widgets import UiLoader, MapWidget, HAS_WEBENGINE
from core.utils import get_resource_path, get_executable_dir, get_svg_icon
from core.dialogs import AddMapDialog, AddLineLayerDialog
from core.config import AppConfig, HistoryManager

APP_TITLE = "NRClipBuilder"
MAX_TABLE_ROWS = 300

DEFAULT_TRANSLATION: dict[str, str] = {
    "dialog_label_has_lines": "Include lines (railways, etc.)",
    "label_track_mode": "Track Mode",
    "track_mode_point": "Point",
    "track_mode_tangent": "Tangent",
    "left_tab_layers": "Layers",
    "left_tab_filter": "Filter",
    "left_tab_export": "Export",
    "draw_line_btn": "Draw Line",
    "msg_draw_line_ready": "Click to add straight-line vertices. Double-click to set the end point.",
    "msg_draw_line_added": "Added line: {count} points",
    "structure_ground": "Ground",
    "structure_bridge": "Viaduct / Bridge",
    "structure_tunnel": "Tunnel",
    "label_opacity": "Opacity",
}


@dataclass
class LayerEntry:
    """ツリーに表示される各レイヤーの共通構造。"""
    name: str
    checked: bool = True
    removable: bool = True
    has_lines: bool = False
    opacity: float = 1.0
    on_check_changed: Callable[[bool], None] | None = None
    on_remove: Callable[[], None] | None = None



class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = FeatureStore()
        self.filtered: list[dict[str, Any]] = []
        self.last_output_dir = Path.cwd()

        # 設定・履歴管理クラスの初期化
        config_path = get_executable_dir() / "app_config.json"
        self.app_config = AppConfig(config_path)
        self.recovery_path = config_path.with_name("nrclip_builder_recovery.json")
        self._recovery_dirty_layers: set[str] = set()
        self._recovery_deleted_layers: set[str] = set()
        self._draw_line_draft: list[dict[str, Any]] = []
        history_path = get_executable_dir() / "bbox_history.json"
        self.history_manager = HistoryManager(history_path)

        # UIのロード
        loader = UiLoader(self)
        ui_path = get_resource_path("ui/main_window.ui")
        ui_file = QFile(str(ui_path))
        if not ui_file.open(QFile.ReadOnly):
            raise RuntimeError(f"UIファイルを開けませんでした: {ui_path}")
        loader.load(ui_file)
        ui_file.close()
        self.apply_app_icon()

        # 言語リストのスキャン
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

        # 背景地図の初期化
        self.registered_maps: list[dict[str, str]] = []
        self.added_maps: set[str] = set()
        self.active_maps: set[str] = {"OpenStreetMap (OSM)"}
        self.registered_lines: list[str] = []
        self.line_edit_undo_stack: list[dict[str, Any]] = []
        self._draw_line_transfers: dict[str, dict[str, Any]] = {}
        
        # 独立したレイヤーの管理
        self.layers: dict[str, FeatureStore] = {}
        self.layers_filtered: dict[str, list[dict[str, Any]]] = {}
        self.active_layers: set[str] = set()
        self.layer_opacities: dict[str, float] = {}
        self.show_history_bounds = False

        # 統一レイヤーエントリ
        self.layer_entries: list[LayerEntry] = []

        self.left_add_map_btn.clicked.connect(self.on_add_map_clicked)
        self.add_map_menu = QMenu(self)
        self.registered_map_btn.setMenu(self.add_map_menu)
        self.registered_map_btn.setPopupMode(QToolButton.InstantPopup)
        self.add_map_menu.aboutToShow.connect(self.update_add_map_menu)

        self.left_add_line_btn.clicked.connect(self.on_add_line_clicked)
        self.registered_line_menu = QMenu(self)
        self.registered_line_btn.setMenu(self.registered_line_menu)
        self.registered_line_btn.setPopupMode(QToolButton.InstantPopup)
        self.registered_line_menu.aboutToShow.connect(self.update_registered_line_menu)

        self.add_history_btn.clicked.connect(self.on_add_history_clicked)
        self.remove_layer_btn.clicked.connect(self.on_remove_layer_clicked)
        self.edit_layer_btn.clicked.connect(self.on_edit_layer_clicked)
        self.draw_line_btn.setCheckable(True)
        self.draw_line_btn.clicked.connect(self.on_draw_line_clicked)
        self.draw_line_btn.setEnabled(HAS_WEBENGINE)
        self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.undo_shortcut.activated.connect(self.undo_line_edit)
        
        self.layer_list.itemChanged.connect(self.on_layer_item_changed)
        self.layer_list.itemSelectionChanged.connect(self.on_layer_selection_changed)
        self.layer_list.model().rowsMoved.connect(self.on_layers_moved)
        self.opacity_slider.valueChanged.connect(self.on_opacity_slider_changed)
        
        self.railway_rail_check.clicked.connect(self.apply_filter)
        self.railway_subway_check.clicked.connect(self.apply_filter)
        self.railway_tram_check.clicked.connect(self.apply_filter)
        self.railway_light_rail_check.clicked.connect(self.apply_filter)
        self.railway_monorail_check.clicked.connect(self.apply_filter)
        self.railway_funicular_check.clicked.connect(self.apply_filter)
        self.railway_abandoned_check.clicked.connect(self.apply_filter)


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

        self.setup_track_mode_combo()
        self.load_history_from_file()
        self.load_config_from_file()

        self.filter_update_timer = QTimer(self)
        self.filter_update_timer.setSingleShot(True)
        self.filter_update_timer.setInterval(500)
        self.filter_update_timer.timeout.connect(self.apply_filter)
        self.keyword_edit.textChanged.connect(self.schedule_filter_update)
        self.field_edit.textChanged.connect(self.schedule_filter_update)
        self.exclude_edit.textChanged.connect(self.schedule_filter_update)
        self.regex_check.toggled.connect(self.schedule_filter_update)
        self.and_radio.toggled.connect(self.schedule_filter_update)
        self.or_radio.toggled.connect(self.schedule_filter_update)

        # メニューアクションの接続
        self.action_open.setVisible(False)
        self.action_export_geojson.triggered.connect(self.export_geojson)
        self.action_export_turnout.triggered.connect(self.export_turnout_json)
        self.action_export_nrclip.triggered.connect(self.export_nrclip)
        self.action_open_html.triggered.connect(self.open_preview_in_browser)
        self.action_quit.triggered.connect(self.close)

        # 描画構造トグルボタンとアクションの設定
        self.draw_struct_tunnel_btn.clicked.connect(lambda: self.on_draw_structure_changed("tunnel"))
        self.draw_struct_ground_btn.clicked.connect(lambda: self.on_draw_structure_changed("ground"))
        self.draw_struct_bridge_btn.clicked.connect(lambda: self.on_draw_structure_changed("bridge"))
        
        self.draw_struct_tunnel_btn.setEnabled(False)
        self.draw_struct_ground_btn.setEnabled(False)
        self.draw_struct_bridge_btn.setEnabled(False)

        self.addAction(self.action_next_struct)
        self.addAction(self.action_prev_struct)
        self.action_next_struct.triggered.connect(self.select_next_structure)
        self.action_prev_struct.triggered.connect(self.select_prev_structure)

        self.struct_buttons = [
            self.draw_struct_tunnel_btn,
            self.draw_struct_ground_btn,
            self.draw_struct_bridge_btn
        ]

        # 初期ローカライズの適用
        self.retranslate_ui()
        self.update_layer_tree()

        # ステータスバーと初期化
        self.statusBar().showMessage(self.tr_msg("msg_select_file"))
        self.apply_active_map()
        self.apply_filter()

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
        try:
            config = self.app_config.load()
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
        self.label_track_mode.setText(self.tr_msg("label_track_mode"))
        self.track_mode_combo.setItemText(0, self.tr_msg("track_mode_point"))
        self.track_mode_combo.setItemText(1, self.tr_msg("track_mode_tangent"))
        self.left_add_line_btn.setText(self.tr_msg("add_line_btn"))
        self.add_history_btn.setText(self.tr_msg("add_history_btn"))
        self.left_add_map_btn.setText(self.tr_msg("add_map_btn"))
        self.remove_layer_btn.setText(self.tr_msg("remove_layer_btn"))
        self.draw_line_btn.setText(self.tr_msg("draw_line_btn"))
        self.draw_struct_tunnel_btn.setText(self.tr_msg("structure_tunnel"))
        self.draw_struct_ground_btn.setText(self.tr_msg("structure_ground"))
        self.draw_struct_bridge_btn.setText(self.tr_msg("structure_bridge"))
        self.layer_group.setTitle(self.tr_msg("layer_group"))
        self.label_opacity.setText(self.tr_msg("label_opacity"))
        self.left_tabs.setTabText(0, self.tr_msg("left_tab_filter"))
        self.left_tabs.setTabText(1, self.tr_msg("left_tab_layers"))
        self.left_tabs.setTabText(2, self.tr_msg("left_tab_export"))
        
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
        
        self.setup_icons()

    def setup_track_mode_combo(self) -> None:
        self.track_mode_combo.blockSignals(True)
        self.track_mode_combo.clear()
        self.track_mode_combo.addItem(self.tr_msg("track_mode_point"), "point")
        self.track_mode_combo.addItem(self.tr_msg("track_mode_tangent"), "tangent")
        self.track_mode_combo.setCurrentIndex(0)
        self.track_mode_combo.blockSignals(False)

    def setup_icons(self) -> None:
        btn_text_color = self.palette().color(QPalette.ButtonText).name()
        icons_dir = get_resource_path("assets/icons")
        
        self.left_add_line_btn.setIcon(get_svg_icon(icons_dir / "spline.svg", btn_text_color))
        self.left_add_map_btn.setIcon(get_svg_icon(icons_dir / "map-plus.svg", btn_text_color))
        self.add_history_btn.setIcon(get_svg_icon(icons_dir / "history.svg", btn_text_color))
        self.remove_layer_btn.setIcon(get_svg_icon(icons_dir / "trash-2.svg", btn_text_color))
        self.edit_layer_btn.setIcon(get_svg_icon(icons_dir / "pencil-line.svg", btn_text_color))
        self.draw_line_btn.setIcon(get_svg_icon(icons_dir / "spline.svg", btn_text_color))
        self.draw_struct_ground_btn.setIcon(get_svg_icon(icons_dir / "arrow-down-to-line.svg", btn_text_color))
        self.draw_struct_tunnel_btn.setIcon(get_svg_icon(icons_dir / "train-front-tunnel.svg", btn_text_color))
        self.draw_struct_bridge_btn.setIcon(get_svg_icon(icons_dir / "bridge-4.svg", btn_text_color))
        
        icon_size = QSize(16, 16)
        self.left_add_line_btn.setIconSize(icon_size)
        self.left_add_map_btn.setIconSize(icon_size)
        self.add_history_btn.setIconSize(icon_size)
        self.remove_layer_btn.setIconSize(icon_size)
        self.edit_layer_btn.setIconSize(icon_size)
        self.draw_line_btn.setIconSize(icon_size)
        self.draw_struct_ground_btn.setIconSize(icon_size)
        self.draw_struct_tunnel_btn.setIconSize(icon_size)
        self.draw_struct_bridge_btn.setIconSize(icon_size)



    def set_draw_buttons_active(self, active: bool) -> None:
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(active)
        self.draw_line_btn.blockSignals(False)

    def apply_draw_line_mode(self) -> None:
        checked = self.draw_line_btn.isChecked()
        if HAS_WEBENGINE:
            js_value = "true" if checked else "false"
            self.map_widget.web.page().runJavaScript(f"window.setDrawLineMode({js_value});")
        
        self.draw_struct_tunnel_btn.setEnabled(checked)
        self.draw_struct_ground_btn.setEnabled(checked)
        self.draw_struct_bridge_btn.setEnabled(checked)
        
        if checked:
            struct = "ground"
            if self.draw_struct_tunnel_btn.isChecked():
                struct = "tunnel"
            elif self.draw_struct_bridge_btn.isChecked():
                struct = "bridge"
            self.on_draw_structure_changed(struct)

    def on_draw_structure_changed(self, struct: str) -> None:
        if HAS_WEBENGINE:
            self.map_widget.web.page().runJavaScript(f"window.setDrawStructure('{struct}');")

    def select_next_structure(self) -> None:
        if not self.draw_line_btn.isChecked():
            return
        current_idx = 1
        for i, btn in enumerate(self.struct_buttons):
            if btn.isChecked():
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(self.struct_buttons)
        self.struct_buttons[next_idx].click()

    def select_prev_structure(self) -> None:
        if not self.draw_line_btn.isChecked():
            return
        current_idx = 1
        for i, btn in enumerate(self.struct_buttons):
            if btn.isChecked():
                current_idx = i
                break
        prev_idx = (current_idx - 1) % len(self.struct_buttons)
        self.struct_buttons[prev_idx].click()

    def on_draw_line_clicked(self) -> None:
        self.apply_draw_line_mode()
        if self.draw_line_btn.isChecked():
            self.log_msg(self.tr_msg("msg_draw_line_ready"))

    def add_drawn_line(self, segments: list[dict[str, Any]]) -> bool:
        if not segments:
            return
            
        target_layer_name = self.get_selected_layer_name()
        if not target_layer_name or target_layer_name not in self.layers:
            confirm = QMessageBox.question(
                self,
                "線の保存先が選択されていません。",
                "新規レイヤーに追加しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                return False
            base_name = "Drawn Lines"
            layer_name = base_name
            suffix = 2
            while layer_name in self.layers:
                layer_name = f"{base_name} {suffix}"
                suffix += 1
            # 描画線を追加してから一度だけツリーと地図を更新する。
            # 先に通常のレイヤー作成処理を呼ぶと、空レイヤーの再描画で描画結果が消える。
            self.layers[layer_name] = FeatureStore(fields=["name", "railway", "_source"])
            self.active_layers.add(layer_name)
            target_layer_name = layer_name
        if not target_layer_name or target_layer_name not in self.layers:
            QMessageBox.warning(
                self,
                self.tr_msg("msg_validation_error") or "警告",
                "描画した線を追加する宛先の線レイヤーをツリーで選択してください。\nまたは「線を追加」ボタンから空の新規レイヤーを作成してください。"
            )
            return False
            
        store = self.layers[target_layer_name]
        
        # 線の名前を入力するダイアログを表示
        default_name = f"Line {len(store.features) + 1}"
        title = self.tr_msg("dialog_drawn_line_title") or "線の追加"
        prompt = self.tr_msg("dialog_drawn_line_prompt") or "線の名前を入力してください:"
        
        name, ok = QInputDialog.getText(
            self,
            title,
            prompt,
            text=default_name
        )
        if not ok or not name.strip():
            return # キャンセルまたは名前が空なら何もしない
            
        # 各セグメントを個別の Feature として登録
        added_count = 0
        added_features: list[dict[str, Any]] = []
        for seg in segments:
            coords = seg.get("coords")
            struct = seg.get("structure")
            if not coords or len(coords) < 2:
                continue
                
            properties = {
                "name": name.strip(),
                "railway": "rail",
                "_source": target_layer_name,
            }
            
            if struct == "bridge":
                properties["bridge"] = "yes"
            elif struct == "tunnel":
                properties["tunnel"] = "yes"
                
            if seg.get("distance") is not None:
                properties["distance"] = f"{seg['distance']:.1f} m"
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                },
                "properties": properties
            }
            store.features.append(feature)
            added_features.append(feature)
            added_count += 1

        if added_count == 0:
            QMessageBox.warning(self, "線の追加エラー", "有効な線分がありませんでした。")
            return False

        self.line_edit_undo_stack.append({
            "kind": "add_line",
            "store": target_layer_name,
            "features": added_features,
        })
        self._mark_recovery_dirty(target_layer_name)
        
        self.update_layer_tree()
        self.select_tree_layer(target_layer_name)
        self.apply_filter()
        self.save_config_to_file()
        
        self.log_msg(self.tr_msg("msg_draw_line_added").format(count=len(segments) + 1))
        return True

    def _mark_recovery_dirty(self, layer_name: str) -> None:
        """変更直後に復旧対象として記録する。通常保存とは独立した退避。"""
        self._recovery_deleted_layers.discard(layer_name)
        self._recovery_dirty_layers.add(layer_name)
        self.write_recovery_backup()

    def write_recovery_backup(self) -> None:
        """変更レイヤーを一時ファイル経由で即時退避する。

        書き込み途中のファイルを復旧データとして扱わないよう、flush/fsync後に置換する。
        """
        if not self._recovery_dirty_layers and not self._recovery_deleted_layers and not self._draw_line_draft:
            return
        layers: list[dict[str, Any]] = []
        for name in sorted(self._recovery_dirty_layers):
            store = self.layers.get(name)
            if store is None:
                continue
            layers.append({
                "name": name,
                "checked": name in self.active_layers,
                "opacity": self.layer_opacities.get(name, 1.0),
                "features": store.features,
            })
        payload = {
            "version": 1,
            "layers": layers,
            "deleted_layers": sorted(self._recovery_deleted_layers),
            "draw_line_draft": self._draw_line_draft,
        }
        temp_path = self.recovery_path.with_name(self.recovery_path.name + ".tmp")
        try:
            self.recovery_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.recovery_path)
        except Exception as exc:
            # 通常保存の失敗に引きずられて線データを破棄しない。
            self.log_msg(f"復旧バックアップの作成に失敗しました: {exc}")
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    def restore_recovery_backup(self) -> None:
        """前回の保存失敗・異常終了時に退避線を現在の状態へ戻す。"""
        if not self.recovery_path.exists():
            return
        try:
            payload = json.loads(self.recovery_path.read_text(encoding="utf-8"))
            for name in payload.get("deleted_layers", []):
                self.layers.pop(name, None)
                self.active_layers.discard(name)
            for item in payload.get("layers", []):
                name = item.get("name")
                features = item.get("features")
                if not name or not isinstance(features, list):
                    continue
                self.layers[name] = FeatureStore(
                    features=features,
                    fields=collect_fields(features),
                )
                if item.get("checked", True):
                    self.active_layers.add(name)
                else:
                    self.active_layers.discard(name)
                self.layer_opacities[name] = float(item.get("opacity", 1.0))
            draft = payload.get("draw_line_draft")
            if isinstance(draft, list) and draft:
                self._draw_line_draft = draft
                self.log_msg("未確定の描画線も復旧バックアップに残っています。")
            self.log_msg("保存前の線データを復旧バックアップから復元しました。")
        except Exception as exc:
            self.log_msg(f"復旧バックアップを読み込めませんでした: {exc}")

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

    def apply_app_icon(self) -> None:
        icon_path = get_resource_path("icon.ico")
        if not icon_path.exists():
            return
        icon = QIcon(str(icon_path.resolve()))
        if icon.isNull():
            return
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app:
            app.setWindowIcon(icon)

    def schedule_filter_update(self, *args: Any) -> None:
        self.filter_update_timer.start()

    def load_path(self, path: Path, checked: bool = True, save_config: bool = True) -> None:
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
            if checked:
                self.active_layers.add(layer_name)
            else:
                self.active_layers.discard(layer_name)
            self.last_output_dir = path.parent
            
            self.log_msg(self.tr_msg("msg_load_success").format(count=len(store.features)))
            if store.crs_note:
                self.log_msg(store.crs_note)
            
            # 登録済み線データへ追加
            path_str = str(path.resolve())
            if path_str in self.registered_lines:
                self.registered_lines.remove(path_str)
            self.registered_lines.insert(0, path_str)
            
            if save_config:
                self.save_config_to_file()

            self.update_layer_tree()
            self.select_tree_layer(layer_name)
            self.apply_filter()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self.log_msg(self.tr_msg("dialog_error") + str(exc))
            QMessageBox.critical(self, self.tr_msg("msg_load_error_title"), f"{exc}\n\n{traceback.format_exc()}")

    def apply_filter(self, *args: Any) -> None:
        try:
            total_count = 0
            for name, store in self.layers.items():
                features = list(store.features)
                if "osm" in name.lower() or "overpass" in name.lower():
                    filtered = self.filter_osm_features(features)
                else:
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
            for entry in self.layer_entries:
                if entry.name == "過去の出力履歴" and entry.checked:
                    map_features.extend(self.get_history_line_features())
                elif entry.checked and entry.name in self.layers_filtered:
                    features = self.layers_filtered[entry.name]
                    map_features.extend(features)
                    filtered_count += len(features)
            self._refresh_map(map_features, preserve_view=True)

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

    def apply_active_map(self, reload_map: bool = True, preserve_view: bool = True) -> None:
        configs = []
        registered_map_by_name = {
            custom_map.get("name", ""): custom_map
            for custom_map in self.registered_maps
        }
        for entry in self.layer_entries:
            if not entry.checked or entry.name not in self.active_maps:
                continue
            if entry.name == "OpenStreetMap (OSM)":
                configs.append({
                    "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                    "attribution": "&copy; OpenStreetMap contributors",
                    "opacity": self.layer_opacities.get(entry.name, 1.0),
                })
            elif entry.name in registered_map_by_name:
                custom_map = registered_map_by_name[entry.name]
                configs.append({
                    "url": custom_map.get("url", ""),
                    "attribution": custom_map.get("attribution", ""),
                    "opacity": self.layer_opacities.get(entry.name, 1.0),
                })
        self.map_widget.set_tile_configs(configs)
        if reload_map:
            self.map_widget.reload_map(preserve_view=preserve_view)

    def update_registered_line_menu(self) -> None:
        self.registered_line_menu.clear()
        
        if self.registered_lines:
            for path_str in self.registered_lines:
                path = Path(path_str)
                name = path.name
                action = self.registered_line_menu.addAction(name)
                action.setToolTip(path_str)
                action.triggered.connect(lambda checked=False, p=path: self.load_path(p))
        else:
            no_history_text = "履歴なし" if self.current_lang.startswith("ja") else "No History"
            action = self.registered_line_menu.addAction(no_history_text)
            action.setEnabled(False)

    def fetch_osm_railways_from_bbox(self) -> None:
        if not self.selected_bbox:
            QMessageBox.warning(
                self,
                self.tr_msg("msg_fetch_osm_title"),
                self.tr_msg("msg_fetch_osm_no_bbox"),
            )
            return

        layer_name = "OSM線路データ"
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.log_msg(self.tr_msg("msg_fetch_osm_loading"))
            store = fetch_osm_railways(self.selected_bbox)
            for feature in store.features:
                feature.setdefault("properties", {})["_source"] = layer_name

            if layer_name in self.layers:
                self.layers[layer_name].cleanup()
            self.layers[layer_name] = store
            self.active_layers.add(layer_name)
            self.layers_filtered.pop(layer_name, None)

            self.update_layer_tree()
            self.select_tree_layer(layer_name)
            self.apply_filter()
            self.save_config_to_file()
            self.log_msg(self.tr_msg("msg_fetch_osm_success").format(count=len(store.features)))
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.tr_msg("msg_fetch_osm_title"),
                f"{self.tr_msg('dialog_error')}{exc}\n\n{traceback.format_exc()}",
            )
        finally:
            QApplication.restoreOverrideCursor()

    def update_add_map_menu(self) -> None:
        self.add_map_menu.clear()
        
        # 登録済みマップ一覧をメニュー項目として追加
        if self.registered_maps:
            for map_data in self.registered_maps:
                name = map_data.get("name", "")
                action = self.add_map_menu.addAction(name)
                action.triggered.connect(lambda checked=False, data=map_data: self.add_map_from_registered(data))

    def add_map_from_registered(self, map_data: dict[str, str]) -> None:
        name = map_data.get("name", "")
        self.added_maps.add(name)
        self.active_maps.add(name)
        self.apply_active_map()
        self.save_config_to_file()
        self.update_layer_tree()
        self.log_msg(f"マップを追加しました: {name}")

    def filter_osm_features(self, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed_types = set()
        if self.railway_rail_check.isChecked():
            allowed_types.add("rail")
        if self.railway_subway_check.isChecked():
            allowed_types.add("subway")
        if self.railway_tram_check.isChecked():
            allowed_types.add("tram")
        if self.railway_light_rail_check.isChecked():
            allowed_types.add("light_rail")
        if self.railway_monorail_check.isChecked():
            allowed_types.add("monorail")
        if self.railway_funicular_check.isChecked():
            allowed_types.add("funicular")
            
        abandoned_types = {"abandoned", "disused", "construction", "proposed"}
        show_abandoned = self.railway_abandoned_check.isChecked()

        result = []
        for feat in features:
            props = feat.get("properties") or {}
            rw = props.get("railway", "")
            if rw in allowed_types:
                result.append(feat)
            elif show_abandoned and rw in abandoned_types:
                result.append(feat)
        return result

    def on_add_map_clicked(self) -> None:
        dialog = AddMapDialog(self, self.translation)
        if dialog.exec() == QDialog.Accepted:
            name, url, attr, has_lines = dialog.get_data()
            existing_idx = -1
            for i, custom_map in enumerate(self.registered_maps):
                if custom_map.get("name") == name:
                    existing_idx = i
                    break
            
            map_data = {"name": name, "url": url, "attribution": attr, "has_lines": has_lines}
            if existing_idx >= 0:
                self.registered_maps[existing_idx] = map_data
            else:
                self.registered_maps.append(map_data)
            
            self.added_maps.add(name)
            self.active_maps.add(name)
            self.apply_active_map()
            self.save_config_to_file()
            self.update_layer_tree()

    def on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self.map_loaded = True
            self.update_map_history_bboxes()
            self.update_map_active_bbox()
            if self.draw_line_btn.isChecked() and HAS_WEBENGINE:
                self.apply_draw_line_mode()

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

    def update_map_active_bbox(self, fit_map: bool = False) -> None:
        if not getattr(self, "map_loaded", False):
            return
        if self.selected_bbox:
            w, s, e, n = self.selected_bbox
            if HAS_WEBENGINE:
                fit_js = "true" if fit_map else "false"
                self.map_widget.web.page().runJavaScript(f"window.setActiveBounds({w}, {s}, {e}, {n}, {fit_js});")
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

    def get_history_line_features(self) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for item in self.history_data:
            geojson = item.get("geojson")
            if not isinstance(geojson, dict):
                continue
            raw_features = geojson.get("features")
            if not isinstance(raw_features, list):
                continue
            history_name = item.get("name", "")
            for feature in raw_features:
                if not isinstance(feature, dict):
                    continue
                if not geometry_is_line(feature.get("geometry") or {}):
                    continue
                new_feature = feature.copy()
                props = dict(new_feature.get("properties") or {})
                props["_source"] = "過去の出力履歴"
                props["_history_output"] = True
                props["_history_name"] = history_name
                new_feature["properties"] = props
                features.append(new_feature)
        return features

    def on_title_changed(self, title: str) -> None:
        if title.startswith("VIEW:"):
            parts = title[5:].split(",")
            if len(parts) == 3:
                try:
                    lat, lng = map(float, parts[:2])
                    zoom = int(float(parts[2]))
                    self.map_widget.current_view = {"lat": lat, "lng": lng, "zoom": zoom}
                except ValueError:
                    pass
        elif title.startswith("BBOX:"):
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
        elif title == "FETCH_OSM":
            self.fetch_osm_railways_from_bbox()
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
                    self.update_map_active_bbox(fit_map=True)
                    self.select_history_row_by_name(name)
                except ValueError:
                    pass
        elif title.startswith("DRAWN_LINE_DRAFT_CHUNK:"):
            parts = title.split(":", 4)
            if len(parts) == 5:
                transfer_id, index_text, total_text, encoded_chunk = parts[1:]
                try:
                    index = int(index_text)
                    total = int(total_text)
                    transfer = self._draw_line_transfers.setdefault(
                        "draft:" + transfer_id, {"total": total, "chunks": {}}
                    )
                    transfer["chunks"][index] = urllib.parse.unquote(encoded_chunk)
                    if len(transfer["chunks"]) == total:
                        payload = "".join(transfer["chunks"][i] for i in range(total))
                        del self._draw_line_transfers["draft:" + transfer_id]
                        self._draw_line_draft = json.loads(payload)
                        self.write_recovery_backup()
                except Exception as e:
                    self._draw_line_transfers.pop("draft:" + transfer_id, None)
                    self.log_msg(f"描画途中のバックアップに失敗しました: {e}")
        elif title.startswith("DRAWN_LINE_DRAFT:"):
            try:
                self._draw_line_draft = json.loads(title[18:])
                self.write_recovery_backup()
            except Exception as e:
                self.log_msg(f"描画途中のバックアップに失敗しました: {e}")
        elif title.startswith("DRAWN_LINE_CHUNK:"):
            parts = title.split(":", 4)
            if len(parts) == 5:
                transfer_id, index_text, total_text, encoded_chunk = parts[1:]
                try:
                    index = int(index_text)
                    total = int(total_text)
                    transfer = self._draw_line_transfers.setdefault(
                        transfer_id, {"total": total, "chunks": {}}
                    )
                    transfer["chunks"][index] = urllib.parse.unquote(encoded_chunk)
                    if len(transfer["chunks"]) == total:
                        payload = "".join(transfer["chunks"][i] for i in range(total))
                        del self._draw_line_transfers[transfer_id]
                        self.finish_drawn_line(json.loads(payload))
                except Exception as e:
                    self._draw_line_transfers.pop(transfer_id, None)
                    self.log_msg(f"Error parsing drawn line: {e}")
                    QMessageBox.critical(self, "線の追加エラー", f"線をレイヤーに追加できませんでした。\n{e}")
        elif title.startswith("DRAWN_LINE:"):
            coords_str = title[11:]
            try:
                coordinates = json.loads(coords_str)
                added = self.add_drawn_line(coordinates)
            except Exception as e:
                self.log_msg(f"Error parsing drawn line: {e}")
                QMessageBox.critical(self, "線の追加エラー", f"線をレイヤーに追加できませんでした。\n{e}")
                added = False
            if added:
                self._draw_line_draft = []
                self.write_recovery_backup()
                self.set_draw_buttons_active(False)
                self.apply_draw_line_mode()
            elif HAS_WEBENGINE:
                # 未確定の線を残したまま、再確定できる状態に戻す。
                self.map_widget.web.page().runJavaScript("document.title = 'DRAW_LINE_ACTIVE';")
        elif title.startswith("EDITED_LINE:"):
            try:
                edit_data = json.loads(title[12:])
                self.update_line_geometry(edit_data)
            except Exception as e:
                self.log_msg(f"Error updating drawn line: {e}")
        elif title.startswith("EDITED_STRUCTURE:"):
            try:
                self.update_line_structure(json.loads(title[17:]))
            except Exception as e:
                self.log_msg(f"Error updating line structure: {e}")
        elif title == "DRAW_LINE_END":
            self.set_draw_buttons_active(False)
            self.apply_draw_line_mode()
        elif title == "DRAW_LINE_CANCEL":
            self._draw_line_draft = []
            self.write_recovery_backup()
            self.set_draw_buttons_active(False)
            self.apply_draw_line_mode()
            self.log_msg("描画をキャンセルしました")

    def finish_drawn_line(self, coordinates: list[dict[str, Any]]) -> None:
        added = self.add_drawn_line(coordinates)
        if added:
            self._draw_line_draft = []
            self.write_recovery_backup()
            self.set_draw_buttons_active(False)
            self.apply_draw_line_mode()
        elif HAS_WEBENGINE:
            self.map_widget.web.page().runJavaScript("document.title = 'DRAW_LINE_ACTIVE';")

    def update_line_geometry(self, edit_data: dict[str, Any]) -> None:
        source = edit_data.get("source")
        old_coords = edit_data.get("old_coords")
        coords = edit_data.get("coords")
        if not isinstance(old_coords, list) or not isinstance(coords, list):
            return
        changed = False
        for store in self.layers.values():
            for feature in store.features:
                props = feature.get("properties") or {}
                if source and props.get("_source") != source:
                    continue
                geometry = feature.get("geometry") or {}
                if geometry.get("type") != "LineString" or geometry.get("coordinates") != old_coords:
                    continue
                self.line_edit_undo_stack.append({
                    "source": source,
                    "old_coords": [point[:] for point in old_coords],
                    "new_coords": [point[:] for point in coords],
                })
                geometry["coordinates"] = coords
                feature["geometry"] = geometry
                changed = True
                layer_name = props.get("_source")
                if layer_name:
                    self._mark_recovery_dirty(layer_name)
                break
        if changed:
            self.save_config_to_file()
            active_layer_name = self.get_selected_layer_name()
            if active_layer_name and active_layer_name in self.layers_filtered:
                self._refresh_table(self.layers_filtered[active_layer_name])

    def update_line_structure(self, edit_data: dict[str, Any]) -> None:
        source = edit_data.get("source")
        coords = edit_data.get("coords")
        structure = edit_data.get("structure")
        if not isinstance(coords, list) or structure not in {"ground", "bridge", "tunnel"}:
            return
        for store in self.layers.values():
            for feature in store.features:
                props = feature.get("properties") or {}
                geometry = feature.get("geometry") or {}
                if source and props.get("_source") != source:
                    continue
                if geometry.get("type") != "LineString" or geometry.get("coordinates") != coords:
                    continue
                old_structure = "tunnel" if props.get("tunnel") in ("yes", "true", "1", True) else "bridge" if props.get("bridge") in ("yes", "true", "1", True) else "ground"
                self.line_edit_undo_stack.append({
                    "kind": "structure",
                    "source": source,
                    "coords": [point[:] for point in coords],
                    "old_structure": old_structure,
                    "new_structure": structure,
                })
                props.pop("bridge", None)
                props.pop("tunnel", None)
                props["layer"] = {"tunnel": -1, "bridge": 1, "ground": 0}[structure]
                if structure == "bridge":
                    props["bridge"] = "yes"
                elif structure == "tunnel":
                    props["tunnel"] = "yes"
                feature["properties"] = props
                if props.get("_source"):
                    self._mark_recovery_dirty(props["_source"])
                self.save_config_to_file()
                return

    def undo_line_edit(self) -> None:
        if not self.line_edit_undo_stack:
            return
        edit = self.line_edit_undo_stack.pop()
        if edit.get("kind") == "add_line":
            store = self.layers.get(edit.get("store"))
            added_features = edit.get("features") or []
            if store is not None:
                added_ids = {id(feature) for feature in added_features}
                store.features[:] = [feature for feature in store.features if id(feature) not in added_ids]
                store_name = edit.get("store")
                if store_name:
                    self._mark_recovery_dirty(store_name)
                self.save_config_to_file()
                self.apply_filter()
                self.log_msg("直前に追加した線を取り消しました")
            return
        if edit.get("kind") == "structure":
            target = edit.get("new_structure")
            restore = edit.get("old_structure")
            coords = edit.get("coords")
            if target and restore and isinstance(coords, list):
                self.update_line_structure({"source": edit.get("source"), "coords": coords, "structure": restore})
                if self.line_edit_undo_stack:
                    self.line_edit_undo_stack.pop()
                if HAS_WEBENGINE:
                    source_json = json.dumps(edit.get("source", ""), ensure_ascii=False)
                    coords_json = json.dumps(coords, ensure_ascii=False)
                    restore_json = json.dumps(restore, ensure_ascii=False)
                    self.map_widget.web.page().runJavaScript(
                        f"window.applyLineStructure({source_json}, {coords_json}, {restore_json});"
                    )
                self.log_msg("線の構造変更を取り消しました")
            return
        source = edit.get("source")
        current_coords = edit.get("new_coords")
        previous_coords = edit.get("old_coords")
        if not isinstance(current_coords, list) or not isinstance(previous_coords, list):
            return
        for store in self.layers.values():
            for feature in store.features:
                props = feature.get("properties") or {}
                if source and props.get("_source") != source:
                    continue
                geometry = feature.get("geometry") or {}
                if geometry.get("type") == "LineString" and geometry.get("coordinates") == current_coords:
                    geometry["coordinates"] = previous_coords
                    feature["geometry"] = geometry
                    self.save_config_to_file()
                    if HAS_WEBENGINE:
                        source_json = json.dumps(source or "", ensure_ascii=False)
                        current_json = json.dumps(current_coords, ensure_ascii=False)
                        previous_json = json.dumps(previous_coords, ensure_ascii=False)
                        self.map_widget.web.page().runJavaScript(
                            f"window.restoreLineGeometry({source_json}, {current_json}, {previous_json});"
                        )
                    self.log_msg("直前のノード移動を取り消しました")
                    return
            # Leaflet側ですでに線を更新済みなので、マップ全体は再生成しない。
            active_layer_name = self.get_selected_layer_name()
            if active_layer_name and active_layer_name in self.layers_filtered:
                self._refresh_table(self.layers_filtered[active_layer_name])
        if False:
            self.set_draw_buttons_active(False)
            self.apply_draw_line_mode()
        if False:
            self.set_draw_buttons_active(False)
            self.apply_draw_line_mode()
            self.log_msg("描画をキャンセルしました")

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

    @property
    def history_data(self) -> list[dict[str, Any]]:
        return self.history_manager.data

    @history_data.setter
    def history_data(self, val: list[dict[str, Any]]) -> None:
        self.history_manager.data = val

    def save_config_to_file(self) -> None:
        layers_data = []
        for entry in self.layer_entries:
            opacity_val = self.layer_opacities.get(entry.name, 1.0)
            if entry.name == "OpenStreetMap (OSM)":
                layers_data.append({
                    "type": "map",
                    "name": entry.name,
                    "checked": entry.checked,
                    "opacity": opacity_val
                })
            elif entry.name == "過去の出力履歴":
                layers_data.append({
                    "type": "history",
                    "name": entry.name,
                    "checked": entry.checked,
                    "opacity": opacity_val
                })
            elif entry.name in self.layers:
                store = self.layers[entry.name]
                if store.source_path:
                    layers_data.append({
                        "type": "line",
                        "name": entry.name,
                        "path": str(store.source_path.resolve()),
                        "checked": entry.checked,
                        "opacity": opacity_val
                    })
            else:
                layers_data.append({
                    "type": "map",
                    "name": entry.name,
                    "checked": entry.checked,
                    "opacity": opacity_val
                })

        config = {
            "default_encoding": "utf-8",
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
            "track_mode": self.track_mode_combo.currentData() or "point",
            "registered_maps": self.registered_maps,
            "layers": layers_data,
            "registered_lines": self.registered_lines,
        }
        self.app_config.save(config)

    def load_config_from_file(self) -> None:
        try:
            config = self.app_config.load()
            if not config:
                self.restore_recovery_backup()
                return
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
            track_mode = config.get("track_mode", "point")
            track_mode_index = self.track_mode_combo.findData(track_mode)
            self.track_mode_combo.setCurrentIndex(track_mode_index if track_mode_index != -1 else 0)
            
            self.registered_maps = config.get("registered_maps", [])
            self.registered_lines = config.get("registered_lines", [])

            layers_data = config.get("layers", [])
            
            self.added_maps = set()
            self.active_maps = set()
            self.active_layers = set()
            self.show_history_bounds = False

            # 線データ以外を先に読み込む
            for layer in layers_data:
                l_type = layer.get("type")
                name = layer.get("name")
                checked = layer.get("checked", False)
                opacity = layer.get("opacity", 1.0)
                if name:
                    self.layer_opacities[name] = opacity
                
                if l_type == "map":
                    if name != "OpenStreetMap (OSM)":
                        self.added_maps.add(name)
                    if checked:
                        self.active_maps.add(name)
                elif l_type == "history":
                    self.show_history_bounds = checked


            # 線データを自動ロード（save_config=Falseで保存ループを防ぐ）
            for layer in layers_data:
                l_type = layer.get("type")
                checked = layer.get("checked", False)
                if l_type == "line":
                    path_str = layer.get("path")
                    if path_str:
                        path = Path(path_str)
                        if path.exists():
                            self.load_path(path, checked=checked, save_config=False)

            # 通常設定より新しい可能性がある退避線を最後に適用する。
            self.restore_recovery_backup()

            self.update_layer_tree()
            self.restore_layer_order_from_config(layers_data)
            self.update_layer_tree()
            self.map_widget.set_layer_opacities(self.layer_opacities)
            self.apply_active_map()
        except Exception as e:
            print(f"Error loading config: {e}")

    def restore_layer_order_from_config(self, layers_data: list[dict[str, Any]]) -> None:
        entry_by_name = {entry.name: entry for entry in self.layer_entries}
        ordered_entries: list[LayerEntry] = []
        used_names: set[str] = set()

        for layer in layers_data:
            name = layer.get("name")
            entry = entry_by_name.get(name)
            if entry is None or entry.name in used_names:
                continue
            ordered_entries.append(entry)
            used_names.add(entry.name)

        for entry in self.layer_entries:
            if entry.name not in used_names:
                ordered_entries.append(entry)

        self.layer_entries = ordered_entries

    def load_history_from_file(self) -> None:
        self.history_manager.load()
        self.refresh_history_table()

    def save_history_to_file(self) -> None:
        self.history_manager.save()

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

    def add_to_history(
        self,
        name: str,
        bbox: tuple[float, float, float, float],
        geojson: dict[str, Any] | None = None,
    ) -> None:
        self.history_manager.add(name, bbox, geojson=geojson)
        self.refresh_history_table()
        self.update_map_history_bboxes()
        self.apply_filter()

    def export_bbox_for_features(self, features: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
        if self.selected_bbox:
            return self.selected_bbox
        coords: list[tuple[float, float]] = []
        for feature in features:
            geometry = feature.get("geometry") or {}
            for line in geometry.get("coordinates", []) if geometry.get("type") == "MultiLineString" else [geometry.get("coordinates", [])]:
                if geometry.get("type") == "Point":
                    continue
                for coord in line or []:
                    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                        coords.append((float(coord[0]), float(coord[1])))
        if not coords:
            return None
        lons = [coord[0] for coord in coords]
        lats = [coord[1] for coord in coords]
        return min(lons), min(lats), max(lons), max(lats)

    def features_for_export(self, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        export_features: list[dict[str, Any]] = []
        for feature in features:
            if self.selected_bbox:
                clipped_geom = clip_geometry_to_bbox(feature.get("geometry") or {}, self.selected_bbox)
                if not clipped_geom:
                    continue
                new_feature = feature.copy()
                new_feature["geometry"] = clipped_geom
            else:
                new_feature = feature.copy()
            export_features.append(new_feature)
        return export_features

    def _refresh_map(self, features: list[dict[str, Any]], preserve_view: bool = True) -> None:
        title = self.tr_msg("msg_filtered_count").split(":")[0] if features else "No data"
        if self.store.source_path:
            title = self.store.source_path.name
        self.map_widget.set_geojson(
            {"type": "FeatureCollection", "features": features},
            title=title,
            lang=self.current_lang.split("-")[0],
            translation=self.translation,
            preserve_view=preserve_view,
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
        filtered = self.layers_filtered[name]
        if not filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = self.features_for_export(filtered)
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
            history_bbox = self.export_bbox_for_features(export_features)
            if history_bbox:
                self.add_to_history(path.stem, history_bbox, geojson={"type": "FeatureCollection", "features": export_features})
            self.log_msg(self.tr_msg("msg_save_success_geojson").format(path=path).replace("\n", " "))
            QMessageBox.information(self, self.tr_msg("msg_save_success_title"), self.tr_msg("msg_save_success_geojson").format(path=path))
        except Exception as exc:
            QMessageBox.critical(self, self.tr_msg("msg_save_error_title"), str(exc))

    def export_turnout_json(self) -> None:
        name = self.get_selected_layer_name()
        if not name or name not in self.layers_filtered:
            QMessageBox.warning(self, self.tr_msg("msg_export_title"), "出力対象の線データレイヤーをツリーで選択してください。")
            return
        filtered = self.layers_filtered[name]
        if not filtered:
            QMessageBox.information(self, self.tr_msg("msg_export_title"), self.tr_msg("msg_no_export_data"))
            return
        export_features = self.features_for_export(filtered)
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
            tangent_mode = (self.track_mode_combo.currentData() == "tangent")
            data = geojson_to_nrclip_bytes(
                line_features, name, scale_x, scale_y,
                spline_tolerance=spline_tolerance,
                junction_spacing=junction_spacing,
                max_spacing=max_spacing,
                tangent_mode=tangent_mode
            )
            path.write_bytes(data)
            self.last_output_dir = path.parent
            self.add_to_history(
                name,
                self.selected_bbox,
                geojson={"type": "FeatureCollection", "features": line_features},
            )
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
        idx = 1
        while f"New Layer {idx}" in self.layers:
            idx += 1
        default_name = f"New Layer {idx}"
        
        dialog = AddLineLayerDialog(self, self.translation, default_name=default_name)
        if dialog.exec() != QDialog.Accepted:
            return
            
        name, path_str = dialog.get_data()
        if name in self.layers:
            QMessageBox.warning(self, self.tr_msg("msg_validation_error") or "警告", "同名のレイヤーがすでに存在します。")
            return
            
        if path_str:
            path = Path(path_str)
            if path.exists():
                self.load_path(path)
                if path.name in self.layers:
                    store = self.layers.pop(path.name)
                    for f in store.features:
                        if "properties" in f:
                            f["properties"]["_source"] = name
                    self.layers[name] = store
                    self.active_layers.discard(path.name)
                    self.active_layers.add(name)
                    
                    self.update_layer_tree()
                    self.select_tree_layer(name)
                    self.apply_filter()
                    self.save_config_to_file()
        else:
            self.create_new_empty_layer(name)

    def create_new_empty_layer(self, name: str) -> None:
        store = FeatureStore(fields=["name", "railway", "_source"])
        self.layers[name] = store
        self.active_layers.add(name)
        
        self.update_layer_tree()
        self.select_tree_layer(name)
        self.apply_filter()
        self.save_config_to_file()

    def on_add_history_clicked(self) -> None:
        # 既にツリーにあれば何もしない
        if any(e.name == "過去の出力履歴" for e in self.layer_entries):
            return
        self.show_history_bounds = True
        self.update_layer_tree()
        self.update_map_history_bboxes()
        self.apply_filter()

    def on_remove_layer_clicked(self) -> None:
        selected = self.layer_list.selectedItems()
        if not selected:
            return
        item = selected[0]
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        if not entry.removable:
            return
        if entry.name in self.layers:
            self._recovery_dirty_layers.discard(entry.name)
            self._recovery_deleted_layers.add(entry.name)
        if entry.on_remove:
            entry.on_remove()
        self.layer_entries.pop(idx)
        self.write_recovery_backup()
        self.save_config_to_file()
        self.update_layer_tree()
        self.log_msg(f"削除しました: {entry.name}")

    def on_edit_layer_clicked(self) -> None:
        selected = self.layer_list.selectedItems()
        if not selected:
            return
        idx = selected[0].data(Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        if entry.name not in self.layers:
            self.log_msg("このレイヤーは編集できません")
            return
        name, ok = QInputDialog.getText(self, "レイヤー編集", "レイヤー名:", text=entry.name)
        name = name.strip()
        if not ok or not name or name == entry.name:
            return
        if name in self.layers:
            QMessageBox.warning(self, self.tr_msg("msg_validation_error") or "警告", "同名のレイヤーがすでに存在します。")
            return
        store = self.layers.pop(entry.name)
        for feature in store.features:
            feature.setdefault("properties", {})["_source"] = name
        self.layers[name] = store
        self._recovery_dirty_layers.discard(entry.name)
        self._recovery_deleted_layers.add(entry.name)
        self._mark_recovery_dirty(name)
        if entry.name in self.active_layers:
            self.active_layers.remove(entry.name)
            self.active_layers.add(name)
        self.update_layer_tree()
        self.select_tree_layer(name)
        self.apply_filter()
        self.save_config_to_file()

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
            has_lines=True,
            opacity=self.layer_opacities.get("OpenStreetMap (OSM)", 1.0),
        )
        osm_entry.on_check_changed = _make_map_check("OpenStreetMap (OSM)")
        entries.append(osm_entry)

        # 登録済みマップ
        for cm in self.registered_maps:
            cm_name = cm.get("name", "")
            if cm_name not in self.added_maps:
                continue

            def _make_map_remove(n: str = cm_name) -> Callable[[], None]:
                def remove() -> None:
                    self.added_maps.discard(n)
                    self.active_maps.discard(n)
                    self.apply_active_map()
                    self.save_config_to_file()
                return remove

            entry = LayerEntry(
                name=cm_name,
                checked=(cm_name in self.active_maps),
                removable=True,
                has_lines=cm.get("has_lines", False),
                opacity=self.layer_opacities.get(cm_name, 1.0),
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
                has_lines=True,
                opacity=self.layer_opacities.get(ln, 1.0),
                on_check_changed=_make_line_check(),
                on_remove=_make_line_remove(),
            ))

        # --- 履歴 ---
        has_history = self.show_history_bounds
        if has_history:
            def _history_check(checked: bool) -> None:
                self.show_history_bounds = checked
                if checked:
                    self.update_map_history_bboxes()
                else:
                    if HAS_WEBENGINE:
                        self.map_widget.web.page().runJavaScript("window.clearHistoryBounds();")
                self.apply_filter()

            def _history_remove() -> None:
                self.show_history_bounds = False
                if HAS_WEBENGINE:
                    self.map_widget.web.page().runJavaScript("window.clearHistoryBounds();")
                self.apply_filter()

            entries.append(LayerEntry(
                name="過去の出力履歴",
                checked=self.show_history_bounds,
                removable=True,
                opacity=self.layer_opacities.get("過去の出力履歴", 1.0),
                on_check_changed=_history_check,
                on_remove=_history_remove,
            ))

        entry_by_name = {entry.name: entry for entry in entries}
        ordered_entries: list[LayerEntry] = []
        used_names: set[str] = set()

        for old_entry in self.layer_entries:
            entry = entry_by_name.get(old_entry.name)
            if entry is None:
                continue
            ordered_entries.append(entry)
            used_names.add(entry.name)

        for entry in entries:
            if entry.name not in used_names:
                ordered_entries.append(entry)

        self.layer_entries = ordered_entries

    def update_layer_tree(self) -> None:
        self._rebuild_layer_entries()
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for i, entry in enumerate(self.layer_entries):
            item = QListWidgetItem(entry.name, self.layer_list)
            item.setCheckState(Qt.Checked if entry.checked else Qt.Unchecked)
            item.setData(Qt.UserRole, i)
        self.layer_list.blockSignals(False)

    def on_layer_item_changed(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        checked = (item.checkState() == Qt.Checked)
        entry.checked = checked
        if entry.on_check_changed:
            entry.on_check_changed(checked)

    def on_layers_moved(self, parent, start, end, destination, row) -> None:
        new_entries = []
        for r in range(self.layer_list.count()):
            item = self.layer_list.item(r)
            idx = item.data(Qt.UserRole)
            if idx is not None and idx < len(self.layer_entries):
                new_entries.append(self.layer_entries[idx])
        
        self.layer_entries = new_entries
        
        # インデックスの振り直し
        self.layer_list.blockSignals(True)
        for r in range(self.layer_list.count()):
            item = self.layer_list.item(r)
            item.setData(Qt.UserRole, r)
        self.layer_list.blockSignals(False)
        
        self.save_config_to_file()
        self.apply_active_map(reload_map=False)
        self.apply_filter()

    def on_layer_selection_changed(self) -> None:
        self.update_window_title()
        active_layer_name = self.get_selected_layer_name()
        
        selected_items = self.layer_list.selectedItems()
        has_lines = False
        if selected_items:
            item = selected_items[0]
            idx = item.data(Qt.UserRole)
            if idx is not None and idx < len(self.layer_entries):
                entry = self.layer_entries[idx]
                has_lines = entry.has_lines
                self.filter_group.setEnabled(has_lines)
                
                if has_lines:
                    # スタックページの切り替え
                    if entry.name in self.layers:
                        # 線データの場合
                        if "osm" in entry.name.lower() or "overpass" in entry.name.lower():
                            self.filter_stack.setCurrentIndex(1)
                        else:
                            self.filter_stack.setCurrentIndex(0)
                    else:
                        # 線を含む地図の場合
                        self.filter_stack.setCurrentIndex(1)
                
                # 選択されているレイヤーに対応する不透明度をスライダーに適用
                opacity_val = self.layer_opacities.get(entry.name, 1.0)
                self.opacity_slider.blockSignals(True)
                self.opacity_slider.setEnabled(True)
                self.opacity_slider.setValue(int(opacity_val * 100))
                self.opacity_slider.blockSignals(False)
                self.opacity_val_label.setText(f"{int(opacity_val * 100)}%")
            else:
                self.filter_group.setEnabled(False)
                self.opacity_slider.setEnabled(False)
                self.opacity_val_label.setText("100%")
        else:
            self.filter_group.setEnabled(False)
            self.opacity_slider.setEnabled(False)
            self.opacity_val_label.setText("100%")

        if active_layer_name and active_layer_name in self.layers_filtered:
            self._refresh_table(self.layers_filtered[active_layer_name])
        else:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)

    def on_opacity_slider_changed(self, value: int) -> None:
        selected_items = self.layer_list.selectedItems()
        if not selected_items:
            return
        item = selected_items[0]
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self.layer_entries):
            return
        entry = self.layer_entries[idx]
        
        opacity = value / 100.0
        self.layer_opacities[entry.name] = opacity
        self.opacity_val_label.setText(f"{value}%")
        
        self.map_widget.set_layer_opacities(self.layer_opacities)
        
        is_map = (entry.name == "OpenStreetMap (OSM)" or entry.name in [m.get("name") for m in self.registered_maps])
        if is_map:
            self.apply_active_map(reload_map=True, preserve_view=True)
        else:
            self.apply_filter()
        self.save_config_to_file()

    def get_selected_layer_name(self) -> Optional[str]:
        selected = self.layer_list.selectedItems()
        if selected:
            item = selected[0]
            idx = item.data(Qt.UserRole)
            if idx is not None and idx < len(self.layer_entries):
                entry = self.layer_entries[idx]
                if entry.name in self.layers:
                    return entry.name
        return None

    def select_tree_layer(self, name: str) -> None:
        self.layer_list.blockSignals(True)
        for i, entry in enumerate(self.layer_entries):
            if entry.name in self.layers and entry.name == name:
                item = self.layer_list.item(i)
                if item:
                    self.layer_list.setCurrentItem(item)
                    item.setSelected(True)
                break
        self.layer_list.blockSignals(False)


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
    icon_path = get_resource_path("icon.ico")
    if icon_path.exists():
        icon = QIcon(str(icon_path.resolve()))
        if not icon.isNull():
            app.setWindowIcon(icon)
    win = MainWindow()
    # Windowsでは生成後にも明示設定しないと、タスクバーが汎用アイコンを保持する場合がある。
    if icon_path.exists():
        win.setWindowIcon(QIcon(str(icon_path.resolve())))
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists():
            win.load_path(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
