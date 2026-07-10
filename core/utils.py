import sys
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

def get_resource_path(relative_path: str) -> Path:
    """PyInstallerの一時展開先フォルダ(_MEIPASS)を考慮してリソースの絶対パスを取得する"""
    try:
        base_path = Path(sys._MEIPASS)
    except AttributeError:
        # 開発時は core/utils.py の2階層上（リポジトリルート）を基準とする
        base_path = Path(__file__).parent.parent
    return base_path / relative_path

def get_executable_dir() -> Path:
    """PyInstallerでパッケージ化されている場合は実行ファイルの場所、開発時はスクリプトの場所を返す"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # 開発時はリポジトリルートを返す
    return Path(__file__).parent.parent

def get_svg_icon(svg_path: Path, color_hex: str, size: QSize = QSize(16, 16)) -> QIcon:
    if not svg_path.exists():
        return QIcon()
    try:
        svg_content = svg_path.read_text(encoding="utf-8")
        svg_content = svg_content.replace("currentColor", color_hex)
        
        renderer = QSvgRenderer(QByteArray(svg_content.encode("utf-8")))
        
        icon = QIcon()
        # 1倍、2倍、3倍の解像度のピックスマップを生成してQIconに登録する
        # これにより、高DPI環境でもボケず、かつボタンからはみ出さずに正しく描画されます
        for scale in [1, 2, 3]:
            scaled_size = size * scale
            pixmap = QPixmap(scaled_size)
            pixmap.fill(Qt.transparent)
            
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            
            icon.addPixmap(pixmap)
            
        return icon
    except Exception as e:
        print(f"Error loading SVG icon {svg_path}: {e}")
        return QIcon()
