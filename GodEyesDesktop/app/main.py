# ============================================================
# AI USAGE NOTE
# This file contains code generated and/or substantially assisted
# by AI during the development of the God Eyes project.
#
# The student should review, test, modify, and be able to explain
# the code used in the final project.
#
# AI-assisted sections are marked below by [AI-GENERATED / AI-ASSISTED CODE].
# ============================================================

from pathlib import Path
import os
import shutil
import sys
import math
import threading
import queue
import re
import subprocess
import tempfile

# Make app-local modules importable whether this file is launched directly
# (python app\main.py) or imported by run.py / PyInstaller.
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
import time
import json
import argparse
import base64
import urllib.request
import urllib.error
from urllib.parse import quote, urlparse, parse_qs, urljoin
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, wait
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSettings, QThread, Signal, QSize, QRect
from PySide6.QtGui import QImage, QPixmap, QFont, QIcon, QPainter, QColor, QPen, QFontMetrics
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QStackedWidget, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QDialogButtonBox, QMessageBox, QProgressBar, QListWidget, QListWidgetItem, QAbstractItemView, QLineEdit, QCheckBox, QAbstractButton, QScrollArea, QGridLayout, QSizePolicy
# Use package-qualified imports when launched by run.py / PyInstaller,
# with a local fallback for direct execution of app\main.py.
try:
    from app.db import Database, EVIDENCE_DIR, ROOT as DATA_DIR
    from app.engine import CameraWorker, AIWorker, discover_onvif_cameras, probe_rtsp_camera
    from app.server_client import ServerClient, ServerClientError
    from app.event_gates import event_dedupe_key
except ModuleNotFoundError:
    from db import Database, EVIDENCE_DIR, ROOT as DATA_DIR
    from engine import CameraWorker, AIWorker, discover_onvif_cameras, probe_rtsp_camera
    from server_client import ServerClient, ServerClientError
    from event_gates import event_dedupe_key

CLIENT_VERSION = '0.15.1-test'
UI_REFRESH_VERSION = 'v15.1-unicode-temporal-face-id-best-evidence'
# User configuration must survive PyInstaller one-file extraction, whose
# temporary APP_DIR changes on every launch. Keep settings beside local history.
SERVER_CONFIG_PATH = DATA_DIR / 'server_config.json'
LEGACY_SERVER_CONFIG_PATH = APP_DIR / 'server_config.json'
MODEL_DIR = APP_DIR / 'assets' / 'models'
LOGO_PATH = APP_DIR / 'assets' / 'god_eyes_logo.svg'


def _godeyes_logo_pixmap(size: int = 256) -> QPixmap:
    """Render the bundled SVG brand mark to a pixmap for UI and window icons."""
    size = max(16, int(size))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    if not LOGO_PATH.is_file():
        return pixmap
    renderer = QSvgRenderer(str(LOGO_PATH))
    if not renderer.isValid():
        return pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap


# GODEYES_UI_V25_LINE_ICONS: consistent, restrained outline icon set.
def _godeyes_line_icon(kind: str, size: int = 20, color: str = '#1678f8') -> QPixmap:
    size = max(16, int(size))
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(max(1, size // 10))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == 'dashboard':
        cell = max(4, size // 3 - 1)
        gap = max(2, size // 9)
        start = max(2, (size - (2 * cell + gap)) // 2)
        painter.drawRoundedRect(start, start, cell, cell, 1.5, 1.5)
        painter.drawRoundedRect(start + cell + gap, start, cell, cell, 1.5, 1.5)
        painter.drawRoundedRect(start, start + cell + gap, cell, cell, 1.5, 1.5)
        painter.drawRoundedRect(start + cell + gap, start + cell + gap, cell, cell, 1.5, 1.5)
    elif kind == 'calendar':
        painter.drawRoundedRect(3, 4, size - 6, size - 7, 2, 2)
        painter.drawLine(3, 8, size - 3, 8)
        painter.drawLine(size // 3, 2, size // 3, 6)
        painter.drawLine(2 * size // 3, 2, 2 * size // 3, 6)
        painter.setBrush(QColor(color))
        dot = max(1, size // 10)
        for x, y in ((6, 11), (12, 11), (6, 14), (12, 14)):
            if x + dot < size - 2 and y + dot < size - 2:
                painter.drawEllipse(x, y, dot, dot)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == 'camera':
        painter.drawRoundedRect(2, size // 3, size - 4, size // 2, 2, 2)
        painter.drawRoundedRect(size // 4, size // 3 - 3, size // 4, 4, 1, 1)
        inset = size // 3
        painter.drawEllipse(inset, size // 2 - 1, size - 2 * inset, size - 2 * inset)
    elif kind == 'eye':
        painter.drawEllipse(2, size // 3, size - 4, size // 3)
        painter.setBrush(QColor(color))
        pupil = max(3, size // 4)
        painter.drawEllipse((size - pupil) // 2, (size - pupil) // 2, pupil, pupil)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.end()
    return pixmap

# Stable AI inference rate used by Scan and Monitor.
# This preserves the previous Balanced/default runtime setting (6 FPS)
# without depending on a Settings-page widget that may not exist.
DEFAULT_AI_FPS = 6.0


def _normalize_camera_source(value):
    """Normalize camera-source values emitted by different Teacher Portal versions."""
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        # Compatibility with portals that store the source selector as 0/1/2.
        return {'0': 'webcam', '1': 'usb', '2': 'wifi_camera'}.get(str(value).strip(), 'webcam')
    if isinstance(value, dict):
        value = value.get('camera_source', value.get('camera_type', value.get('source', value.get('type', 'webcam'))))
    source = re.sub(r'[^a-z0-9]+', '_', str(value or 'webcam').strip().lower()).strip('_')
    if source in {'tapo', 'tapo_c230', 'tapo_c230_rtsp', 'rtsp', 'wifi', 'wifi_rtsp',
                  'wifi_camera', 'ip_camera', 'network_camera', 'network', 'onvif',
                  'wireless_camera', 'ipcam', 'network_video', 'ip'} or 'rtsp' in source or 'wifi' in source or 'wi_fi' in source:
        return 'wifi_camera'
    if source in {'usb', 'usb_camera', 'usb_cam', 'usb_webcam', 'external_usb',
                  'external_usb_webcam', 'webcam_usb', 'external_camera', 'usb_video'} or ('usb' in source and ('cam' in source or 'video' in source)):
        return 'usb'
    if source in {'webcam', 'built_in', 'built_in_camera', 'built_in_webcam', 'built_in_cam',
                  'builtin', 'builtin_camera', 'builtin_webcam', 'integrated', 'integrated_camera',
                  'computer_camera', 'internal', 'internal_camera', 'internal_webcam', 'default_camera',
                  'default', 'local', 'local_camera', 'computer', 'built_in_video'} or 'built_in' in source or 'builtin' in source or 'integrated' in source or 'internal' in source:
        return 'webcam'
    return 'webcam'


def _normalize_ui_language(value):
    """Accept both compact language codes and the labels used by portal versions."""
    language = str(value or 'vi').strip().lower()
    if language in {'en', 'eng', 'english'} or language.startswith('english'):
        return 'en'
    if language in {'vi', 'vn', 'vietnamese', 'tiếng việt', 'tieng viet'} or language.startswith('vietnamese'):
        return 'vi'
    return 'vi'

# [AI-GENERATED / AI-ASSISTED CODE] UI stylesheet and visual design.
# Includes colors, typography, cards, buttons, navigation, tables, and progress bars.
STYLE = """
* { font-family: "Segoe UI"; }
QMainWindow { background: #f3f7fd; color: #10233f; font-size: 13px; }
QWidget { background: transparent; color: #10233f; font-size: 13px; }
QDialog { background: #f3f7fd; }
QScrollArea { background: #f3f7fd; border: 0; }
QScrollArea > QWidget > QWidget { background: #f3f7fd; }
QStackedWidget#mainPageStack { background: #f3f7fd; }
QWidget#scanPage, QWidget#monitorPage { background: #f3f7fd; }
QFrame#sidebar { background: #ffffff; border-right: 1px solid #e3ebf7; }
QLabel { background: transparent; border: 0; padding: 0; }
QLabel#title, QLabel#section, QLabel#muted, QLabel#eyebrow, QLabel#metric { background: transparent; border: 0; padding: 0; }
QLabel#logo { font-size: 23px; font-weight: 850; color: #155eef; }
QLabel#logo_image { background: transparent; border: 0; }
QLabel#eyebrow { background: transparent; color: #6d87ac; font-size: 10px; font-weight: 800; letter-spacing: 1px; }
QLabel#muted { background: transparent; color: #7185a5; }
QLabel#title { background: transparent; font-size: 29px; font-weight: 850; color: #10243b; }
QLabel#section { background: transparent; font-size: 15px; font-weight: 800; color: #10243b; }
QLabel#metric { background: transparent; font-size: 27px; font-weight: 850; color: #155eef; }
QLabel#pill { background: #eaf2ff; color: #1261ec; border: 1px solid #e0ebff; border-radius: 12px; padding: 6px 11px; font-weight: 800; }
QLabel#status_success { background: #e7f8f0; color: #07854d; border: 1px solid #d5f1e3; border-radius: 10px; padding: 7px 10px; font-weight: 800; }
QLabel#status_warning { background: #fff7e4; color: #936400; border: 1px solid #ffe9b4; border-radius: 10px; padding: 7px 10px; font-weight: 800; }
QLabel#camera { background: #0b1727; border: 0; border-radius: 16px; color: #e8f1ff; padding: 0; }
QLabel#student_avatar { background: #e2edff; color: #155eef; border: 1px solid #cfe0ff; border-radius: 28px; font-weight: 850; font-size: 15px; }
QLabel#headerIcon { background: #e8f2ff; color: #1678f8; border: 1px solid #d8e8ff; border-radius: 13px; font-size: 23px; font-weight: 900; }
QLabel#liveBadge { background: #e7f9ef; color: #0a9854; border: 1px solid #cef1df; border-radius: 10px; padding: 6px 10px; font-weight: 850; }
QLabel#emptyState { background: #f7faff; color: #7185a5; border: 1px dashed #cbdcf3; border-radius: 13px; padding: 18px; }
QFrame#card { background: #ffffff; border: 1px solid #dce7f5; border-radius: 16px; }
QFrame#metricCard { background: #f7faff; border: 1px solid #e1e8f3; border-radius: 12px; }
QFrame#dialogCard { background: #ffffff; border: 1px solid #e1e9f5; border-radius: 18px; }
QFrame#clockCard { background: #ffffff; border: 1px solid #d9e7fb; border-radius: 13px; }
QFrame#forceStopNotice { background: #fff1f2; border: 1px solid #ffe0e4; border-radius: 14px; }
QFrame#studentCard { background: #ffffff; border: 1px solid #dce8f8; border-radius: 15px; }
QFrame#studentCardSelected { background: #edf5ff; border: 2px solid #1680ff; border-radius: 15px; }
QFrame#connectionStatus { background: #fff7e5; border: 1px solid #ffe7b5; border-radius: 11px; }
QFrame#primaryActionBar { background: #087bfa; border: 1px solid #087bfa; border-radius: 14px; }
QFrame#primarySeparator { background: rgba(255,255,255,0.45); max-width: 1px; min-width: 1px; }
QPushButton { background: #ffffff; color: #24415e; border: 1px solid #d0ddef; border-radius: 11px; padding: 9px 13px; font-weight: 750; min-height: 22px; }
QPushButton:hover { background: #f4f8ff; border-color: #8bbcff; }
QPushButton:pressed { background: #e7f1ff; }
QPushButton:disabled { color: #a1b0c5; background: #f6f8fc; border-color: #e1e8f2; }
QPushButton#primary { background: #087bfa; color: #ffffff; border: 0; font-size: 14px; font-weight: 850; min-height: 44px; padding: 10px 22px; }
QPushButton#primary:hover { background: #006de8; }
QPushButton#primary:disabled { background: #b9d7ff; color: #f8fbff; }
QPushButton#minorAction { background: #f6faff; color: #145fe5; border: 1px solid #d6e5fa; border-radius: 10px; padding: 7px 9px; font-size: 12px; font-weight: 800; min-height: 20px; }
QPushButton#minorAction:hover { background: #eaf3ff; border-color: #a7ccff; }
QPushButton#durationPreset { background: #ffffff; color: #17375f; border: 1px solid #d9e5f5; border-radius: 10px; padding: 6px 7px; font-size: 12px; font-weight: 750; min-height: 30px; max-height: 36px; }
QPushButton#durationPreset:hover { background: #f2f7ff; border-color: #9fc6ff; }
QPushButton#durationPreset:checked { background: #087bfa; color: #ffffff; border: 1px solid #087bfa; }
QPushButton#primaryActionButton { background: transparent; color: #ffffff; border: 0; border-radius: 11px; font-size: 13px; font-weight: 900; padding: 8px 12px; min-height: 42px; max-height: 46px; }
QPushButton#primaryActionButton:hover { background: rgba(255,255,255,0.10); }
QPushButton#primaryActionButton:disabled { background: transparent; color: #e6f1ff; }
QPushButton#danger { color: #c32332; background: #fff5f6; border: 1px solid #ffd5db; border-radius: 10px; }
QPushButton#nav, QPushButton#nav_active { border: 0; text-align: left; padding: 12px 14px; min-height: 24px; border-radius: 11px; font-size: 13px; }
QPushButton#nav { background: transparent; color: #52677f; }
QPushButton#nav:hover { background: #f0f6ff; color: #155eef; }
QPushButton#nav_active { background: #e9f2ff; color: #1261ec; font-weight: 850; }
QComboBox, QTableWidget, QListWidget, QLineEdit { background: #ffffff; color: #193858; border: 1px solid #d0dff2; border-radius: 10px; padding: 8px 10px; selection-background-color: #dceaff; }
QComboBox:focus, QTableWidget:focus, QListWidget:focus, QLineEdit:focus { border-color: #80b4ff; }
QComboBox { padding-right: 30px; outline: none; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: 0; border-left: 1px solid #e2ebf7; border-top-right-radius: 10px; border-bottom-right-radius: 10px; background: #f7faff; }
QComboBox::down-arrow { width: 0px; height: 0px; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #7187a2; }
QComboBox::drop-down:hover { background: #eaf3ff; }
QComboBox QAbstractItemView { background: #ffffff; color: #193858; selection-background-color: #e6f1ff; selection-color: #174c9d; border: 1px solid #d0dff2; }
QHeaderView::section { background: #f2f6fc; border: 0; padding: 9px; color: #5d7086; font-weight: 800; }
QTableWidget { gridline-color: #edf1f7; }
QListWidget { padding: 6px; outline: none; }
QListWidget::item { border: 0; padding: 0; margin: 3px 0; color: #243b53; }
QListWidget::item:selected { background: #edf4ff; color: #243b53; border-radius: 8px; }
QListWidget::item:selected:active { background: #edf4ff; color: #243b53; }
QListWidget::item:hover { background: #f5f8fc; color: #243b53; }
QProgressBar { background: #e8edf5; border: 0; border-radius: 6px; height: 8px; }
QProgressBar::chunk { background: #1679f8; border-radius: 6px; }
QCheckBox { spacing: 8px; font-size: 12px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked { background: #ffffff; border: 1px solid #9ab0cd; border-radius: 4px; }
QCheckBox::indicator:checked { background: #087bfa; border: 1px solid #087bfa; border-radius: 4px; }
QRadioButton { background: transparent; spacing: 8px; }
QScrollBar:vertical { width: 10px; margin: 2px 0; background: transparent; }
QScrollBar::handle:vertical { background: #c8d8ed; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #172d4a; color: #ffffff; border: 1px solid #6ba6ff; border-radius: 5px; padding: 5px; }
"""

DARK_STYLE = """
* { font-family: "Segoe UI"; }
QMainWindow { background: #0b1220; color: #f4f7fb; font-size: 13px; }
QWidget { background: transparent; color: #f4f7fb; font-size: 13px; }
QDialog { background: #0b1220; color: #f4f7fb; }
QScrollArea { background: #0b1220; border: 0; }
QScrollArea > QWidget > QWidget { background: #0b1220; }
QStackedWidget#mainPageStack { background: #0b1220; }
QWidget#scanPage, QWidget#monitorPage { background: #0b1220; }
QLabel { background: transparent; }
QFrame#sidebar { background: #0d1726; border-right: 1px solid #2b415b; }
QLabel#logo_image { background: transparent; border: 0; }
QLabel#eyebrow { color: #8ccfff; font-size: 10px; font-weight: 850; letter-spacing: 1px; }
QLabel#muted { color: #b6c5d6; }
QLabel#title { font-size: 29px; font-weight: 900; color: #f7faff; }
QLabel#section { font-size: 15px; font-weight: 850; color: #f1f6fb; }
QLabel#metric { font-size: 27px; font-weight: 900; color: #70c8ff; }
QLabel#pill { background: #17395e; color: #8bd4ff; border: 1px solid #326a98; border-radius: 12px; padding: 6px 11px; font-weight: 850; }
QLabel#status_success { background: #173a2a; color: #8be7b1; border-radius: 10px; padding: 7px 10px; font-weight: 850; }
QLabel#status_warning { background: #403719; color: #efd977; border-radius: 10px; padding: 7px 10px; font-weight: 850; }
QLabel#camera { background: #08111e; border: 1px solid #2b415b; border-radius: 16px; color: #b2c1d2; padding: 0; }
QLabel#student_avatar { background: #173a61; color: #8fd6ff; border: 1px solid #3a6f9e; border-radius: 28px; font-weight: 900; font-size: 15px; }
QLabel#headerIcon { background: #17395e; color: #8bd4ff; border: 1px solid #326a98; border-radius: 13px; font-size: 23px; font-weight: 900; }
QLabel#liveBadge { background: #173a2a; color: #8be7b1; border: 1px solid #31684a; border-radius: 10px; padding: 6px 10px; font-weight: 850; }
QLabel#emptyState { background: #101d2e; color: #b6c5d6; border: 1px dashed #35516f; border-radius: 13px; padding: 18px; }
QFrame#card, QFrame#dialogCard { background: #111d2d; border: 1px solid #2c435e; border-radius: 16px; }
QFrame#metricCard { background: #142438; border: 1px solid #31506d; border-radius: 12px; }
QFrame#clockCard { background: #111d2d; border: 1px solid #31506d; border-radius: 13px; }
QFrame#forceStopNotice { background: #3d2027; border: 1px solid #8a4b57; border-radius: 14px; }
QFrame#studentCard { background: #111d2d; border: 1px solid #2c435e; border-radius: 13px; }
QFrame#studentCardSelected { background: #17314d; border: 2px solid #4eafff; border-radius: 15px; }
QFrame#connectionStatus { background: #173a2a; border: 1px solid #31684a; border-radius: 11px; }
QFrame#primaryActionBar { background: #146ac0; border: 1px solid #398ad9; border-radius: 14px; }
QFrame#primarySeparator { background: rgba(255,255,255,0.45); max-width: 1px; min-width: 1px; }
QPushButton { background: #152337; color: #e6eef6; border: 1px solid #3a536f; border-radius: 10px; padding: 9px 13px; font-weight: 800; min-height: 22px; }
QPushButton:hover { background: #1b2e46; border-color: #67bfff; color: #ffffff; }
QPushButton:pressed { background: #203b59; }
QPushButton:disabled { color: #728399; background: #111b29; border-color: #26384d; }
QPushButton#primary { background: #2b8fdf; color: #ffffff; border: 1px solid #59baff; font-size: 14px; font-weight: 900; min-height: 44px; padding: 10px 22px; }
QPushButton#primary:hover { background: #42a6ee; }
QPushButton#primary:disabled { background: #244667; color: #a8bdd0; border-color: #315675; }
QPushButton#minorAction { background: #14263c; color: #8bd4ff; border: 1px solid #31506d; border-radius: 10px; padding: 8px 11px; font-weight: 800; }
QPushButton#durationPreset { background: #152337; color: #e6eef6; border: 1px solid #3a536f; border-radius: 11px; padding: 8px; font-weight: 750; min-height: 34px; }
QPushButton#durationPreset:checked { background: #237dca; color: #ffffff; border: 1px solid #58b8ff; }
QPushButton#primaryActionButton { background: transparent; color: #ffffff; border: 0; border-radius: 11px; font-size: 13px; font-weight: 900; padding: 8px 12px; min-height: 42px; max-height: 46px; }
QPushButton#primaryActionButton:hover { background: rgba(255,255,255,0.10); }
QPushButton#primaryActionButton:disabled { background: transparent; color: #bed9f1; }
QPushButton#danger { color: #ffb3bc; background: #3d2027; border-color: #8a4b57; }
QPushButton#nav, QPushButton#nav_active { border: 0; text-align: left; padding: 12px 14px; min-height: 24px; border-radius: 11px; font-size: 13px; }
QPushButton#nav { background: transparent; color: #b7c6d7; }
QPushButton#nav:hover { background: #17283c; color: #ffffff; }
QPushButton#nav_active { background: #173a61; color: #8fd6ff; font-weight: 900; border-left: 3px solid #63c1ff; }
QComboBox, QTableWidget, QListWidget, QLineEdit { background: #0e1929; color: #f1f6fb; border: 1px solid #3a536f; border-radius: 10px; padding: 8px 10px; }
QComboBox:focus, QTableWidget:focus, QListWidget:focus, QLineEdit:focus { border-color: #63c1ff; }
QComboBox { padding-right: 30px; outline: none; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: 0; border-left: 1px solid #334b67; border-top-right-radius: 10px; border-bottom-right-radius: 10px; background: #14263c; }
QComboBox::down-arrow { width: 0px; height: 0px; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #b8cce3; }
QComboBox::drop-down:hover { background: #1c3a5b; }
QComboBox QAbstractItemView { background: #111d2d; color: #f1f6fb; selection-background-color: #1e507e; selection-color: #ffffff; border: 1px solid #3a536f; }
QHeaderView::section { background: #17283c; border: 0; padding: 9px; color: #c7d4e2; font-weight: 850; }
QTableWidget { gridline-color: #2b415b; }
QProgressBar { background: #24364d; border: 0; border-radius: 6px; height: 8px; }
QProgressBar::chunk { background: #5dbdff; border-radius: 6px; }
QCheckBox { spacing: 8px; font-size: 12px; color: #e8eff6; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked { background: #0e1929; border: 1px solid #5b7697; border-radius: 4px; }
QCheckBox::indicator:checked { background: #278de5; border: 1px solid #5cbaff; border-radius: 4px; }
QScrollBar:vertical { width: 10px; margin: 2px 0; background: transparent; }
QScrollBar::handle:vertical { background: #35516f; border-radius: 5px; min-height: 28px; }
QToolTip { background: #16283c; color: #ffffff; border: 1px solid #59baff; }
"""


VI_TO_EN = {
    'Tổng quan': 'Dashboard', 'Lớp học': 'Classes', 'Học sinh': 'Students', 'Lịch sử': 'History', 'Cài đặt': 'Settings',
    'học sinh': 'students', 'LỚP': 'CLASS', 'Lớp': 'Class', 'lớp': 'class', 'lớp học': 'class',
    'vắng': 'absent', 'Vắng': 'Absent', 'được chọn': 'selected', 'đã được nhận diện': 'have been identified',
    'học sinh khác.': 'other students.', 'Đã đánh dấu vắng:': 'Marked absent:', 'Học sinh được chọn (': 'Selected students (',
    'đã được nhận diện': 'identified', 'loại trừ': 'excluded', 'học sinh vắng khỏi Face ID': 'absent students from Face ID',
    'Face ID sẽ tải khi bắt đầu': 'Face ID will load when starting', 'Chưa chọn học sinh': 'No students selected',
    'Chọn lớp': 'Select class', 'Lớp đang xem': 'Current class', 'Tất cả lớp học': 'All classes',
    'God Eyes Classroom': 'God Eyes Room', 'Bắt đầu một buổi học mới': 'Start a new class session',
    'Một quy trình rõ ràng cho giáo viên: chọn lớp → điểm danh → quét Face ID → giám sát.': 'Choose a class → attendance → Face ID scan → monitoring.',
    'Bạn chỉ cần chọn lớp và điểm danh một lần. Sau đó God Eyes tự xử lý camera, nhận diện và ghi nhận quan sát.': 'Choose the class and attendance once. God Eyes then handles the camera, identification and observations.',
    'BẮT ĐẦU  →': 'START  →', 'Camera Setup': 'Camera Setup', 'Chọn lớp từ God Eyes Server trước khi bắt đầu. Camera hiện tại dùng webcam; IP/RTSP/ONVIF sẽ nối ở bước camera tiếp theo.': 'Choose a class from God Eyes Server before starting. The current camera is a webcam; IP/RTSP/ONVIF can be connected later.',
    'Lớp học': 'Class', 'Lớp': 'Room', 'Room': 'Lớp', 'Chưa kết nối server': 'Server not connected', 'Webcam': 'Webcam', 'Board direction': 'Board direction',
    'Gợi ý: đặt camera lệch khoảng 45° so với hướng nhìn của học sinh để AI quan sát tự nhiên hơn.': 'Tip: place the camera about 45° from the students’ viewing direction for more natural observation.',
    'Student Scan': 'Student Scan', 'Danh sách điểm danh chỉ chọn một lần ở đầu buổi. God Eyes sẽ quét mọi người xuất hiện trong camera và chỉ đối chiếu với nhóm đã chọn.': 'Attendance is selected once at the start. God Eyes scans everyone visible and matches only against the selected group.',
    '1  ĐIỂM DANH': '1  ATTENDANCE', '2  QUÉT KHUÔN MẶT': '2  FACE SCAN', '3  GIÁM SÁT': '3  MONITOR',
    'SẴN SÀNG QUÉT': 'READY TO SCAN', 'Chưa chọn lớp': 'No class selected', 'Chưa chọn học sinh': 'No students selected', '0 người được nhận diện': '0 people identified',
    'Học sinh': 'Student', 'Trạng thái': 'Status', 'Khớp': 'Match', 'SẴN SÀNG': 'READY', 'Xem danh sách đã chọn': 'View selected list',
    'Live Monitor': 'Live Monitor', 'MONITORING': 'MONITORING', '0 students': '0 students', '0 events': '0 events', 'Latest observations': 'Latest observations',
    'End Session': 'End Session', 'Reset ID': 'Reset ID', 'Every session is independent. Evidence được lưu cục bộ và đồng bộ lên God Eyes Server.': 'Every session is independent. Evidence is stored locally and synced to God Eyes Server.',
    'Kết nối server và chọn mức tải AI phù hợp với máy tính.': 'Server connection and teacher interface preferences.', 'God Eyes Server': 'God Eyes Server',
    'Tên đăng nhập giáo viên': 'Teacher username', 'KẾT NỐI / ĐỔI TÀI KHOẢN': 'CONNECT / SWITCH ACCOUNT', 'ĐĂNG XUẤT': 'SIGN OUT',
    'Cài đặt giáo viên': 'Teacher Settings', 'Giao diện': 'Appearance', 'Ngôn ngữ': 'Language', 'Sáng': 'Light', 'Tối': 'Dark', 'Tiếng Việt': 'Vietnamese',
    'LƯU CÀI ĐẶT': 'SAVE SETTINGS', 'Chọn giao diện hiển thị cho God Eyes App.': 'Choose the God Eyes App appearance.', 'Chọn Light hoặc Dark. Thay đổi sẽ áp dụng ngay trên ứng dụng.': 'Choose Light or Dark. Changes apply immediately.',
    'Chọn Tiếng Việt hoặc English. Ngôn ngữ được đồng bộ theo tài khoản.': 'Choose Vietnamese or English. Language is synced to your account.', 'Cài đặt Server đã đồng bộ.': 'Server settings synced.',
    'Đã lưu vào tài khoản giáo viên.': 'Saved to your Teacher account.', 'Server chưa kết nối.': 'Server not connected.',
    'Kết nối God Eyes Server': 'Connect to God Eyes Server', 'Đăng nhập tài khoản giáo viên để tải lớp học, danh sách học sinh và Face ID.': 'Sign in with a teacher account to load classes, students and Face ID.',
    'Tên đăng nhập': 'Username', 'Mật khẩu': 'Password', 'Nhập mật khẩu': 'Enter password', 'Ghi nhớ tài khoản trên máy này': 'Remember this account on this computer',
    'LOCAL / OFFLINE AI': 'LOCAL / OFFLINE AI', 'Camera và Face ID được xử lý trên máy God Eyes.': 'Camera and Face ID are processed on this God Eyes computer.',
    'HỦY': 'CANCEL', 'ĐĂNG NHẬP & KẾT NỐI': 'SIGN IN & CONNECT', 'ĐANG ĐĂNG NHẬP…': 'SIGNING IN…', 'Đang kiểm tra tài khoản và kết nối server…': 'Checking account and connecting to server…',
    'Face ID • Quét khuôn mặt học sinh': 'Face ID • Scan student face', 'ĐANG KHỞI ĐỘNG CAMERA…': 'STARTING CAMERA…', 'Đang khởi động…': 'Starting…', 'QUÉT LẠI': 'SCAN AGAIN',
    'Cần đúng một khuôn mặt rõ ràng trong khung hình': 'Exactly one clear face is required in the frame', 'Đã tải Face ID model • chờ camera': 'Face ID model loaded • waiting for camera',
    'FACE ID ĐÃ LƯU • đóng cửa sổ…': 'FACE ID SAVED • closing window…', 'Delete': 'Delete', 'Delete All': 'Delete All', 'View Session & Evidence': 'View Session & Evidence',
    'Date': 'Date', 'Duration': 'Duration', 'Status': 'Status', 'Students': 'Students', 'Events': 'Events', 'Session': 'Session',
    'Pause': 'Pause', 'Resume': 'Resume', 'Exit': 'Exit', 'CONNECTED': 'CONNECTED', 'DISCONNECTED': 'DISCONNECTED',
    'Được kết nối': 'Connected', 'Server đang kết nối': 'Server connected', 'Server chưa kết nối': 'Server not connected',
    'Chưa có lớp học': 'No classes yet', 'Tạo lớp trên Server trước': 'Create a class on the Server first',
    'Chọn lớp trước khi Start Session': 'Select a class before starting a session', 'Chưa chọn lớp': 'No class selected',
    'Lớp đã chọn': 'Selected class', 'Đã sẵn sàng. Bấm “BẮT ĐẦU” để khởi động AI và quét Face ID.': 'Ready. Click “START” to launch AI and scan Face ID.',
    'Danh sách tham gia buổi học': 'Session attendance list', 'Đang kết nối camera…': 'Connecting camera…',
    'Camera đã sẵn sàng • AI sẽ bắt đầu quét ngay.': 'Camera is ready • AI will start scanning now.', 'ĐANG QUÉT…': 'SCANNING…',
    'CHƯA CÓ HỌC SINH CÓ FACE ID ĐƯỢC CHỌN': 'NO SELECTED STUDENT HAS FACE ID',
    'KHÔNG CÓ FACE ID READY TRONG NHÓM ĐƯỢC CHỌN': 'NO FACE ID READY IN THE SELECTED GROUP',
    'Học sinh được giáo viên đánh dấu vắng trước khi bắt đầu quét.': 'Students marked absent by the teacher before scanning are excluded.',
    'AI chưa khởi động • hãy bấm BẮT ĐẦU để quét.': 'AI has not started • click START to scan.',
    'CHƯA KHÓA ĐƯỢC DANH TÍNH • giữ khuôn mặt rõ 2–3 giây trước camera': 'IDENTITY NOT LOCKED • keep the face clear in front of the camera for 2–3 seconds',
    'IDENTITY UNCERTAIN': 'IDENTITY UNCERTAIN', 'SERVER SYNC •': 'SERVER SYNC •', 'SERVER SESSION #': 'SERVER SESSION #',
    'SCANNING • camera có thể thấy bất kỳ số người nào •': 'SCANNING • the camera may see any number of people •',
    'học sinh được chọn có Face ID': 'selected students have Face ID', 'selected students detected': 'selected students detected',
    'Có mặt đã chọn:': 'Selected present:', 'AI đã thấy:': 'Seen by AI:', 'Vắng/loại trừ:': 'Absent/excluded:',
    'ĐÃ RESET FACE ID • tiếp tục quét với cùng danh sách đã chọn': 'FACE ID RESET • continue scanning with the same selected list',
    'FACE ID READY • LỚP': 'FACE ID READY • CLASS', 'Face ID sẵn sàng': 'Face ID ready', 'Face ID chưa sẵn sàng': 'Face ID not ready',
    'Đang lưu Face ID vào Server…': 'Saving Face ID to the Server…', 'Giữ đầu ổn định trong 1–2 giây…': 'Keep your head steady for 1–2 seconds…',
    'Server từ chối lưu Face ID.': 'The Server rejected the Face ID save.', 'Không thể chuyển tài khoản cũ sang đăng nhập tự động •': 'Could not migrate the old account to automatic sign-in •',
    'Vui lòng nhập đầy đủ Server URL, tên đăng nhập và mật khẩu.': 'Enter the Server URL, username and password.',
    'Đã kết nối:': 'Connected:', 'Lỗi kết nối:': 'Connection error:', 'ROSTER ERROR •': 'ROSTER ERROR •',
    'SERVER ERROR': 'SERVER ERROR', 'FACE MODEL ERROR •': 'FACE MODEL ERROR •', 'SERVER FINISH ERROR •': 'SERVER FINISH ERROR •',
    'SERVER SESSION ERROR •': 'SERVER SESSION ERROR •', 'SERVER HEARTBEAT ERROR •': 'SERVER HEARTBEAT ERROR •',
    'ABSENT SYNC ERROR •': 'ABSENT SYNC ERROR •', 'SERVER SYNC ERROR •': 'SERVER SYNC ERROR •',
    'Không tìm thấy YuNet:': 'YuNet not found:', 'Không tìm thấy SFace:': 'SFace not found:',
    'LƯU FACE ID THẤT BẠI •': 'FAILED TO SAVE FACE ID •', 'Tự đăng nhập từ Server thất bại •': 'Automatic sign-in from the Server failed •',
    'Tự đăng nhập thất bại •': 'Automatic sign-in failed •', 'Unable to sync settings •': 'Unable to sync settings •',
    'Không đồng bộ được cài đặt •': 'Unable to sync settings •', 'Server không trả về cài đặt giáo viên hợp lệ.': 'The Server returned invalid teacher settings.',
    'Server không lưu được cài đặt giáo viên.': 'The Server could not save teacher settings.', 'Server chưa kết nối.': 'Server is not connected.',
    'Đã đăng xuất khỏi server.': 'Signed out from the Server.', 'Đã tự đăng nhập •': 'Signed in automatically •',
    'Đã tải Face ID model • chờ camera': 'Face ID model loaded • waiting for camera',
    'Cần đúng một khuôn mặt rõ ràng trong khung hình': 'Exactly one clear face is required in the frame',
    'Đưa đúng một học sinh vào giữa khung hình…': 'Place one student in the center of the frame…',
    'Đưa đúng một học sinh đứng trước camera. Hệ thống sẽ tự động quét và lưu embedding vào God Eyes Server.': 'Place one student in front of the camera. The system will scan and save the embedding to the God Eyes Server.',
    'Đang kiểm tra tài khoản và kết nối server…': 'Checking the account and connecting to the Server…',
    'Camera và Face ID được xử lý trên máy God Eyes.': 'Camera and Face ID are processed locally on this God Eyes computer.',
    'Kết nối God Eyes Server': 'Connect to God Eyes Server', 'Tên đăng nhập': 'Username', 'Mật khẩu': 'Password',
    'Nhập mật khẩu': 'Enter password', 'Ghi nhớ tài khoản trên máy này': 'Remember this account on this computer',
    'Ví dụ: giao_vien01': 'Example: teacher01', 'Bắt đầu buổi học': 'Start Session',
    'Chọn học sinh có mặt hôm nay. Sau khi bấm "BẮT ĐẦU QUÉT", danh sách này được giữ nguyên cho đến khi kết thúc buổi học.': 'Select today’s present students. After you click “START SCAN”, this list stays fixed until the session ends.',
    'Đang tải danh sách…': 'Loading list…', 'Tìm theo tên hoặc mã học sinh…': 'Search by student name or code…',
    'CHỌN TẤT CẢ': 'SELECT ALL', 'BỎ CHỌN TẤT CẢ': 'CLEAR ALL',
    'Người được bỏ chọn sẽ được loại khỏi Face ID matching và ghi nhận ABSENT trên Server.': 'Unselected students are excluded from Face ID matching and recorded as ABSENT on the Server.',
    'Sĩ số lớp:': 'Class size:', 'Chọn có mặt:': 'Present selected:', 'Vắng:': 'Absent:', 'Face ID sẵn sàng:': 'Face ID ready:',
    'Hãy chọn ít nhất một học sinh có mặt.': 'Select at least one present student.',
    'Các học sinh được chọn chưa có Face ID. Hãy tạo Face ID trên Server trước khi quét.': 'Some selected students do not have Face ID. Create Face ID on the Server before scanning.',
    'Giữ đầu ổn định trong 1–2 giây…': 'Keep your head steady for 1–2 seconds…', 'Ổn định:': 'Stable:',
    'Thiết lập giao diện và ngôn ngữ cho God Eyes App. Các lựa chọn được lưu theo tài khoản giáo viên trên Server.': 'Set the God Eyes appearance and language. These options are saved to your Teacher account on the Server.',
    'Server connection and teacher interface preferences.': 'Server connection and teacher interface preferences.',
    'Cài đặt': 'Settings', 'Giao diện': 'Appearance', 'Ngôn ngữ': 'Language', 'Sáng': 'Light', 'Tối': 'Dark', 'Tiếng Việt': 'Vietnamese',
    'Chọn giao diện hiển thị cho God Eyes App.': 'Choose the God Eyes appearance.', 'Chọn Light hoặc Dark. Thay đổi sẽ áp dụng ngay trên ứng dụng.': 'Choose Light or Dark. Changes apply immediately.',
    'Chọn Tiếng Việt hoặc English. Ngôn ngữ được đồng bộ theo tài khoản.': 'Choose Vietnamese or English. Language is synced to your account.',
    'Cài đặt Server đã đồng bộ.': 'Server settings synced.', 'Đã lưu vào tài khoản giáo viên.': 'Saved to your Teacher account.',
    'God Eyes • Evidence Viewer': 'God Eyes • Evidence Viewer', 'SELECT AN OBSERVATION': 'SELECT AN OBSERVATION',
    'Evidence viewer': 'Evidence viewer', 'Delete Session': 'Delete Session', 'Delete this session, events and evidence?': 'Delete this session, events and evidence?',
    'Delete all history and evidence?': 'Delete all history and evidence?', 'Date': 'Date', 'Duration': 'Duration', 'Status': 'Status', 'Students': 'Students', 'Events': 'Events',
    'Session': 'Session', 'Pause': 'Pause', 'Resume': 'Resume', 'Exit': 'Exit',
}

# v23: close the remaining language gaps from legacy UI strings.
VI_TO_EN.update({
    'Phiên bản': 'Version', 'Tệp': 'File', 'Trạng thái': 'Status',
    'Hệ thống': 'System', 'Kết nối': 'Connect', 'SẴN SÀNG': 'READY',
    'Sẵn sàng': 'Ready', 'ĐÃ KẾT NỐI': 'CONNECTED', 'CHƯA KẾT NỐI': 'DISCONNECTED',
    'BẮT ĐẦU': 'START', 'QUÉT': 'SCAN', 'Tạm dừng': 'Pause', 'Tiếp tục': 'Resume',
    'Kết thúc buổi học': 'End Session', 'Đặt lại danh tính': 'Reset ID',
    'Quan sát gần nhất': 'Latest observations', 'Quan sát': 'Observation',
    'Minh chứng': 'Evidence', 'Buổi học': 'Session', 'Sự kiện': 'Events',
    'Học sinh': 'Student', 'Đăng nhập': 'Sign in', 'Đăng xuất': 'Sign out',
    'Máy chủ': 'Server', 'Tài khoản giáo viên': 'Teacher account',
    'Ứng dụng God Eyes': 'God Eyes App', 'Cài đặt': 'Settings',
})

VI_TO_EN.update({
    'Danh sách điểm danh chỉ chọn một lần ở đầu buổi. God Eyes sẽ quét mọi người xuất hiện trong camera và chỉ đối chiếu với nhóm đã chọn.': 'Attendance is selected once at the start. GodEyes scans people visible to the camera and matches only against the selected group.',
    'Hãy chọn ít nhất một học sinh có mặt.': 'Select at least one student who is present.',
    'Lớp này chưa có học sinh trên Server.': 'This class has no students on the Server yet.',
    'ĐÃ HỦY ĐIỂM DANH': 'ATTENDANCE CANCELLED', 'ĐANG TẢI DANH SÁCH HỌC SINH…': 'LOADING STUDENT LIST…',
    'ĐANG KẾT NỐI CAMERA…': 'CONNECTING TO CAMERA…', 'ĐÃ SẴN SÀNG. BẤM BẮT ĐẦU ĐỂ KHỞI ĐỘNG AI VÀ QUÉT FACE ID.': 'Ready. Click Start to launch AI and scan faces.',
    'ĐÃ SẴN SÀNG • BẤM BẮT ĐẦU QUÉT FACE ID': 'READY • CLICK START FACE SCAN',
    'ĐANG QUÉT FACE ID…': 'SCANNING FACES…', 'ĐANG KHỞI ĐỘNG BUỔI HỌC…': 'STARTING SESSION…',
    'AI ĐANG CHẠY…': 'AI RUNNING…', 'ĐANG GIÁM SÁT…': 'MONITORING…',
    'Quy trình dành cho giáo viên: chọn lớp → điểm danh → quét Face ID → giám sát trực tiếp.': 'Teacher workflow: select a class → attendance → face scan → live monitoring.',
    'Chọn lớp, điểm danh học sinh và đặt thời gian quét tối đa.': 'Choose a class, mark attendance and set a scan time limit.',
    'Kết nối máy chủ để tải danh sách lớp.': 'Connect to the Server to load classes.',
    'Chỉ học sinh được chọn mới được đối chiếu khuôn mặt và ghi nhận dữ liệu trong buổi quét.': 'Only checked students will be included in face matching and session records.',
    'Chưa điểm danh cho buổi quét này.': 'Attendance has not been completed.',
    'phút (1–180)': 'minutes (1–180)', '15 phút': '15 minutes', '30 phút': '30 minutes',
    '45 phút': '45 minutes', '60 phút': '60 minutes', 'Tùy chỉnh': 'Custom',
    'Camera': 'Camera', 'Tài khoản camera': 'Camera account', 'Tên đăng nhập camera': 'Camera username',
    'Mật khẩu camera': 'Camera password', 'Kết nối camera': 'Connect camera',
    'Tổng quan': 'Overview', 'ĐIỂM DANH VÀ BẮT ĐẦU QUÉT  →': 'TAKE ATTENDANCE AND START SCAN  →',
    '1. Chọn lớp học': '1. Select class', '2. Điểm danh và chọn học sinh': '2. Attendance and student selection',
    '3. Thời lượng quét': '3. Scan time limit', '4. Bắt đầu quét lớp học': '4. Start classroom scan',
    'Chỉ học sinh được chọn mới được đối chiếu khuôn mặt và ghi nhận dữ liệu trong buổi quét.': 'Only checked students will be included in face matching and session records.',
    'Chưa điểm danh cho buổi quét này.': 'Attendance has not been completed.',
    'Khi hết giờ, hệ thống sẽ BUỘC DỪNG quét, kể cả khi đang tạm dừng hoặc không ở màn hình giám sát.': 'When the time limit is reached, scanning is forcibly stopped, even if the session is paused or the monitor view is not active.',
    'Nguồn camera': 'Camera source', 'Camera tích hợp': 'Built-in camera', 'Camera USB': 'USB camera',
    'Camera Wi-Fi đã kết nối': 'Wi-Fi camera connected', 'Camera Wi-Fi • chưa kết nối': 'Wi-Fi camera • not connected',
    'Tài khoản camera': 'Camera account', 'Kết nối theo loại camera đã chọn trên Cổng Giáo viên.': 'Connect using the source selected in Teacher Portal.',
    'Camera tích hợp và camera USB kết nối trực tiếp. Camera Wi-Fi dùng tài khoản và mật khẩu bên dưới.': 'Built-in and USB cameras connect directly. Wi-Fi cameras use the account and password below.',
    'KẾT NỐI CAMERA': 'CONNECT CAMERA', 'Hình ảnh trực tiếp': 'Live preview',
    'Camera chưa kết nối.': 'Camera is not connected.', 'Kết nối camera để xem hình ảnh trực tiếp.': 'Connect the camera to see its live image.',
    'TÌM CAMERA': 'FIND CAMERA', 'HỦY': 'CANCEL', 'KẾT NỐI': 'CONNECT',
    'ĐIỂM DANH': 'ATTENDANCE', 'QUÉT ĐỘ TẬP TRUNG': 'FOCUS SCAN', 'LỊCH SỬ': 'HISTORY',
    'Giám sát trực tiếp': 'Live monitoring', 'Quan sát gần nhất': 'Latest observations',
    'SESSION TIME LIMIT': 'ĐÃ HẾT THỜI GIAN • ĐÃ BUỘC DỪNG',
    'ĐÃ HẾT THỜI GIAN • ĐÃ BUỘC DỪNG': 'SESSION TIME LIMIT',
    'MỨC ÂM THANH TRONG LỚP': 'CLASSROOM SOUND LEVEL',
    'Hiện tại:': 'Current:',
    'Trung bình buổi học:': 'Session average:',
    'Đang chờ microphone…': 'Waiting for microphone…',
    'Đang lấy mẫu âm thanh từ microphone…': 'Sampling microphone audio…',
    'Đang tích lũy…': 'Accumulating…',
    'Tính trung bình theo năng lượng trong thời gian giám sát; tạm dừng sẽ không cộng mẫu.': 'Energy-averaged over active monitoring time; paused time is excluded.',
    'NGUỒN CAMERA TỪ TEACHER PORTAL': 'CAMERA SOURCE FROM TEACHER PORTAL',
    'Để đổi loại camera, vào Teacher Portal và chọn Built-in, USB hoặc Wi-Fi/RTSP. Ở đây nhập thông tin kết nối cho nguồn đã chọn.': 'Choose Built-in, USB, or Wi-Fi/RTSP in Teacher Portal. Enter connection details for the selected source here.',
    'Chỉ số camera Built-in/USB': 'Built-in/USB camera index',
    'Tên đăng nhập camera': 'Camera username',
    'Mật khẩu camera': 'Camera password',
    'LƯU CẤU HÌNH': 'SAVE CONFIGURATION',
    'KẾT NỐI / KIỂM TRA CAMERA': 'CONNECT / TEST CAMERA',
    'ĐỒNG BỘ TEACHER PORTAL': 'SYNC TEACHER PORTAL',
    'Chưa kết nối camera.': 'Camera is not connected.',
    'XEM TRƯỚC CAMERA': 'CAMERA PREVIEW',
    'Nhấn “Kết nối / Kiểm tra Camera” để xem trước.': 'Click “Connect / Test Camera” to preview.',
    'Port hoặc chỉ số camera không hợp lệ.': 'The port or camera index is invalid.',
    'Đã lưu cấu hình và đồng bộ Teacher Portal.': 'Configuration saved and synced with Teacher Portal.',
    'Đã lưu trên máy; chưa đồng bộ được Server:': 'Saved locally; Server sync failed:',
    'Đã lưu trên máy này. Đăng nhập Server để đồng bộ với Teacher Portal.': 'Saved on this computer. Sign in to the Server to sync with Teacher Portal.',
    'Hiện tại: — dBFS': 'Current: — dBFS',
    'Trung bình buổi học: — dBFS': 'Session average: — dBFS',
})

# Complete phrases for the main workflow and runtime messages. Keeping these as
# whole phrases prevents mixed Vietnamese/English strings after Teacher Portal sync.
VI_TO_EN.update({
    'GIÁM SÁT LỚP HỌC THÔNG MINH': 'CLASSROOM INTELLIGENCE',
    'AI CỤC BỘ • NGOẠI TUYẾN': 'LOCAL AI • OFFLINE',
    'MÁY CHỦ • CHƯA KẾT NỐI': 'SERVER • DISCONNECTED',
    'MÁY CHỦ • ĐÃ KẾT NỐI': 'SERVER • CONNECTED',
    'Kết nối theo loại camera đã chọn trên Cổng Giáo viên.': 'Connect using the source selected in Teacher Portal.',
    'Đã chọn học sinh': 'Students selected',
    'Chưa điểm danh cho buổi quét này.': 'Attendance has not been completed.',
    'ĐIỂM DANH VÀ CHỌN HỌC SINH': 'MARK ATTENDANCE / SELECT STUDENTS',
    'BẮT ĐẦU QUÉT': 'START SCAN',
    'ĐIỂM DANH VÀ BẮT ĐẦU QUÉT  →': 'MARK ATTENDANCE AND START SCAN  →',
    '1  ĐIỂM DANH': '1  ATTENDANCE',
    '2  QUÉT ĐỘ TẬP TRUNG': '2  FOCUS SCAN',
    '3  LỊCH SỬ': '3  HISTORY',
    'SẴN SÀNG QUÉT': 'READY TO SCAN',
    'ĐANG CHỜ HÌNH ẢNH': 'WAITING FOR CAMERA IMAGE',
    'XÁC NHẬN DANH TÍNH  →': 'CONFIRM IDENTITY  →',
    'Xem danh sách đã chọn': 'View selected students',
    'Chọn ít nhất một học sinh có mặt.': 'Select at least one student who is present.',
    'Hãy chọn ít nhất một học sinh có mặt.': 'Select at least one student who is present.',
    'ĐÃ HỦY ĐIỂM DANH': 'ATTENDANCE CANCELLED',
    'CHƯA CHỌN LỚP HỌC': 'NO CLASS SELECTED',
    'CHƯA CHỌN HỌC SINH': 'NO STUDENTS SELECTED',
    'CHƯA CÓ HỌC SINH CÓ FACE ID ĐƯỢC CHỌN': 'NO SELECTED STUDENT HAS A FACE ID',
    'KHÔNG CÓ FACE ID READY TRONG NHÓM ĐƯỢC CHỌN': 'NO SELECTED STUDENT HAS A READY FACE ID',
    'ĐANG TẢI DANH SÁCH HỌC SINH…': 'LOADING STUDENT LIST…',
    'ĐANG KẾT NỐI CAMERA…': 'CONNECTING TO CAMERA…',
    'ĐÃ SẴN SÀNG • BẤM BẮT ĐẦU QUÉT FACE ID': 'READY • CLICK START FACE SCAN',
    'BẮT ĐẦU QUÉT FACE ID  →': 'START FACE SCAN  →',
    'ĐANG QUÉT FACE ID…': 'SCANNING FACE ID…',
    'AI ĐANG KHỞI ĐỘNG • ĐANG QUÉT FACE ID…': 'AI STARTING • SCANNING FACE ID…',
    'ĐANG GIÁM SÁT…': 'MONITORING…',
    'ĐANG KHỞI TẠO BUỔI HỌC…': 'STARTING SESSION…',
    'ĐANG CHỜ HÌNH ẢNH': 'WAITING FOR CAMERA IMAGE',
    'ĐÃ KẾT NỐI THÀNH CÔNG': 'CONNECTED SUCCESSFULLY',
    'Đã kết nối camera.': 'Camera connected.',
    'Đang kết nối camera…': 'Connecting to camera…',
    'Kết nối camera thất bại.': 'Camera connection failed.',
    'Đã chọn tất cả': 'All students selected',
    'Không có lớp học trên máy chủ.': 'No classes are available on the Server.',
    'Hãy tạo lớp học trên máy chủ trước.': 'Create a class on the Server first.',
    'Hãy chọn lớp học trước khi điểm danh.': 'Select a class before marking attendance.',
    'Mỗi buổi học là độc lập. Minh chứng được lưu cục bộ và đồng bộ lên máy chủ God Eyes.': 'Each session is independent. Evidence is stored locally and synced to the God Eyes Server.',
    'Face ID sẵn sàng': 'Face recognition ready',
    'Face ID chưa sẵn sàng': 'Face recognition not ready',
    'Chỉ học sinh được chọn mới được đối chiếu khuôn mặt và ghi nhận dữ liệu trong buổi quét.': 'Only selected students are included in face matching and session records.',
    'Khi hết giờ, hệ thống sẽ BUỘC DỪNG quét, kể cả khi đang tạm dừng hoặc không ở màn hình giám sát.': 'When time expires, scanning is forcibly stopped, even if paused or outside the monitor view.',
})

VI_TO_EN.update({
    'ĐANG CHỜ': 'WAITING', 'ĐANG CHỜ KẾT NỐI CAMERA…': 'WAITING FOR CAMERA CONNECTION…',
    'Đang kết nối máy chủ để tải danh sách lớp.': 'Connect to the Server to load classes.',
    'Đã điểm danh:': 'Attendance complete:', 'học sinh •': 'students •',
    'khuôn mặt sẵn sàng': 'face IDs ready', 'học sinh không được quét.': 'students excluded from scanning.',
    'ĐANG GIÁM SÁT': 'MONITORING', 'AI ĐANG CHẠY…': 'AI RUNNING…',
    'ĐANG KHỞI TẠO BUỔI HỌC…': 'STARTING SESSION…', 'ĐÃ SẴN SÀNG. ĐANG KHỞI ĐỘNG GIÁM SÁT…': 'READY. STARTING MONITOR…',
    'Đã kết nối •': 'Connected •', 'lớp học': 'classes',
    'Tạm dừng giám sát': 'Pause monitoring', 'Tiếp tục giám sát': 'Resume monitoring',
    'Đặt lại nhận diện': 'Reset recognition', 'Kết thúc buổi học': 'End session',
    'MỨC ÂM THANH TRONG LỚP': 'CLASSROOM SOUND LEVEL',
    'Hiện tại: — dBFS': 'Current: — dBFS',
    'Trung bình buổi học: — dBFS': 'Session average: — dBFS',
    'Đang chờ microphone…': 'Waiting for microphone…',
    'Trung bình buổi học: Đang tích lũy…': 'Session average: Accumulating…',
    'Camera chưa kết nối.': 'Camera is not connected.',
    'Kết nối camera để xem hình ảnh trực tiếp.': 'Connect the camera to see its live image.',
    'Kết nối camera': 'Connect camera',
    'SỬA ĐIỂM DANH': 'EDIT ATTENDANCE',
    'NHẬN DIỆN KHUÔN MẶT': 'FACE RECOGNITION',
    'nhận diện khuôn mặt': 'face recognition',
    'MÁY CHỦ': 'SERVER',
    'Máy chủ': 'Server',
    'CỔNG GIÁO VIÊN': 'TEACHER PORTAL',
    'Cổng Giáo viên': 'Teacher Portal',
})

# Exact labels used on the combined Overview/Camera screen.
VI_TO_EN.update({
    'Điểm danh và chọn học sinh': 'Mark attendance and select students',
    '2. Điểm danh và chọn học sinh': '2. Attendance and student selection',
    '4. Bắt đầu quét lớp học': '4. Start classroom scan',
    'Buổi quét đã buộc dừng vì hết thời gian.': 'The scan was forcibly stopped because its time limit expired.',
    'Hãy chọn lớp học trước.': 'Select a class first.',
    'Hãy kết nối máy chủ trước.': 'Connect to the Server first.',
    'Hãy kết thúc buổi hiện tại trước khi đổi điểm danh.': 'End the current session before changing attendance.',
    'Đã kết nối camera.': 'Camera connected.',
    'Đang kết nối camera…': 'Connecting to camera…',
    'Lỗi camera:': 'Camera error:',
})

EN_TO_VI = {v: k for k, v in VI_TO_EN.items() if v != k}
EN_TO_VI_EXTRA = {
    'God Eyes Room': 'God Eyes Lớp', 'Room': 'Lớp',
    'Camera Setup': 'Thiết lập camera', 'Board direction': 'Hướng bảng', 'Student Scan': 'Quét học sinh',
    'Live Monitor': 'Giám sát trực tiếp', 'Monitoring': 'Đang giám sát', 'MONITORING': 'ĐANG GIÁM SÁT',
    'End Session': 'Kết thúc buổi học', 'Reset ID': 'Đặt lại danh tính', '0 students': '0 học sinh', '0 events': '0 sự kiện',
    'Latest observations': 'Quan sát gần nhất', 'Every session is independent. Evidence is stored locally and synced to God Eyes Server.': 'Mỗi buổi học là độc lập. Minh chứng được lưu cục bộ và đồng bộ lên God Eyes Server.',
    'Server URL': 'Địa chỉ Server', 'Teacher account': 'Tài khoản giáo viên', 'CONNECTED': 'ĐÃ KẾT NỐI', 'DISCONNECTED': 'CHƯA KẾT NỐI',
    'SERVER • CONNECTED': 'SERVER • ĐÃ KẾT NỐI', 'SERVER NOT CONNECTED': 'SERVER • CHƯA KẾT NỐI',
    'AI READY': 'AI SẴN SÀNG', 'AI ONLINE': 'AI ĐANG HOẠT ĐỘNG', 'PAUSED SESSION': 'BUỔI HỌC ĐÃ TẠM DỪNG', 'ENDED EARLY': 'ĐÃ KẾT THÚC SỚM', 'SESSION COMPLETE': 'BUỔI HỌC HOÀN TẤT',
    'CAMERA PREVIEW': 'XEM TRƯỚC CAMERA', 'CAMERA NOT READY': 'CAMERA CHƯA SẴN SÀNG', 'CONNECTING…': 'ĐANG KẾT NỐI…',
    'SCANNING •': 'ĐANG QUÉT •', 'IDENTITY UNCERTAIN': 'CHƯA XÁC ĐỊNH DANH TÍNH', 'SERVER SYNC •': 'ĐỒNG BỘ SERVER •',
    'Delete Session': 'Xóa buổi học', 'Delete All': 'Xóa tất cả', 'View Session & Evidence': 'Xem buổi học & minh chứng',
    'Date': 'Ngày', 'Duration': 'Thời lượng', 'Status': 'Trạng thái', 'Students': 'Học sinh', 'Events': 'Sự kiện', 'Session': 'Buổi học',
    'Pause': 'Tạm dừng', 'Resume': 'Tiếp tục', 'Exit': 'Thoát', 'God Eyes Desktop': 'Ứng dụng God Eyes Desktop',
    'SELECT AN OBSERVATION': 'CHỌN MỘT QUAN SÁT', 'Evidence viewer': 'Trình xem minh chứng',
    'Start Session': 'Bắt đầu buổi học', 'Start Scan': 'Bắt đầu quét', 'Select class before starting a session': 'Chọn lớp trước khi bắt đầu buổi học',
    'Face ID matching': 'đối chiếu Face ID', 'Start Session': 'Bắt đầu buổi học', 'Server settings synced.': 'Cài đặt Server đã đồng bộ.',
    'Saved to your Teacher account.': 'Đã lưu vào tài khoản giáo viên.', 'Unable to sync settings •': 'Không thể đồng bộ cài đặt •',
    'Unable to save settings •': 'Không thể lưu cài đặt •', 'Checking account and connecting to the Server…': 'Đang kiểm tra tài khoản và kết nối Server…',
}
EN_TO_VI.update(EN_TO_VI_EXTRA)
EN_TO_VI.update({
    'Face recognition': 'nhận diện khuôn mặt',
    'Face ID': 'nhận diện khuôn mặt',
    'FACE ID': 'NHẬN DIỆN KHUÔN MẶT',
    'SERVER': 'MÁY CHỦ',
    'TEACHER PORTAL': 'CỔNG GIÁO VIÊN',
    'EDIT ATTENDANCE': 'SỬA ĐIỂM DANH',
    'START FACE SCAN  →': 'BẮT ĐẦU QUÉT KHUÔN MẶT  →',
})
EN_TO_VI.update({
    'Attendance is selected once at the start. GodEyes scans people visible to the camera and matches only against the selected group.': 'Danh sách điểm danh chỉ chọn một lần ở đầu buổi. GodEyes sẽ quét mọi người xuất hiện trong camera và chỉ đối chiếu với nhóm đã chọn.',
    'Select at least one student who is present.': 'Hãy chọn ít nhất một học sinh có mặt.',
    'This class has no students on the Server yet.': 'Lớp này chưa có học sinh trên Server.',
    'ATTENDANCE CANCELLED': 'ĐÃ HỦY ĐIỂM DANH', 'LOADING STUDENT LIST…': 'ĐANG TẢI DANH SÁCH HỌC SINH…',
    'CONNECTING TO CAMERA…': 'ĐANG KẾT NỐI CAMERA…', 'Ready. Click Start to launch AI and scan faces.': 'Đã sẵn sàng. Bấm BẮT ĐẦU để khởi động AI và quét khuôn mặt.',
    'READY • CLICK START FACE SCAN': 'ĐÃ SẴN SÀNG • BẤM BẮT ĐẦU QUÉT FACE ID',
    'SCANNING FACES…': 'ĐANG QUÉT KHUÔN MẶT…', 'STARTING SESSION…': 'ĐANG KHỞI ĐỘNG BUỔI HỌC…',
    'AI RUNNING…': 'AI ĐANG CHẠY…', 'MONITORING…': 'ĐANG GIÁM SÁT…',
    'Teacher workflow: select a class → attendance → face scan → live monitoring.': 'Quy trình dành cho giáo viên: chọn lớp → điểm danh → quét Face ID → giám sát trực tiếp.',
    'Choose a class, mark attendance and set a scan time limit.': 'Chọn lớp, điểm danh học sinh và đặt thời gian quét tối đa.',
    'Connect to the Server to load classes.': 'Kết nối máy chủ để tải danh sách lớp.',
    'minutes (1–180)': 'phút (1–180)', '15 minutes': '15 phút', '30 minutes': '30 phút',
    '45 minutes': '45 phút', '60 minutes': '60 phút', 'Custom': 'Tùy chỉnh',
    'Camera username': 'Tên đăng nhập camera', 'Camera password': 'Mật khẩu camera',
    'Overview': 'Tổng quan',
    '1. Select class': '1. Chọn lớp học',
    '2. Attendance and student selection': '2. Điểm danh và chọn học sinh',
    '3. Scan time limit': '3. Thời lượng quét',
    '4. Start classroom scan': '4. Bắt đầu quét lớp học',
    'TAKE ATTENDANCE AND START SCAN  →': 'ĐIỂM DANH VÀ BẮT ĐẦU QUÉT  →',
    'Only checked students will be included in face matching and session records.': 'Chỉ học sinh được chọn mới được đối chiếu khuôn mặt và ghi nhận dữ liệu trong buổi quét.',
    'Attendance has not been completed.': 'Chưa điểm danh cho buổi quét này.',
    'When the time limit is reached, scanning is forcibly stopped, even if the session is paused or the monitor view is not active.': 'Khi hết giờ, hệ thống sẽ BUỘC DỪNG quét, kể cả khi đang tạm dừng hoặc không ở màn hình giám sát.',
    'Camera source': 'Nguồn camera', 'Built-in camera': 'Camera tích hợp', 'USB camera': 'Camera USB',
    'Wi-Fi camera connected': 'Camera Wi-Fi đã kết nối', 'Wi-Fi camera • not connected': 'Camera Wi-Fi • chưa kết nối',
    'Camera account': 'Tài khoản camera',
    'Connect using the source selected in Teacher Portal.': 'Kết nối theo loại camera đã chọn trên Cổng Giáo viên.',
    'Built-in and USB cameras connect directly. Wi-Fi cameras use the account and password below.': 'Camera tích hợp và camera USB kết nối trực tiếp. Camera Wi-Fi dùng tài khoản và mật khẩu bên dưới.',
    'CONNECT CAMERA': 'KẾT NỐI CAMERA', 'Live preview': 'Hình ảnh trực tiếp',
    'Camera is not connected.': 'Camera chưa kết nối.',
    'Connect the camera to see its live image.': 'Kết nối camera để xem hình ảnh trực tiếp.',
    'FIND CAMERA': 'TÌM CAMERA', 'CANCEL': 'HỦY', 'CONNECT': 'KẾT NỐI',
    'ATTENDANCE': 'ĐIỂM DANH', 'FOCUS SCAN': 'QUÉT ĐỘ TẬP TRUNG', 'HISTORY': 'LỊCH SỬ',
    'Live monitoring': 'Giám sát trực tiếp',
    'SESSION TIME LIMIT': 'ĐÃ HẾT THỜI GIAN • ĐÃ BUỘC DỪNG',
    'SESSION STATUS': 'TRẠNG THÁI BUỔI HỌC',
    'CLASSROOM': 'LỚP HỌC', 'CAMERA': 'CAMERA',
    'LOCAL AI • OFFLINE': 'AI CỤC BỘ • NGOẠI TUYẾN',
})
EN_TO_VI.update({
    'Sessions': 'Buổi học', 'Observations': 'Quan sát', 'System': 'Hệ thống',
    'CONNECT': 'KẾT NỐI', 'READY': 'SẴN SÀNG', 'Start': 'Bắt đầu',
    'START': 'BẮT ĐẦU', 'SCAN': 'QUÉT', 'Student': 'Học sinh',
    'Status': 'Trạng thái', 'Match': 'Khớp', 'Latest observations': 'Quan sát gần nhất',
    'Server': 'Máy chủ', 'Teacher account': 'Tài khoản giáo viên',
    'Settings': 'Cài đặt', 'Dashboard': 'Tổng quan', 'History': 'Lịch sử',
    'Classes': 'Lớp học', 'Delete': 'Xóa', 'Delete All': 'Xóa tất cả',
    'View Session & Evidence': 'Xem buổi học & minh chứng', 'Exit': 'Thoát',
})

# Prefer whole-phrase translations over fragments to keep the UI single-language.
EN_TO_VI.update({
    'Mark attendance and select students': 'Điểm danh và chọn học sinh',
    'The scan was forcibly stopped because its time limit expired.': 'Buổi quét đã buộc dừng vì hết thời gian.',
    'Select a class first.': 'Hãy chọn lớp học trước.',
    'Connect to the Server first.': 'Hãy kết nối máy chủ trước.',
    'End the current session before changing attendance.': 'Hãy kết thúc buổi hiện tại trước khi đổi điểm danh.',
    'Camera connected.': 'Đã kết nối camera.',
    'Connecting to camera…': 'Đang kết nối camera…',
    'Camera error:': 'Lỗi camera:',
})

def _translate_text(value: str, language: str) -> str:
    text = str(value or '')
    mapping = VI_TO_EN if language == 'en' else EN_TO_VI
    if not text.strip():
        return text
    for src, dst in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if src and src in text:
            text = text.replace(src, dst)
    return text


def _translate_runtime_widget_texts(root, language: str):
    """Translate every visible Qt text-bearing widget, including late status updates."""
    try:
        from PySide6.QtWidgets import QAbstractButton
    except Exception:
        QAbstractButton = ()
    for obj in root.findChildren(QWidget):
        try:
            if isinstance(obj, QAbstractButton):
                obj.setText(_translate_text(obj.text(), language))
            elif isinstance(obj, QLabel):
                obj.setText(_translate_text(obj.text(), language))
            elif isinstance(obj, QLineEdit):
                obj.setPlaceholderText(_translate_text(obj.placeholderText(), language))
            elif isinstance(obj, QComboBox):
                for i in range(obj.count()):
                    obj.setItemText(i, _translate_text(obj.itemText(i), language))
            elif isinstance(obj, QTableWidget):
                for i in range(obj.columnCount()):
                    item=obj.horizontalHeaderItem(i)
                    if item: item.setText(_translate_text(item.text(), language))
                for r in range(obj.rowCount()):
                    for c in range(obj.columnCount()):
                        item=obj.item(r,c)
                        if item: item.setText(_translate_text(item.text(), language))
            elif isinstance(obj, QListWidget):
                for i in range(obj.count()):
                    item=obj.item(i)
                    if item: item.setText(_translate_text(item.text(), language))
        except Exception:
            pass


# [AI-GENERATED / AI-ASSISTED CODE] Camera-frame conversion for Qt display.
def pixmap(frame, size, fill=False):
    """Convert a BGR camera frame to Qt pixmap.

    fill=False preserves the complete image (useful for photos/evidence).
    fill=True fills the entire camera preview and center-crops the excess so
    the preview panel does not show large dark letterbox bands.
    """
    if frame is None or getattr(frame, 'size', 0) == 0:
        return QPixmap()
    try:
        h, w = frame.shape[:2]
        image = QImage(frame.data, int(w), int(h), int(frame.strides[0]), QImage.Format_BGR888).copy()
        target_w = max(1, int(size.width()))
        target_h = max(1, int(size.height()))
        source_pixmap = QPixmap.fromImage(image)
        if int(w) == target_w and int(h) == target_h:
            return source_pixmap
        if fill:
            scaled = source_pixmap.scaled(
                target_w, target_h, Qt.KeepAspectRatioByExpanding, Qt.FastTransformation
            )
            x = max(0, (scaled.width() - target_w) // 2)
            y = max(0, (scaled.height() - target_h) // 2)
            return scaled.copy(x, y, min(target_w, scaled.width()), min(target_h, scaled.height()))
        return source_pixmap.scaled(
            target_w, target_h, Qt.KeepAspectRatio, Qt.FastTransformation
        )
    except Exception:
        return QPixmap()


# Student portrait support: prefer a photo returned by the Server/Teacher Portal;
# never attempt to reconstruct a portrait from the numeric Face ID embedding.
_STUDENT_IMAGE_KEYS = (
    'photo_url', 'avatar_url', 'image_url', 'face_image_url', 'face_photo_url',
    'thumbnail_url', 'profile_image_url', 'student_image_url', 'student_photo_url',
    'photo', 'avatar', 'image', 'face_image', 'face_photo', 'face_thumbnail',
    'portrait', 'profile_image', 'profile_picture', 'student_photo', 'student_image',
    'thumbnail', 'photo_data', 'image_data', 'portrait_data', 'photo_base64',
    'avatar_base64', 'image_base64', 'face_image_base64', 'face_photo_base64',
    'thumbnail_base64', 'profile_image_base64',
)
_STUDENT_IMAGE_NESTED_KEYS = ('profile', 'student', 'media', 'images', 'face', 'face_data', 'profile_data', 'photo_data')
_MAX_STUDENT_IMAGE_BYTES = 4 * 1024 * 1024


def _student_image_source(record, depth=0):
    """Return ('bytes', image_bytes), ('url', url), or (None, None) from known image fields."""
    if not isinstance(record, dict) or depth > 2:
        return None, None

    def convert(value, field_name):
        if isinstance(value, bytes):
            return ('bytes', value) if 0 < len(value) <= _MAX_STUDENT_IMAGE_BYTES else (None, None)
        if isinstance(value, bytearray):
            data = bytes(value)
            return ('bytes', data) if 0 < len(data) <= _MAX_STUDENT_IMAGE_BYTES else (None, None)
        if isinstance(value, (list, tuple)) and value and len(value) <= _MAX_STUDENT_IMAGE_BYTES:
            try:
                data = bytes(int(x) for x in value)
                return ('bytes', data) if data else (None, None)
            except Exception:
                return None, None
        if isinstance(value, dict):
            for inner_key in ('data_url', 'data_uri', 'base64', 'data', 'content', 'url', 'href', 'path', 'file_path'):
                if inner_key in value:
                    kind, payload = convert(value.get(inner_key), f'{field_name}_{inner_key}')
                    if kind:
                        return kind, payload
            return None, None
        if not isinstance(value, str):
            return None, None

        text = value.strip()
        if not text:
            return None, None
        if text.startswith('data:image/') and ',' in text:
            try:
                data = base64.b64decode(text.split(',', 1)[1], validate=False)
                if 0 < len(data) <= _MAX_STUDENT_IMAGE_BYTES:
                    return 'bytes', data
            except Exception:
                return None, None
        parsed = urlparse(text)
        if parsed.scheme.lower() in ('http', 'https') and parsed.netloc:
            return 'url', text
        # Some portal versions provide a relative endpoint instead of an absolute URL.
        if ('url' in field_name.lower() or 'href' in field_name.lower()) and (text.startswith('/') or text.startswith('./') or text.startswith('../')):
            return 'url', text

        # Try existing local files before interpreting a field as base64.
        try:
            candidates = [Path(text), APP_DIR / text, APP_DIR / 'assets' / text]
            for candidate in candidates:
                if candidate.is_file() and candidate.stat().st_size <= _MAX_STUDENT_IMAGE_BYTES:
                    return 'bytes', candidate.read_bytes()
        except Exception:
            pass

        # Base64 is accepted only for image-related fields and only when it has enough data
        # to plausibly be an encoded image. A face_embedding is deliberately not an image field.
        lname = field_name.lower()
        if ('base64' in lname or 'photo' in lname or 'image' in lname or 'avatar' in lname or 'thumbnail' in lname) and len(text) >= 100:
            compact = ''.join(text.split())
            try:
                data = base64.b64decode(compact, validate=False)
                if 0 < len(data) <= _MAX_STUDENT_IMAGE_BYTES:
                    return 'bytes', data
            except Exception:
                pass
        return None, None

    for key in _STUDENT_IMAGE_KEYS:
        if key in record:
            kind, payload = convert(record.get(key), key)
            if kind:
                return kind, payload
    for key in _STUDENT_IMAGE_NESTED_KEYS:
        nested = record.get(key)
        if isinstance(nested, dict):
            kind, payload = _student_image_source(nested, depth + 1)
            if kind:
                return kind, payload
        elif isinstance(nested, (list, tuple)):
            for entry in nested[:4]:
                if isinstance(entry, dict):
                    kind, payload = _student_image_source(entry, depth + 1)
                    if kind:
                        return kind, payload
    return None, None


def _avatar_pixmap_from_bytes(data, size):
    """Decode a supported image and center-crop it to a square thumbnail."""
    if not data:
        return QPixmap()
    image = QPixmap()
    if not image.loadFromData(bytes(data)):
        return QPixmap()
    size = max(16, int(size or 64))
    scaled = image.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    return scaled.copy(x, y, size, size)


def _generic_student_avatar(size=68):
    """Neutral illustrated avatar used only when the Server supplies no portrait."""
    size = max(16, int(size or 68))
    image = QPixmap(size, size)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#eaf2ff'))
    painter.drawRoundedRect(0, 0, size - 1, size - 1, max(7, size * .16), max(7, size * .16))
    painter.setBrush(QColor('#91b9ef'))
    painter.drawEllipse(int(size * .35), int(size * .16), int(size * .30), int(size * .34))
    painter.setBrush(QColor('#3d8be8'))
    painter.drawRoundedRect(int(size * .17), int(size * .55), int(size * .66), int(size * .37), max(6, size * .22), max(6, size * .22))
    painter.end()
    return image


def _download_student_image(url, access_token='', trusted_base_url=''):
    """Fetch a student photo asynchronously; send bearer auth only to the configured Server origin."""
    try:
        url_text = str(url or '').strip()
        parsed = urlparse(url_text)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.netloc:
            return b''
        headers = {'User-Agent': 'GodEyes/1.0', 'Accept': 'image/*'}
        base = urlparse(str(trusted_base_url or '').strip())
        same_origin = (
            base.scheme.lower() == parsed.scheme.lower()
            and (base.hostname or '').lower() == (parsed.hostname or '').lower()
            and (base.port or (443 if base.scheme.lower() == 'https' else 80))
                == (parsed.port or (443 if parsed.scheme.lower() == 'https' else 80))
        )
        if access_token and same_origin:
            headers['Authorization'] = 'Bearer ' + str(access_token)
        request = urllib.request.Request(url_text, headers=headers)
        with urllib.request.urlopen(request, timeout=3.5) as response:
            content_type = str(response.headers.get('Content-Type', '')).split(';', 1)[0].strip().lower()
            if content_type and not content_type.startswith('image/'):
                return b''
            data = response.read(_MAX_STUDENT_IMAGE_BYTES + 1)
            if len(data) > _MAX_STUDENT_IMAGE_BYTES:
                return b''
            return data
    except Exception:
        return b''


# Missing in v16: the Settings Wi-Fi camera button referenced this class.
# Keep discovery/connect behavior identical to the existing camera workflow.
class CameraConnectionDialog(QDialog):
    def __init__(self, parent, brand='tapo', username='', password='', stream='stream1', language='vi'):
        super().__init__(parent)
        self.language = str(language or 'vi').lower()
        self.brand = 'tapo'
        self._username = str(username or '')
        self._password = str(password or '')
        self._stream = str(stream or 'stream1') if str(stream or 'stream1') in ('stream1','stream2') else 'stream1'
        self._found = []
        self._selected = None
        self.setWindowTitle('Connect Wi-Fi Camera' if self.language == 'en' else 'Kết nối Camera Wi-Fi')
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(DARK_STYLE if parent and getattr(parent, 'ui_theme', 'light') == 'dark' else STYLE)
        self._build()

    def _t(self, en, vi): return en if self.language == 'en' else vi

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(12)
        title = QLabel(self._t('Connect camera', 'Kết nối camera'))
        title.setObjectName('title')
        root.addWidget(title)
        sub = QLabel(self._t(
            'Enter the camera username and password. GodEyes will search for a compatible camera on the local network.',
            'Nhập tài khoản và mật khẩu camera. GodEyes sẽ tự tìm camera tương thích trong mạng nội bộ.'
        ))
        sub.setObjectName('muted')
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addWidget(QLabel(self._t('Username', 'Tài khoản camera')))
        self.user_edit = QLineEdit(self._username)
        self.user_edit.setPlaceholderText(self._t('Camera username', 'Nhập tài khoản camera'))
        root.addWidget(self.user_edit)
        root.addWidget(QLabel(self._t('Password', 'Mật khẩu camera')))
        self.pass_edit = QLineEdit(self._password)
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setPlaceholderText(self._t('Camera password', 'Nhập mật khẩu camera'))
        root.addWidget(self.pass_edit)
        self.info = QLabel(self._t(
            'Select Find Camera to search for a compatible camera.',
            'Bấm Tìm camera để tìm camera tương thích.'
        ))
        self.info.setObjectName('muted')
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.results = QListWidget()
        self.results.setMinimumHeight(130)
        self.results.currentRowChanged.connect(self._select)
        root.addWidget(self.results)
        row = QHBoxLayout()
        self.find_btn = QPushButton(self._t('FIND CAMERA', 'TÌM CAMERA'))
        self.find_btn.setObjectName('primary')
        self.find_btn.clicked.connect(self._discover)
        self.connect_btn = QPushButton(self._t('CONNECT', 'KẾT NỐI'))
        self.connect_btn.setObjectName('primary')
        self.connect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._connect)
        cancel = QPushButton(self._t('CANCEL', 'HỦY'))
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self.find_btn)
        row.addWidget(self.connect_btn)
        row.addWidget(cancel)
        root.addLayout(row)

    def _discover(self):
        self._found=[]; self._selected=None; self.results.clear(); self.connect_btn.setEnabled(False); self.find_btn.setEnabled(False)
        self.info.setText(self._t('Searching the local network…','Đang tìm camera trong mạng nội bộ…'))
        QApplication.processEvents()
        try:
            found=discover_onvif_cameras(timeout=2.5,max_results=10)
        except Exception as exc:
            found=[]
            self.info.setText(self._t(f'Discovery failed: {exc}','Không thể tìm camera: {exc}'))
        self._found=list(found or [])
        if not self._found:
            self.info.setText(self._t('No compatible camera found. Make sure the camera and this PC are on the same network.','Không tìm thấy camera tương thích. Hãy chắc chắn camera và máy tính đang dùng cùng mạng.'))
            self.find_btn.setEnabled(True); return
        for i,_item in enumerate(self._found,1):
            self.results.addItem(QListWidgetItem(self._t(f'Wi-Fi Camera {i}','Camera Wi-Fi ' + str(i))))
        self.results.setCurrentRow(0); self._selected=0
        self.connect_btn.setEnabled(True); self.find_btn.setEnabled(True)
        self.info.setText(self._t(f'{len(self._found)} compatible camera(s) found.','Đã tìm thấy ' + str(len(self._found)) + ' camera tương thích.'))

    def _select(self,row):
        if 0 <= int(row) < len(self._found): self._selected=int(row)

    def _connect(self):
        if self._selected is None or not self._found: return
        user = self.user_edit.text().strip()
        password = self.pass_edit.text()
        if not user or not password:
            QMessageBox.warning(                self,
                self._t('Camera account', 'Tài khoản camera'),
                self._t('Enter the camera username and password.', 'Nhập tài khoản và mật khẩu camera.')
            )
            return
        self._username = user
        self._password = password
        self._stream = 'stream1'
        self.accept()

    def connection_info(self):
        item=self._found[int(self._selected)]
        return {'brand': 'tapo', 'host': str(item['ip']), 'port': 554, 'stream': 'stream1', 'username': self._username, 'password': self._password}


# [RESTORED] Attendance selection before each classroom scan.
class AttendanceSelectionDialog(QDialog):
    """Choose who is present for this session; unchecked students are excluded from AI matching."""
    def __init__(self, parent, roster, selected_ids=None):
        super().__init__(parent)
        self.roster=list(roster or [])
        self.preselected_ids = None if selected_ids is None else {int(x) for x in selected_ids}
        self._rows=[]
        self.language = _normalize_ui_language(getattr(parent, 'ui_language', 'vi'))
        self.setWindowTitle('God Eyes • Attendance' if self.language == 'en' else 'God Eyes • Điểm danh buổi học')
        self.setModal(True)
        self.resize(980,800)
        self.setMinimumSize(860,700)
        self.setStyleSheet(DARK_STYLE if getattr(parent,'ui_theme','light')=='dark' else STYLE)
        self._build()

    def _t(self, en, vi):
        return en if self.language == 'en' else vi

    def _build(self):
        outer=QVBoxLayout(self); outer.setContentsMargins(20,20,20,20); outer.setSpacing(12)
        card=QFrame(); card.setObjectName('dialogCard'); root=QVBoxLayout(card); root.setContentsMargins(24,22,24,22); root.setSpacing(12)
        header=QHBoxLayout(); icon=QLabel(); icon.setFixedSize(54,54); icon.setAlignment(Qt.AlignCenter)
        if LOGO_PATH.exists():
            pix=_godeyes_logo_pixmap(256)
            if not pix.isNull(): icon.setPixmap(pix.scaled(54,54,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        header.addWidget(icon); hv=QVBoxLayout()
        title=QLabel(self._t('Attendance', 'Điểm danh buổi học')); title.setObjectName('title'); title.setStyleSheet('font-size:24px;'); hv.addWidget(title)
        sub=QLabel(self._t('Select the students who are present today. Unchecked students will be excluded from face matching for this session.', 'Đánh dấu học sinh có mặt hôm nay. Người bỏ chọn sẽ không được đưa vào đối chiếu khuôn mặt của buổi học này.'))
        sub.setObjectName('muted'); sub.setWordWrap(True); hv.addWidget(sub); header.addLayout(hv,1); root.addLayout(header)
        info=QFrame(); info.setObjectName('card'); il=QHBoxLayout(info); il.setContentsMargins(14,10,14,10)
        self.summary=QLabel(self._t('Loading student list…', 'Đang tải danh sách…')); self.summary.setObjectName('section'); il.addWidget(self.summary,1); root.addWidget(info)
        tools=QHBoxLayout(); tools.setSpacing(8)
        self.search=QLineEdit(); self.search.setPlaceholderText(self._t('Search by student name or ID…', 'Tìm theo tên hoặc mã học sinh…')); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._filter); tools.addWidget(self.search,1)
        b=QPushButton(self._t('☑  SELECT ALL', '☑  CHỌN TẤT CẢ')); b.clicked.connect(self._select_all); tools.addWidget(b)
        b=QPushButton(self._t('☐  CLEAR ALL', '☐  BỎ CHỌN TẤT CẢ')); b.clicked.connect(self._clear_all); tools.addWidget(b); root.addLayout(tools)
        self.list=QListWidget(); self.list.setSpacing(3); root.addWidget(self.list,1)
        self.ok=QPushButton(self._t('CONFIRM ATTENDANCE  →', 'XÁC NHẬN ĐIỂM DANH  →')); self.ok.setObjectName('primary'); self.ok.setMinimumWidth(230); self.ok.clicked.connect(self._accept)
        self._populate()
        footer=QHBoxLayout(); hint=QLabel(self._t('Example: if 44 of 45 students are present, uncheck the absent student. AI will only scan the 44 selected students.', 'Ví dụ: lớp có 45 học sinh nhưng hôm nay 44 có mặt thì bỏ chọn 1 học sinh. AI sẽ chỉ quét 44 học sinh được chọn.'))
        hint.setObjectName('muted'); hint.setWordWrap(True); footer.addWidget(hint,1)
        cancel=QPushButton(self._t('CANCEL', 'HỦY')); cancel.clicked.connect(self.reject); footer.addWidget(cancel); footer.addWidget(self.ok); root.addLayout(footer); outer.addWidget(card)

    def _populate(self):
        self.list.clear(); self._rows=[]
        for item in self.roster:
            sid=int(item.get('id') or 0); code=str(item.get('student_code') or '').strip(); name=str(item.get('full_name') or '').strip(); ready=bool(item.get('ready'))
            widget=QFrame(); widget.setObjectName('card'); lay=QHBoxLayout(widget); lay.setContentsMargins(12,10,12,10); lay.setSpacing(10)
            cb=QCheckBox(); cb.setChecked(True if self.preselected_ids is None else sid in self.preselected_ids); cb.stateChanged.connect(self._refresh_summary); lay.addWidget(cb)
            avatar=QLabel(); avatar.setAlignment(Qt.AlignCenter); avatar.setFixedSize(44,44); avatar.setObjectName('student_avatar')
            avatar.setStyleSheet('background:transparent;border:1px solid #d3e5f8;border-radius:8px;padding:0;')
            owner=self.parent()
            if owner is not None and hasattr(owner, '_set_student_avatar'):
                owner._set_student_avatar(avatar, item, sid)
            else:
                avatar.setPixmap(_generic_student_avatar(44))
            lay.addWidget(avatar)
            names=QVBoxLayout(); primary=QLabel(f'{code} • {name}' if code and name else (name or code or (f'STUDENT {sid}' if self.language == 'en' else f'HỌC SINH {sid}'))); primary.setObjectName('section'); primary.setStyleSheet('font-size:14px;'); names.addWidget(primary)
            secondary=QLabel(self._t('Face recognition ready' if ready else 'Face recognition not ready', 'Nhận diện khuôn mặt đã sẵn sàng' if ready else 'Chưa có dữ liệu nhận diện khuôn mặt')); secondary.setObjectName('status_success' if ready else 'status_warning'); names.addWidget(secondary); lay.addLayout(names,1)
            self.list.addItem(''); row=self.list.count()-1; self.list.setItemWidget(self.list.item(row),widget); self.list.item(row).setSizeHint(widget.sizeHint())
            self._rows.append({'sid':sid,'code':code,'name':name,'ready':ready,'item':self.list.item(row),'checkbox':cb})
        self._refresh_summary()

    def _filter(self,value):
        q=str(value or '').strip().lower()
        for row in self._rows:
            row['item'].setHidden(bool(q) and q not in row['name'].lower() and q not in row['code'].lower())
    def _selected_rows(self): return [r for r in self._rows if r['checkbox'].isChecked()]
    def _select_all(self):
        for r in self._rows: r['checkbox'].setChecked(True)
    def _clear_all(self):
        for r in self._rows: r['checkbox'].setChecked(False)
    def _refresh_summary(self):
        selected=self._selected_rows(); ready=sum(1 for r in selected if r['ready']); absent=len(self.roster)-len(selected)
        self.summary.setText((f'Class size: {len(self.roster)}   •   Present: {len(selected)}   •   Absent/excluded: {absent}   •   Face ID ready: {ready}' if self.language == 'en' else f'Sĩ số lớp: {len(self.roster)}   •   Có mặt: {len(selected)}   •   Vắng/loại trừ: {absent}   •   Nhận diện khuôn mặt sẵn sàng: {ready}'))
        self.ok.setEnabled(bool(selected) and bool(ready))
    def selected_ids(self): return {r['sid'] for r in self._selected_rows()}
    def _accept(self):
        selected=self._selected_rows(); ready=[r for r in selected if r['ready']]
        if not selected:
            QMessageBox.information(self,'God Eyes',self._t('Select at least one student who is present.', 'Hãy chọn ít nhất một học sinh có mặt.')); return
        if not ready:
            QMessageBox.warning(self,'God Eyes',self._t('The selected students do not have Face ID yet. Enroll their Face ID on the Server before scanning.', 'Các học sinh được chọn chưa có Face ID. Hãy tạo Face ID trên Server trước khi quét.')); return
        self.accept()


class ClickableStudentCard(QFrame):
    """Card surface that toggles its student unless the checkbox itself was clicked."""
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class StudentSelectionCheckBox(QCheckBox):
    """Compact selection control: an empty outline, or a blue tick without a blue block."""
    def paintEvent(self, event):
        if self.text():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        side = min(18, max(14, self.height() - 2))
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        if self.isChecked():
            # A tick only—do not fill the whole checkbox blue.
            pen_color = QColor('#087bfa' if self.isEnabled() else '#8ba8ca')
            painter.setPen(QPen(pen_color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(x + 3, y + 9, x + 7, y + 13)
            painter.drawLine(x + 7, y + 13, x + 15, y + 4)
        else:
            painter.setPen(QPen(QColor('#91a8c6' if self.isEnabled() else '#c5cfdd'), 1.3))
            painter.setBrush(QColor('#ffffff' if self.isEnabled() else '#f2f5f9'))
            painter.drawRoundedRect(x + 1, y + 1, side - 2, side - 2, 4, 4)
        painter.end()


class AudioLevelWorker(QThread):
    """Measure RMS power from RTSP camera audio or a local input device."""
    level_power = Signal(float)
    audio_error = Signal(str)

    def __init__(self, parent=None, rtsp_url=""):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self.rtsp_url = str(rtsp_url or "").strip()
        self._process = None
        self._process_lock = threading.Lock()

    def stop(self):
        self._stop_event.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    @staticmethod
    def _find_ffmpeg():
        candidates = []
        executable = shutil.which("ffmpeg")
        if executable:
            candidates.append(Path(executable))

        # Portable FFmpeg may be placed next to the application.
        candidates.extend([
            APP_DIR / "ffmpeg.exe",
            APP_DIR.parent / "ffmpeg.exe",
            Path(sys.executable).resolve().parent / "ffmpeg.exe",
            Path(sys.executable).resolve().parent / "_internal" / "ffmpeg.exe",
        ])

        # PyInstaller one-file build: imageio-ffmpeg's executable is extracted
        # alongside the package into the temporary bundle directory.
        try:
            import imageio_ffmpeg
            bundled = imageio_ffmpeg.get_ffmpeg_exe()
            if bundled:
                candidates.append(Path(bundled))
        except Exception:
            pass

        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except Exception:
                continue
        return None

    @staticmethod
    def _ffmpeg_error_text(error_file):
        try:
            error_file.flush()
            error_file.seek(0)
            details = error_file.read().decode("utf-8", errors="replace")[-2400:].strip()
            # Avoid exposing RTSP credentials in a UI error string.
            details = re.sub(r"(rtsps?://)[^/@\s]+@", r"\1***@", details, flags=re.I)
            return details
        except Exception:
            return ""

    def _read_rtsp_audio(self):
        if not self.rtsp_url:
            raise RuntimeError(
                "Thiếu URL RTSP hoặc thông tin đăng nhập camera. Hãy lưu IP, port, stream, username và password trong mục Camera."
            )

        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "Không tìm thấy FFmpeg trong gói ứng dụng. Hãy build lại bằng script build_godeyes.ps1 có imageio-ffmpeg."
            )

        # Map an actual audio track. If the RTSP stream is video-only, FFmpeg's
        # diagnostic is returned to the UI rather than leaving the level blank.
        # Do not pass -rw_timeout here: some FFmpeg binaries bundled by
        # imageio-ffmpeg (including older/static builds) do not expose that
        # option and exit with "Option rw_timeout not found" before opening RTSP.
        # The worker below enforces a 15-second application-level timeout and
        # terminates FFmpeg if the stream never delivers samples.
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url, "-map", "0:a:0", "-vn",
            "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1",
        ]

        with tempfile.TemporaryFile() as error_file:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_file,
                bufsize=0,
                creationflags=flags,
            )
            with self._process_lock:
                self._process = process

            output_queue = queue.Queue()
            def _pipe_reader():
                try:
                    stdout = process.stdout
                    if stdout is None:
                        output_queue.put(None)
                        return
                    while True:
                        data = stdout.read(4096 * 4)
                        if not data:
                            break
                        output_queue.put(data)
                except Exception as exc:
                    output_queue.put(("__reader_error__", str(exc)))
                finally:
                    output_queue.put(None)

            reader = threading.Thread(target=_pipe_reader, name="GodEyesRTSPAudioReader", daemon=True)
            reader.start()
            started_at = time.monotonic()
            last_audio_at = started_at
            got_audio = False
            try:
                while not self._stop_event.is_set():
                    try:
                        item = output_queue.get(timeout=0.5)
                    except queue.Empty:
                        item = "__timeout_tick__"

                    if item is None:
                        if self._stop_event.is_set():
                            return
                        try:
                            process.wait(timeout=0.5)
                        except Exception:
                            pass
                        details = self._ffmpeg_error_text(error_file)
                        if any(key in details.lower() for key in (
                            "matches no streams", "does not contain any stream", "no audio", "map 0:a:0"
                        )):
                            cause = "Luồng RTSP không có track âm thanh (hoặc camera chưa bật audio)."
                        elif not got_audio:
                            cause = "FFmpeg đã kết thúc nhưng chưa nhận được mẫu âm thanh từ RTSP."
                        else:
                            cause = "Luồng âm thanh RTSP đã kết thúc."
                        raise RuntimeError(cause + (" Chi tiết FFmpeg: " + details if details else ""))

                    if isinstance(item, tuple) and item and item[0] == "__reader_error__":
                        raise RuntimeError("Không đọc được dữ liệu âm thanh FFmpeg: " + item[1])

                    if item == "__timeout_tick__":
                        now = time.monotonic()
                        if process.poll() is not None:
                            continue  # Reader thread will deliver EOF shortly.
                        timeout_seconds = 15.0 if not got_audio else 12.0
                        if now - last_audio_at > timeout_seconds:
                            details = self._ffmpeg_error_text(error_file)
                            cause = (
                                "Kết nối RTSP chưa tạo ra dữ liệu âm thanh sau 15 giây. "
                                "Có thể sai stream path, camera chưa bật audio, hoặc luồng RTSP chỉ có hình ảnh."
                                if not got_audio else
                                "Luồng RTSP không gửi thêm mẫu âm thanh trong 12 giây."
                            )
                            raise RuntimeError(cause + (" Chi tiết FFmpeg: " + details if details else ""))
                        continue

                    last_audio_at = time.monotonic()
                    usable = len(item) - (len(item) % 4)
                    if usable < 4:
                        continue
                    samples = np.frombuffer(item[:usable], dtype="<f4")
                    samples = np.nan_to_num(samples, copy=True)
                    power = float(np.mean(np.square(samples, dtype=np.float64)))
                    if math.isfinite(power) and power >= 0.0:
                        got_audio = True
                        self.level_power.emit(power)
            finally:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1.2)
                        except Exception:
                            process.kill()
                except Exception:
                    pass
                try:
                    if process.stdout:
                        process.stdout.close()
                except Exception:
                    pass
                with self._process_lock:
                    if self._process is process:
                        self._process = None

    def _read_local_microphone(self):
        import sounddevice as sd

        devices = list(sd.query_devices())
        try:
            hostapis = list(sd.query_hostapis())
        except Exception:
            hostapis = []

        try:
            default_pair = sd.default.device
            default_input = int(default_pair[0])
        except Exception:
            default_input = -1

        candidates = []
        for index, info in enumerate(devices):
            try:
                channels = int(info.get("max_input_channels") or 0)
                if channels <= 0:
                    continue
                name = str(info.get("name") or f"Input device {index}")
                name_upper = name.upper()
                host_index = int(info.get("hostapi", -1))
                host_name = (
                    str(hostapis[host_index].get("name") or "").upper()
                    if 0 <= host_index < len(hostapis) else ""
                )
                if "MICROPHONE" in name_upper and "HEADSET" not in name_upper:
                    priority = 0
                elif index == default_input:
                    priority = 1
                elif "WASAPI" in host_name:
                    priority = 2
                elif "DIRECTSOUND" in host_name:
                    priority = 3
                elif "MME" in host_name:
                    priority = 4
                else:
                    priority = 5
                candidates.append((priority, index, info, channels, name))
            except Exception:
                continue

        candidates.sort(key=lambda item: (item[0], item[1]))
        errors = []
        for _priority, index, info, max_channels, name in candidates:
            if self._stop_event.is_set():
                return
            try:
                native_rate = int(round(float(info.get("default_samplerate") or 44100)))
            except Exception:
                native_rate = 44100
            rates = []
            for rate in (native_rate, 44100, 48000, 32000, 22050, 16000, 8000):
                if rate > 0 and rate not in rates:
                    rates.append(rate)

            channel_options = [1, 2] if max_channels >= 2 else [1]
            for rate in rates:
                for channels in channel_options:
                    if self._stop_event.is_set():
                        return
                    try:
                        sd.check_input_settings(
                            device=index, channels=channels,
                            dtype="float32", samplerate=rate,
                        )
                    except Exception as exc:
                        errors.append(f"{name} ({index}) {rate} Hz: {exc}")
                        continue

                    try:
                        blocksize = max(512, int(round(rate * 0.25)))
                        with sd.InputStream(
                            device=index, samplerate=rate, channels=channels,
                            dtype="float32", blocksize=blocksize, latency="high",
                        ) as stream:
                            while not self._stop_event.is_set():
                                audio, _overflowed = stream.read(blocksize)
                                samples = np.asarray(audio, dtype=np.float32)
                                if not samples.size:
                                    continue
                                samples = np.nan_to_num(samples, copy=True)
                                power = float(np.mean(np.square(samples, dtype=np.float64)))
                                if math.isfinite(power) and power >= 0.0:
                                    self.level_power.emit(power)
                        return
                    except Exception as exc:
                        errors.append(f"{name} ({index}) {rate} Hz: {exc}")

        raise RuntimeError("Không mở được microphone đầu vào trên máy tính. " + " | ".join(errors[-3:]))

    def run(self):
        try:
            if self.rtsp_url:
                self._read_rtsp_audio()
            else:
                self._read_local_microphone()
        except Exception as exc:
            if not self._stop_event.is_set():
                self.audio_error.emit(f"{type(exc).__name__}: {exc}")


def _migrate_legacy_server_config():
    """Move a previous source-run config to the persistent user data folder."""
    try:
        if SERVER_CONFIG_PATH.exists() or not LEGACY_SERVER_CONFIG_PATH.is_file():
            return
        if SERVER_CONFIG_PATH.resolve() == LEGACY_SERVER_CONFIG_PATH.resolve():
            return
        legacy = json.loads(LEGACY_SERVER_CONFIG_PATH.read_text(encoding='utf-8'))
        if not isinstance(legacy, dict):
            return
        server_url = str(legacy.get('server_url') or '').strip().rstrip('/')
        parsed = urlparse(server_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return
        SERVER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SERVER_CONFIG_PATH.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'server_url': server_url}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, SERVER_CONFIG_PATH)
    except Exception:
        # A bad old config must not prevent the desktop app from starting.
        try:
            SERVER_CONFIG_PATH.with_suffix('.json.tmp').unlink(missing_ok=True)
        except Exception:
            pass


class MainWindow(QMainWindow):
    force_timeout_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('God Eyes')
        self.setMinimumSize(1160, 760)
        self.resize(1320, 820)
        self.setStyleSheet(STYLE)
        if LOGO_PATH.exists():
            self.setWindowIcon(QIcon(_godeyes_logo_pixmap(256)))
        self.db = Database()
        _migrate_legacy_server_config()
        self.server = ServerClient.from_config(SERVER_CONFIG_PATH)
        self.settings = QSettings('GodEyes', 'GodEyes')
        try:
            self.session_duration_seconds = max(60, min(180 * 60, int(self.settings.value('scan_duration_seconds', 45 * 60) or 45 * 60)))
        except Exception:
            self.session_duration_seconds = 45 * 60
        self.session_deadline_monotonic = None
        self._session_watchdog_timer = None
        self._session_watchdog_key = None
        self._session_watchdog_lock = threading.RLock()
        self.force_timeout_signal.connect(self._handle_force_timeout_signal)
        self._teacher_preferences_future = None
        self.ui_language = _normalize_ui_language(self.settings.value('ui_language', 'vi'))
        self.ui_theme = str(self.settings.value('ui_theme', 'light') or 'light').lower()
        if self.ui_theme not in ('light', 'dark'):
            self.ui_theme = 'light'
        self.server.device_token = str(self.settings.value('device_token', '') or '')
        self.server_profile = None
        self.server_classes = []
        self.server_roster = []
        self.selected_server_roster = []
        self.absent_server_roster = []
        self.attendance_selected_ids = set()
        self.attendance_absent_ids = set()
        self.expected_student_count = 0
        self.server_session_id = None
        self.server_selected_class_id = 0
        self.scan_date_context = ''
        self.server_username = ''
        self.server_status_text = 'SERVER NOT CONNECTED'
        self.server_sync_text = ''
        self.server_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='GodEyesSync')
        self.avatar_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='GodEyesAvatar')
        self._avatar_photo_jobs = []
        self.remote_futures = []
        self._roster_future = None
        self._attendance_only_flow = False
        self._session_create_future = None
        self._async_start_timer = QTimer(self)
        self._async_start_timer.timeout.connect(self._poll_async_start)
        self._async_start_timer.setInterval(50)
        self.last_heartbeat = 0.0
        self.camera = None
        self.ai = None
        self.latest = None
        self.current_session = None
        self.session_start = None
        self.session_elapsed = 0.0
        self.paused = False
        self.audio_monitor = None
        self.audio_power_sum = 0.0
        self.audio_samples_count = 0
        self.audio_last_dbfs = None
        self.audio_avg_dbfs = None
        self.audio_monitor_error_text = ''
        self.camera_audio_test_worker = None
        self.camera_audio_test_power_seen = False
        self.camera_audio_test_had_error = False
        self.event_count = 0
        self.student_count = 0
        self.pending_scan = False
        self.pending_monitor_after_camera = False
        self.pending_face_scan = False
        self.face_scan_active = False
        self.board_side = 'RIGHT'
        self.overlay_items = []
        self.overlay_updated_at = 0.0
        # Keep an already verified track visible briefly when a face turns away or is occluded.
        # This is a short UI tracking grace, not a new face-recognition decision.
        self._verified_track_identity = {}
        self._student_last_visible_at = {}
        self._event_last_saved_at = {}
        self._student_last_alert_at = {}
        # Lightweight camera-FPS visual follower. AI inference remains at its
        # configured rate; this only updates the already-known box between AI
        # detections using sparse optical flow on the newest camera frames.
        self._rt_prev_gray = None
        self._rt_prev_sequence = 0
        self._rt_points = {}
        self._rt_box_by_track = {}
        self._rt_last_render_sequence = 0
        self._rt_render_interval_ms = 33
        self.ai_status_text = 'AI READY'
        self.camera_source = _normalize_camera_source(self.settings.value('camera_source', 'webcam'))
        self.camera_brand = str(self.settings.value('camera_brand', 'tapo') or 'tapo').lower()
        if self.camera_brand != 'tapo':
            self.camera_brand = 'tapo'
        try: self.camera_device_index = max(0, min(9, int(self.settings.value('camera_device_index', 0) or 0)))
        except Exception: self.camera_device_index = 0
        self.camera_host = str(self.settings.value('camera_host', '') or '').strip()
        try: self.camera_port = max(1, min(65535, int(self.settings.value('camera_port', 554) or 554)))
        except Exception: self.camera_port = 554
        self.camera_stream = str(self.settings.value('camera_stream', 'stream1') or 'stream1').lower()
        if self.camera_stream not in ('stream1', 'stream2'): self.camera_stream = 'stream1'
        self.camera_username = str(self.settings.value('camera_username', '') or '').strip()
        self.camera_password = str(self.settings.value('camera_password_local', '') or '')
        # Teacher Portal preferences are polled in the background. Network calls
        # never run on the Qt GUI thread, and camera changes are deferred during a session.
        self.settings_sync_timer = QTimer(self)
        self.settings_sync_timer.setInterval(30000)
        self.settings_sync_timer.timeout.connect(self._schedule_teacher_preferences_sync)
        self.teacher_preferences_poll_timer = QTimer(self)
        self.teacher_preferences_poll_timer.setInterval(500)
        self.teacher_preferences_poll_timer.timeout.connect(self._poll_teacher_preferences_sync)
        self._build()
        self._apply_ui_preferences(self.ui_language, self.ui_theme, save_local=False)
        self.avatar_poll_timer = QTimer(self)
        self.avatar_poll_timer.setInterval(250)
        self.avatar_poll_timer.timeout.connect(self._poll_avatar_photo_jobs)
        self.avatar_poll_timer.start()
        self.ui_translation_timer = QTimer(self)
        self.ui_translation_timer.setInterval(1000)
        self.ui_translation_timer.timeout.connect(lambda: _translate_runtime_widget_texts(self, self.ui_language))
        self.ui_translation_timer.start()
        self.overview_clock_timer = QTimer(self)
        self.overview_clock_timer.setInterval(1000)
        self.overview_clock_timer.timeout.connect(self._update_overview_clock)
        self.overview_clock_timer.start()
        self._update_overview_clock()

        # Keep the UI preview refreshed from the newest frame in CameraWorker's shared buffer.
        # CameraWorker owns capture; this timer only paints the latest frame on the Qt UI thread.
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self._render)
        # USB camera preview follows the 60 FPS capture path. AI inference stays
        # independently throttled so CPU-heavy detection is not forced to 60 FPS.
        # Always render from the latest camera frame. USB capture itself can
        # run at 60 FPS; UI paint is capped at ~30 FPS to avoid CPU/GPU backlog.
        self.render_timer.start(33)

        # The previous code defined _tick() but never created/started its QTimer.
        # This timer is the actual 45-minute lesson clock.
        self.session_timer = QTimer(self)
        self.session_timer.timeout.connect(self._tick)
        self.session_timer.start(250)
        self.settings_sync_timer.start()
        self.teacher_preferences_poll_timer.start()

        self._set_page(0)

    # [AI-GENERATED / AI-ASSISTED CODE] Reusable UI card helper.
    def _card(self):
        f = QFrame(); f.setObjectName('card'); return f

    # [AI-GENERATED / AI-ASSISTED CODE] Reusable UI label helper.
    def _label(self, text, role=None):
        language = getattr(self, 'ui_language', 'vi')
        l = QLabel(_translate_text(str(text), language))
        if role:
            l.setObjectName(role)
        return l

    def _room_word(self):
        return 'Room' if getattr(self, 'ui_language', 'vi') == 'en' else 'Lớp'

    def _scrollable_page(self, page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

    # [AI-GENERATED / AI-ASSISTED CODE] Builds sidebar, navigation, and page stack.
    def _build(self):
        root = QWidget(); outer = QHBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        side = QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(220)
        sl = QVBoxLayout(side); sl.setContentsMargins(18,22,18,18); sl.setSpacing(8)
        self.logo_view = QLabel()
        self.logo_view.setObjectName('logo_image')
        self.logo_view.setAlignment(Qt.AlignCenter)
        self.logo_view.setMinimumHeight(148)
        self.logo_view.setMaximumHeight(170)
        if LOGO_PATH.exists():
            logo = _godeyes_logo_pixmap(256)
            if not logo.isNull():
                self.logo_view.setPixmap(logo.scaled(192,148,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:
                self.logo_view.setText('God Eyes')
        else:
            self.logo_view.setText('God Eyes')
        sl.addWidget(self.logo_view)
        brand=self._label('CLASSROOM INTELLIGENCE' if self.ui_language == 'en' else 'GIÁM SÁT LỚP HỌC THÔNG MINH','eyebrow')
        brand.setAlignment(Qt.AlignCenter)
        sl.addWidget(brand)
        sl.addSpacing(18)
        self.nav=[]
        home_label = 'Overview' if self.ui_language == 'en' else 'Tổng quan'
        b=QPushButton(home_label)
        b.setIcon(QIcon(_godeyes_line_icon('dashboard', 18)))
        b.setIconSize(QSize(18, 18))
        b.setObjectName('nav_active')
        b.clicked.connect(lambda: self._set_page(0))
        self.nav.append((b,0))
        sl.addWidget(b)
        sl.addStretch()
        local_text = 'LOCAL AI • OFFLINE' if self.ui_language == 'en' else 'AI CỤC BỘ • NGOẠI TUYẾN'
        local=self._label(local_text,'pill')
        self.local_ai_label = local
        local.setAlignment(Qt.AlignCenter)
        sl.addWidget(local)
        self.server_badge=self._label('SERVER • DISCONNECTED' if self.ui_language == 'en' else 'MÁY CHỦ • CHƯA KẾT NỐI','pill')
        self.server_badge.setAlignment(Qt.AlignCenter)
        sl.addWidget(self.server_badge)
        exit_text = 'Exit' if self.ui_language == 'en' else 'Thoát'
        exit_btn=QPushButton(exit_text)
        exit_btn.setObjectName('danger')
        exit_btn.clicked.connect(self.close)
        sl.addWidget(exit_btn)

        # Main view: Overview at the top and Camera immediately below it.
        # Index 1 is retained as a hidden compatibility slot so the existing
        # Face ID scan/monitor workflow can keep its stable indices (2 and 3).
        self.pages=QStackedWidget()
        self.pages.setObjectName('mainPageStack')
        self.pages.addWidget(self.page_home())  # 0: Overview (scrollable)
        self.pages.addWidget(self._scrollable_page(self.page_camera()))  # 1: Camera (separate page)
        self.pages.addWidget(self.page_scan())                  # 2
        self.pages.addWidget(self.page_monitor())               # 3
        outer.addWidget(side); outer.addWidget(self.pages,1); self.setCentralWidget(root)

    def _page_overview_with_camera(self):
        """Build one vertically scrolling screen with Overview first and Camera below."""
        overview_scroll = self.page_home()
        camera_scroll = self.page_camera()
        overview = overview_scroll.takeWidget()
        camera = camera_scroll.takeWidget()
        overview_scroll.deleteLater()
        camera_scroll.deleteLater()

        # Remove the old bottom stretch so it cannot create a blank gap before Camera.
        overview_layout = overview.layout()
        if overview_layout is not None and overview_layout.count():
            last_item = overview_layout.itemAt(overview_layout.count() - 1)
            if last_item is not None and last_item.spacerItem() is not None:
                overview_layout.removeItem(last_item)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(overview)
        content_layout.addWidget(camera)
        self.overview_content_widget = overview
        self.camera_section_widget = camera
        self.combined_overview_camera_scroll = self._scrollable_page(content)
        return self.combined_overview_camera_scroll

    def _scroll_to_camera_section(self):
        """Scroll the main view to the Camera section without changing workflow state."""
        scroll = getattr(self, 'combined_overview_camera_scroll', None)
        target = getattr(self, 'camera_section_widget', None)
        if scroll is None or target is None:
            return
        try:
            if hasattr(self, 'pages') and self.pages.currentIndex() != 0:
                self.pages.setCurrentIndex(0)
            self._set_nav_state(1)
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(target.y()))
        except Exception:
            pass

    # [AI-GENERATED / AI-ASSISTED CODE] Shared page-header component.
    def _head(self,title,sub=''):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setSpacing(4); l.addWidget(self._label(title,'title'))
        if sub:l.addWidget(self._label(sub,'muted'))
        return w

    # [AI-GENERATED / AI-ASSISTED CODE] Dashboard / Home UI.
    def page_home(self):
        """Overview page styled to closely match the GodEyes UI reference."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(22, 18, 22, 22)
        l.setSpacing(11)
        en = self.ui_language == 'en'

        # Header with compact brand icon and a small date/time card.
        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(11)
        header_icon = QLabel()
        header_icon.setPixmap(_godeyes_line_icon('dashboard', 22))
        header_icon.setObjectName('headerIcon')
        header_icon.setAlignment(Qt.AlignCenter)
        header_icon.setFixedSize(38, 40)
        header_row.addWidget(header_icon)
        heading = QWidget()
        heading_layout = QVBoxLayout(heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(2)
        heading_layout.addWidget(self._label('Overview' if en else 'Tổng quan', 'title'))
        heading_layout.addWidget(self._label(
            'Choose a class, mark attendance and prepare the scan.' if en else
            'Chọn lớp, điểm danh học sinh và chuẩn bị quét.', 'muted'))
        header_row.addWidget(heading, 1)

        clock_card = QFrame()
        clock_card.setObjectName('clockCard')
        clock_card.setMaximumWidth(250)
        clock_row = QHBoxLayout(clock_card)
        clock_row.setContentsMargins(10, 7, 12, 7)
        clock_row.setSpacing(8)
        clock_icon = QLabel()
        clock_icon.setPixmap(_godeyes_line_icon('calendar', 18))
        clock_icon.setObjectName('headerIcon')
        clock_icon.setFixedSize(30, 32)
        clock_icon.setAlignment(Qt.AlignCenter)
        clock_icon.setStyleSheet('font-size:18px;')
        clock_row.addWidget(clock_icon)
        clock_texts = QWidget()
        clock_text_layout = QVBoxLayout(clock_texts)
        clock_text_layout.setContentsMargins(0, 0, 0, 0)
        clock_text_layout.setSpacing(0)
        self.overview_date_label = self._label('', 'muted')
        self.overview_time_label = self._label('', 'section')
        clock_text_layout.addWidget(self.overview_date_label)
        clock_text_layout.addWidget(self.overview_time_label)
        clock_row.addWidget(clock_texts)
        header_row.addWidget(clock_card, 0, Qt.AlignVCenter)
        self.overview_ready_badge = self._label('SẴN SÀNG CHUẨN BỊ', 'pill')
        self.overview_ready_badge.setVisible(False)
        l.addWidget(header)

        # 1. Class picker and compact select-all controls.
        class_card = self._card()
        class_layout = QVBoxLayout(class_card)
        class_layout.setContentsMargins(15, 12, 15, 12)
        class_layout.setSpacing(7)
        class_layout.addWidget(self._label('1. Choose class' if en else '1. Chọn lớp học', 'section'))
        class_row = QHBoxLayout()
        class_row.setContentsMargins(0, 0, 0, 0)
        class_row.setSpacing(8)
        self.class_combo = QComboBox()
        self.class_combo.setMinimumHeight(38)
        self.class_combo.setMinimumWidth(260)
        self.class_combo.setMaximumWidth(330)
        self.class_combo.currentIndexChanged.connect(self._class_changed)
        class_row.addWidget(self.class_combo, 0)

        self.class_info = self._label(
            'Choose a class to load students.' if en else 'Chọn lớp để tải danh sách học sinh.', 'muted')
        self.class_info.setWordWrap(True)
        self.class_info.setMaximumWidth(265)
        class_row.addWidget(self.class_info, 0)

        self.overview_select_all_button = QPushButton('☑  Select all' if en else '☑  Chọn tất cả')
        self.overview_select_all_button.setObjectName('minorAction')
        self.overview_select_all_button.setFixedSize(112, 36)
        self.overview_select_all_button.clicked.connect(lambda: self._set_all_overview_students(True))
        self.overview_select_all_button.setVisible(False)
        class_row.addWidget(self.overview_select_all_button, 0)

        self.overview_clear_all_button = QPushButton('☐  Clear all' if en else '☐  Bỏ chọn tất cả')
        self.overview_clear_all_button.setObjectName('minorAction')
        self.overview_clear_all_button.setFixedSize(124, 36)
        self.overview_clear_all_button.clicked.connect(lambda: self._set_all_overview_students(False))
        self.overview_clear_all_button.setVisible(False)
        class_row.addWidget(self.overview_clear_all_button, 0)
        class_row.addStretch(1)
        class_layout.addLayout(class_row)
        l.addWidget(class_card)

        # 2. Attendance: use the real roster, not demonstration/fake students.
        attendance_card = self._card()
        attendance_layout = QVBoxLayout(attendance_card)
        attendance_layout.setContentsMargins(15, 12, 15, 12)
        attendance_layout.setSpacing(7)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        toolbar.addWidget(self._label('2. Select students to scan' if en else '2. Điểm danh và chọn học sinh', 'section'), 1)
        self.overview_student_search = QLineEdit()
        self.overview_student_search.setPlaceholderText('Search students…' if en else 'Tìm kiếm học sinh…')
        self.overview_student_search.setClearButtonEnabled(True)
        self.overview_student_search.setFixedSize(215, 36)
        self.overview_student_search.textChanged.connect(self._filter_overview_student_cards)
        self.overview_student_search.setVisible(False)
        toolbar.addWidget(self.overview_student_search, 0)
        attendance_layout.addLayout(toolbar)

        attendance_hint = self._label(
            'Only selected students are included in face matching and session records.' if en else
            'Chỉ học sinh được chọn mới được đối chiếu khuôn mặt và ghi nhận dữ liệu trong buổi quét.', 'muted')
        attendance_hint.setWordWrap(True)
        attendance_layout.addWidget(attendance_hint)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
        self.overview_attendance_summary = self._label(
            'Attendance has not been completed.' if en else 'Chưa điểm danh cho buổi quét này.', 'pill')
        self.overview_attendance_summary.setWordWrap(True)
        self.overview_attendance_summary.setMinimumHeight(36)
        controls_row.addWidget(self.overview_attendance_summary, 1)
        self.attendance_prepare_button = QPushButton('ATTENDANCE' if en else 'ĐIỂM DANH')
        self.attendance_prepare_button.setObjectName('minorAction')
        self.attendance_prepare_button.setFixedSize(158, 38)
        self.attendance_prepare_button.clicked.connect(self._prepare_attendance_from_overview)
        controls_row.addWidget(self.attendance_prepare_button, 0)
        attendance_layout.addLayout(controls_row)

        self.overview_students_placeholder = self._label(
            'Choose a class, then mark attendance to load students.' if en else
            'Chọn lớp rồi bấm Điểm danh để tải danh sách học sinh.', 'emptyState')
        self.overview_students_placeholder.setAlignment(Qt.AlignCenter)
        self.overview_students_placeholder.setMinimumHeight(56)
        attendance_layout.addWidget(self.overview_students_placeholder)

        self.overview_students_host = QWidget()
        self.overview_students_grid = QGridLayout(self.overview_students_host)
        self.overview_students_grid.setContentsMargins(0, 0, 0, 0)
        self.overview_students_grid.setHorizontalSpacing(8)
        self.overview_students_grid.setVerticalSpacing(8)
        for col in range(6):
            self.overview_students_grid.setColumnStretch(col, 1)
        self.overview_students_host.setVisible(False)
        attendance_layout.addWidget(self.overview_students_host)
        self.overview_student_cards = []
        self.overview_student_checks = {}
        l.addWidget(attendance_card)

        # 3. Duration presets on the left; mandatory stop notice on the right.
        duration_card = self._card()
        duration_row = QHBoxLayout(duration_card)
        duration_row.setContentsMargins(14, 12, 14, 12)
        duration_row.setSpacing(11)

        duration_controls = QWidget()
        duration_layout = QVBoxLayout(duration_controls)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(7)
        duration_layout.addWidget(self._label('3. Scan duration' if en else '3. Thời gian quét', 'section'))

        self.scan_duration_combo = QComboBox()
        duration_options = (
            [('15 minutes', 15 * 60), ('30 minutes', 30 * 60), ('45 minutes', 45 * 60),
             ('60 minutes', 60 * 60), ('Custom', 0)] if en else
            [('15 phút', 15 * 60), ('30 phút', 30 * 60), ('45 phút', 45 * 60),
             ('60 phút', 60 * 60), ('Tùy chỉnh', 0)]
        )
        for label, seconds in duration_options:
            self.scan_duration_combo.addItem(label, seconds)
        selected_index = next((i for i in range(self.scan_duration_combo.count())
                               if int(self.scan_duration_combo.itemData(i) or 0) == self.session_duration_seconds), -1)
        if selected_index < 0:
            selected_index = 2 if self.session_duration_seconds == 45 * 60 else 4
        self.scan_duration_combo.setCurrentIndex(selected_index)
        self.scan_duration_combo.setVisible(False)

        presets = QWidget()
        presets_row = QHBoxLayout(presets)
        presets_row.setContentsMargins(0, 0, 0, 0)
        presets_row.setSpacing(6)
        self.duration_buttons = []
        duration_labels = (
            ['15 min', '30 min', '45 min', '60 min', 'Custom'] if en else
            ['15 phút', '30 phút', '45 phút', '60 phút', 'Tùy chỉnh']
        )
        for i, label in enumerate(duration_labels):
            button = QPushButton(label)
            button.setObjectName('durationPreset')
            button.setCheckable(True)
            button.setFixedSize(76 if i < 4 else 92, 36)
            button.setToolTip(('Set scan duration' if en else 'Chọn thời lượng quét') + f': {label}')
            button.clicked.connect(lambda checked=False, index=i: self._choose_scan_duration_preset(index))
            presets_row.addWidget(button, 0)
            self.duration_buttons.append(button)
        presets_row.addStretch(1)
        duration_layout.addWidget(presets)

        self.custom_duration_row = QWidget()
        custom_row = QHBoxLayout(self.custom_duration_row)
        custom_row.setContentsMargins(0, 0, 0, 0)
        custom_row.setSpacing(7)
        self.custom_duration_checkbox = QCheckBox('Custom' if en else 'Tùy chỉnh')
        self.custom_duration_checkbox.setFixedWidth(92)
        self.custom_duration_checkbox.toggled.connect(self._custom_duration_option_toggled)
        custom_row.addWidget(self.custom_duration_checkbox, 0)
        self.custom_scan_minutes = QLineEdit(str(max(1, min(180, self.session_duration_seconds // 60))))
        self.custom_scan_minutes.setFixedSize(78, 34)
        self.custom_scan_minutes.setPlaceholderText('1–180')
        self.custom_scan_minutes.textChanged.connect(self._on_custom_scan_minutes_changed)
        self.custom_scan_minutes.textChanged.connect(self._update_overview_auto_stop_caption)
        custom_row.addWidget(self.custom_scan_minutes, 0)
        custom_row.addWidget(self._label('minutes (1–180)' if en else 'phút (1–180)', 'muted'), 0)
        custom_row.addStretch(1)
        duration_layout.addWidget(self.custom_duration_row)
        self.scan_duration_combo.currentIndexChanged.connect(self._refresh_duration_preset_ui)
        duration_row.addWidget(duration_controls, 3)

        force_stop = QFrame()
        force_stop.setObjectName('forceStopNotice')
        force_stop.setMinimumWidth(300)
        stop_row = QHBoxLayout(force_stop)
        stop_row.setContentsMargins(12, 11, 12, 11)
        stop_row.setSpacing(8)
        warning_icon = QLabel('!')
        warning_icon.setAlignment(Qt.AlignCenter)
        warning_icon.setFixedSize(30, 30)
        warning_icon.setStyleSheet('background:#ff5967;color:white;border-radius:15px;font-size:18px;font-weight:900;')
        stop_row.addWidget(warning_icon, 0, Qt.AlignTop)
        stop_texts = QWidget()
        stop_text_layout = QVBoxLayout(stop_texts)
        stop_text_layout.setContentsMargins(0, 0, 0, 0)
        stop_text_layout.setSpacing(3)
        stop_title = self._label('Mandatory automatic stop' if en else 'Tự động dừng (Bắt buộc)', 'section')
        stop_title.setStyleSheet('color:#d73543;font-weight:900;background:transparent;')
        stop_text_layout.addWidget(stop_title)
        stop_description = self._label(
            'When the time expires, scanning stops immediately, whatever state the app is in.' if en else
            'Hết giờ, hệ thống buộc dừng quét ngay, bất kể đang xử lý hay tạm dừng.', 'muted')
        stop_description.setWordWrap(True)
        stop_text_layout.addWidget(stop_description)
        stop_row.addWidget(stop_texts, 1)
        duration_row.addWidget(force_stop, 2)
        l.addWidget(duration_card)
        self._refresh_duration_preset_ui()

        # Compact blue action bar: fixed-size start action plus the auto-stop countdown.
        # Keep the primary action bar focused on the scan button only.
        # The session timer still enforces the selected duration; the redundant
        # 'Auto-stop after 45 min' caption is intentionally not shown here.
        self.overview_action_bar = QFrame()
        self.overview_action_bar.setObjectName('primaryActionBar')
        self.overview_action_bar.setMaximumWidth(370)
        action_layout = QHBoxLayout(self.overview_action_bar)
        action_layout.setContentsMargins(5, 5, 5, 5)
        action_layout.setSpacing(0)
        self.overview_start_button = QPushButton('▶   START SCAN' if en else '▶   BẮT ĐẦU QUÉT')
        self.overview_start_button.setObjectName('primaryActionButton')
        self.overview_start_button.setFixedSize(350, 44)
        self.overview_start_button.setEnabled(False)
        self.overview_start_button.clicked.connect(self._start_from_overview)
        action_layout.addWidget(self.overview_start_button, 0)
        l.addWidget(self.overview_action_bar, 0, Qt.AlignLeft)
        return self._scrollable_page(w)


    def _update_overview_auto_stop_caption(self, *_args):
        """Compatibility no-op: the redundant countdown caption is hidden from the UI."""
        return

    def _update_overview_clock(self):
        if not hasattr(self, 'overview_date_label') or not hasattr(self, 'overview_time_label'):
            return
        now = datetime.now()
        if self.ui_language == 'en':
            date_text = now.strftime('%a, %d %b %Y')
        else:
            weekdays = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật']
            date_text = f'{weekdays[now.weekday()]}, {now:%d/%m/%Y}'
        self.overview_date_label.setText(date_text)
        self.overview_time_label.setText(now.strftime('%H:%M:%S'))

    def _choose_scan_duration_preset(self, index):
        if not hasattr(self, 'scan_duration_combo'):
            return
        if getattr(self, 'current_session', None) is not None:
            return
        index = max(0, min(int(index), self.scan_duration_combo.count() - 1))
        self.scan_duration_combo.setCurrentIndex(index)
        # Keep the chosen duration authoritative even if the user starts from the
        # Camera/Face-ID screen instead of returning through the Overview button.
        try:
            if index < 4:
                seconds = int(self.scan_duration_combo.itemData(index) or 0)
            else:
                seconds = int((self.custom_scan_minutes.text() or '').strip()) * 60
            if 60 <= seconds <= 180 * 60:
                self.session_duration_seconds = seconds
                self.settings.setValue('scan_duration_seconds', seconds)
                self.settings.sync()
        except (TypeError, ValueError):
            pass
        self._refresh_duration_preset_ui()

    def _custom_duration_option_toggled(self, checked):
        if checked:
            self._choose_scan_duration_preset(4)
        elif hasattr(self, 'scan_duration_combo') and self.scan_duration_combo.currentIndex() == 4:
            self._choose_scan_duration_preset(2)

    def _on_custom_scan_minutes_changed(self, *_args):
        """GOD EYES DURATION AND UNKNOWN FACE OVERLAY V19.

        Editing the custom minutes field selects Custom automatically and persists
        valid values, so typing "1" cannot silently leave the 45-minute preset active.
        """
        if getattr(self, 'current_session', None) is not None:
            return
        combo = getattr(self, 'scan_duration_combo', None)
        field = getattr(self, 'custom_scan_minutes', None)
        if combo is None or field is None:
            return
        if combo.currentIndex() != 4:
            combo.setCurrentIndex(4)
        try:
            minutes = int((field.text() or '').strip())
        except (TypeError, ValueError):
            return
        if not 1 <= minutes <= 180:
            return
        self.session_duration_seconds = minutes * 60
        try:
            self.settings.setValue('scan_duration_seconds', self.session_duration_seconds)
            self.settings.sync()
        except Exception:
            pass

    def _refresh_duration_preset_ui(self, *_args):
        if not hasattr(self, 'scan_duration_combo'):
            return
        selected = self.scan_duration_combo.currentIndex()
        for i, button in enumerate(getattr(self, 'duration_buttons', [])):
            button.blockSignals(True)
            button.setChecked(i == selected)
            button.blockSignals(False)
        if hasattr(self, 'custom_duration_checkbox'):
            self.custom_duration_checkbox.blockSignals(True)
            self.custom_duration_checkbox.setChecked(selected == 4)
            self.custom_duration_checkbox.blockSignals(False)
        # Keep the custom row visible, as in the reference UI; the checkbox
        # determines whether the typed value is used for this session.
        if hasattr(self, 'custom_duration_row'):
            self.custom_duration_row.setVisible(True)
        self._update_overview_auto_stop_caption()

    def _set_student_avatar(self, label, record, student_id=0, no_silhouette=False):
        """Display a stored portrait; otherwise probe the authenticated endpoint."""
        size = max(24, min(120, int(label.width() or 68)))
        label.setAlignment(Qt.AlignCenter)
        label.setScaledContents(False)
        kind, source = _student_image_source(record)
        rendered = QPixmap()
        photo_url = ''
        if kind == 'bytes':
            rendered = _avatar_pixmap_from_bytes(source, size)
        elif kind == 'url':
            photo_url = str(source or '').strip()
            if photo_url.startswith('/') or photo_url.startswith('./') or photo_url.startswith('../'):
                base_url = str(getattr(self.server, 'base_url', '') or '').strip()
                if base_url:
                    photo_url = urljoin(base_url.rstrip('/') + '/', photo_url)
        elif int(student_id or 0) > 0:
            # Some deployed Teacher Portal versions omit photo_url from the roster
            # even though the student photo is stored. Try the secure endpoint directly.
            base_url = str(getattr(self.server, 'base_url', '') or '').strip()
            if base_url:
                photo_url = urljoin(base_url.rstrip('/') + '/', f'api/v1/students/{int(student_id)}/photo')

        if photo_url:
            parsed = urlparse(photo_url)
            if parsed.scheme.lower() in ('http', 'https') and parsed.netloc:
                try:
                    access_token = str(getattr(self.server, 'access_token', '') or '')
                    server_base = str(getattr(self.server, 'base_url', '') or '').strip()
                    future = self.avatar_executor.submit(_download_student_image, photo_url, access_token, server_base)
                    self._avatar_photo_jobs.append((future, label, size, int(student_id or 0), time.monotonic()))
                except Exception:
                    pass

        label.setProperty('studentPhotoNoSilhouette', bool(no_silhouette))
        if not rendered.isNull():
            label.setText('')
            label.setPixmap(rendered)
            label.setStyleSheet('background:transparent;border:1px solid #d3e5f8;border-radius:12px;padding:0;')
        elif no_silhouette:
            # Avoid presenting a generic person icon as if it were this student's photo.
            label.setPixmap(QPixmap())
            label.setText(('Đang tải ảnh…' if photo_url else 'Chưa có ảnh') if self.ui_language != 'en'
                          else ('Loading photo…' if photo_url else 'No photo'))
            if getattr(self, 'ui_theme', 'light') == 'dark':
                label.setStyleSheet('background:#14263b;border:1px dashed #496785;border-radius:12px;color:#a6bedb;font-size:10px;font-weight:700;padding:3px;')
            else:
                label.setStyleSheet('background:#f4f8fe;border:1px dashed #b7c9e0;border-radius:12px;color:#7b91ae;font-size:10px;font-weight:700;padding:3px;')
        else:
            label.setPixmap(_generic_student_avatar(size))
        if kind != 'bytes' and not photo_url:
            label.setToolTip((label.toolTip() + ' • ' if label.toolTip() else '') +
                             ('Chưa có ảnh hồ sơ trên máy chủ. Tải ảnh hồ sơ lên Cổng Giáo viên hoặc đăng ký Face ID lại.' if self.ui_language != 'en' else 'No profile photo is stored on the Server. Upload one in Teacher Portal or enroll Face ID again.'))

    def _mark_avatar_photo_unavailable(self, label):
        """Replace an endless loading label with a clear, actionable failure state."""
        try:
            label.setPixmap(QPixmap())
            label.setText('Ảnh không khả dụng' if self.ui_language != 'en' else 'Photo unavailable')
            label.setWordWrap(True)
            if getattr(self, 'ui_theme', 'light') == 'dark':
                label.setStyleSheet('background:#14263b;border:1px dashed #496785;border-radius:12px;color:#a6bedb;font-size:10px;font-weight:700;padding:3px;')
            else:
                label.setStyleSheet('background:#f4f8fe;border:1px dashed #b7c9e0;border-radius:12px;color:#7b91ae;font-size:10px;font-weight:700;padding:3px;')
            label.setToolTip(
                'Không tải được ảnh từ Server. Kiểm tra đăng nhập, quyền truy cập API ảnh và ảnh hồ sơ của học sinh.'
                if self.ui_language != 'en' else
                'Could not load the photo from the Server. Check login, photo API access, and the student profile photo.'
            )
        except RuntimeError:
            # The card may have been removed while its request was in flight.
            pass

    def _poll_avatar_photo_jobs(self):
        pending = []
        now = time.monotonic()
        for job in list(getattr(self, '_avatar_photo_jobs', [])):
            # Accept old 4-field entries too, in case a refresh races with a queued job.
            if len(job) >= 5:
                future, label, size, student_id, submitted_at = job[:5]
            else:
                future, label, size, student_id = job
                submitted_at = now
            if not future.done():
                if now - submitted_at > 10.0:
                    future.cancel()
                    self._mark_avatar_photo_unavailable(label)
                else:
                    pending.append((future, label, size, student_id, submitted_at))
                continue
            try:
                data = future.result()
                rendered = _avatar_pixmap_from_bytes(data, size)
                if not rendered.isNull():
                    label.setText('')
                    label.setPixmap(rendered)
                    label.setStyleSheet('background:transparent;border:1px solid #d3e5f8;border-radius:12px;padding:0;')
                else:
                    self._mark_avatar_photo_unavailable(label)
            except Exception:
                self._mark_avatar_photo_unavailable(label)
        self._avatar_photo_jobs = pending

    def _populate_overview_student_cards(self, roster):
        """Render the current class as selectable cards without inventing student imagery."""
        if not hasattr(self, 'overview_students_grid'):
            return
        grid = self.overview_students_grid
        # The visible cards and the roster used by selection/scan must always be the same list.
        roster = list(roster or [])
        self.server_roster = roster
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.overview_student_cards = []
        self.overview_student_checks = {}
        if not roster:
            self.overview_students_host.setVisible(False)
            self.overview_students_placeholder.setVisible(True)
            self.attendance_prepare_button.setVisible(True)
            self.overview_student_search.setVisible(False)
            self.overview_select_all_button.setVisible(False)
            self.overview_clear_all_button.setVisible(False)
            self.overview_attendance_summary.setText(
                'Attendance has not been completed.' if self.ui_language == 'en' else 'Chưa điểm danh cho buổi quét này.')
            if hasattr(self, 'overview_start_button'):
                self.overview_start_button.setEnabled(False)
            return

        self.overview_students_placeholder.setVisible(False)
        self.overview_students_host.setVisible(True)
        self.attendance_prepare_button.setVisible(False)
        self.overview_student_search.setVisible(True)
        self.overview_select_all_button.setVisible(True)
        self.overview_clear_all_button.setVisible(True)
        columns = 6
        for i, record in enumerate(roster):
            sid = int(record.get('id') or 0)
            name = str(record.get('full_name') or '').strip() or str(record.get('student_code') or f'Student {sid}')
            code = str(record.get('student_code') or '').strip()
            ready = bool(record.get('ready') and record.get('face_embedding'))
            selected = sid in self.attendance_selected_ids
            card = ClickableStudentCard()
            card.setObjectName('studentCardSelected' if selected else 'studentCard')
            card.setFixedSize(164, 164)
            card.setCursor(Qt.PointingHandCursor)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(7, 7, 7, 8)
            card_layout.setSpacing(4)
            checkbox_row = QHBoxLayout()
            checkbox_row.setContentsMargins(0, 0, 0, 0)
            check = StudentSelectionCheckBox()
            check.setFixedSize(22, 22)
            check.setChecked(selected)
            check.setToolTip('Include this student in scanning' if self.ui_language == 'en' else 'Đưa học sinh này vào lượt quét')
            checkbox_row.addWidget(check, 0, Qt.AlignLeft)
            checkbox_row.addStretch(1)
            card_layout.addLayout(checkbox_row)
            avatar = QLabel()
            avatar.setObjectName('student_avatar')
            avatar.setFixedSize(82, 82)
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            avatar.setWordWrap(True)
            avatar.setStyleSheet('background:transparent;border:1px solid #d3e5f8;border-radius:12px;padding:0;')
            avatar.setToolTip(('Face ID ready' if ready else 'Face ID missing') if self.ui_language == 'en' else
                              ('Đã có Face ID' if ready else 'Chưa có Face ID'))
            self._set_student_avatar(avatar, record, sid, no_silhouette=True)
            avatar_row = QHBoxLayout()
            avatar_row.addStretch(1)
            avatar_row.addWidget(avatar)
            avatar_row.addStretch(1)
            card_layout.addLayout(avatar_row)
            name_label = QLabel(name)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setWordWrap(True)
            name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            name_label.setToolTip(f'{code} • {name}' if code else name)
            name_label.setStyleSheet('font-weight:800;background:transparent;color:#173555;')
            name_label.setMaximumHeight(32)
            name_label.setStyleSheet('font-size:11px;font-weight:800;background:transparent;color:#173555;')
            card_layout.addWidget(name_label)
            # Status is preserved in the tooltip and summary to keep cards compact
            # like the reference while still exposing Face ID readiness.
            check.toggled.connect(lambda checked, student_id=sid: self._on_overview_student_toggled(student_id, checked))
            self.overview_student_checks[sid] = check
            card.clicked.connect(lambda student_id=sid: self._toggle_overview_student_card(student_id))
            self.overview_student_cards.append({'id': sid, 'name': name, 'code': code, 'ready': ready, 'widget': card, 'checkbox': check})
            grid.addWidget(card, i // columns, i % columns)
        for col in range(columns):
            grid.setColumnStretch(col, 0)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._filter_overview_student_cards(self.overview_student_search.text())
        self._refresh_overview_attendance_state()

    def _toggle_overview_student_card(self, student_id):
        """Allow selecting from the card body, not only from the tiny checkbox."""
        if getattr(self, 'current_session', None) is not None:
            return
        box = getattr(self, 'overview_student_checks', {}).get(int(student_id))
        if box is not None:
            box.setChecked(not box.isChecked())

    def _on_overview_student_toggled(self, student_id, checked):
        if getattr(self, 'current_session', None) is not None:
            box = self.overview_student_checks.get(int(student_id))
            if box is not None:
                box.blockSignals(True)
                box.setChecked(int(student_id) in self.attendance_selected_ids)
                box.blockSignals(False)
            return
        sid = int(student_id)
        if checked:
            self.attendance_selected_ids.add(sid)
        else:
            self.attendance_selected_ids.discard(sid)
        self._refresh_overview_attendance_state()

    def _set_all_overview_students(self, checked):
        """Apply a bulk attendance choice from one canonical ID set."""
        if getattr(self, 'current_session', None) is not None:
            return
        checks = dict(getattr(self, 'overview_student_checks', {}) or {})
        valid_ids = {int(sid) for sid in checks}
        # Replace the set atomically rather than incrementally updating stale IDs.
        self.attendance_selected_ids = set(valid_ids) if bool(checked) else set()
        for box in checks.values():
            was_blocked = box.blockSignals(True)
            box.setChecked(bool(checked))
            box.blockSignals(was_blocked)
        self._refresh_overview_attendance_state()

    def _filter_overview_student_cards(self, query):
        query = str(query or '').strip().casefold()
        for record in getattr(self, 'overview_student_cards', []):
            match = (not query or query in record['name'].casefold() or query in record['code'].casefold())
            record['widget'].setVisible(bool(match))

    def _refresh_overview_attendance_state(self):
        roster = list(getattr(self, 'server_roster', []) or [])
        session_locked = getattr(self, 'current_session', None) is not None
        # The controls used to look clickable while the event handlers silently
        # ignored clicks during an active session. Make the lock visible and
        # restore the controls as soon as the session ends.
        lock_tip = (
            'Attendance is locked while a session is running. End the session to edit it.'
            if self.ui_language == 'en' else
            'Điểm danh đang khóa khi buổi học chạy. Hãy kết thúc buổi học để chỉnh sửa.'
        )
        edit_tip = ('Select or clear students before starting a scan.' if self.ui_language == 'en'
                    else 'Chọn hoặc bỏ chọn học sinh trước khi bắt đầu quét.')
        for attr in ('overview_select_all_button', 'overview_clear_all_button'):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(not session_locked)
                button.setToolTip(lock_tip if session_locked else edit_tip)
        for record in getattr(self, 'overview_student_cards', []):
            widget = record.get('widget')
            if widget is not None:
                widget.setEnabled(not session_locked)
                widget.setToolTip(lock_tip if session_locked else edit_tip)
        for box in getattr(self, 'overview_student_checks', {}).values():
            box.setEnabled(not session_locked)
        all_ids = {int(item.get('id') or 0) for item in roster}
        selected_ids = {int(sid) for sid in getattr(self, 'attendance_selected_ids', set()) if int(sid) in all_ids}
        self.attendance_selected_ids = selected_ids
        self.attendance_absent_ids = all_ids - selected_ids
        # Prevent a stale blue checkbox from remaining visible when a roster refresh or
        # class change removed its ID from the canonical selected set.
        for sid, box in getattr(self, 'overview_student_checks', {}).items():
            desired = int(sid) in selected_ids
            if box.isChecked() != desired:
                box.blockSignals(True)
                box.setChecked(desired)
                box.blockSignals(False)
        self.selected_server_roster = [item for item in roster if int(item.get('id') or 0) in selected_ids and item.get('ready') and item.get('face_embedding')]
        self.absent_server_roster = [item for item in roster if int(item.get('id') or 0) not in selected_ids]
        self.expected_student_count = len(selected_ids)
        ready_count = len(self.selected_server_roster)
        if roster:
            if not selected_ids:
                summary_text = (f'No students selected • {len(roster)} students in this class' if self.ui_language == 'en'
                                else f'Chưa chọn học sinh • Lớp có {len(roster)} học sinh')
            else:
                summary_text = (f'Selected: {len(selected_ids)}/{len(roster)} • Face ID ready: {ready_count}' if self.ui_language == 'en' else
                                f'Đã chọn: {len(selected_ids)}/{len(roster)} học sinh • {ready_count} khuôn mặt sẵn sàng')
            self.overview_attendance_summary.setText(summary_text)
            self.attendance_prepare_button.setText('EDIT' if self.ui_language == 'en' else 'SỬA ĐIỂM DANH')
            self.overview_ready_badge.setText(('READY TO SCAN' if ready_count else 'FACE ID REQUIRED') if self.ui_language == 'en' else
                                               ('SẴN SÀNG QUÉT' if ready_count else 'CẦN FACE ID'))
        else:
            self.overview_ready_badge.setText('READY TO PREPARE' if self.ui_language == 'en' else 'SẴN SÀNG CHUẨN BỊ')
        if hasattr(self, 'overview_start_button'):
            can_start = bool(selected_ids) and bool(ready_count) and getattr(self, 'current_session', None) is None
            self.overview_start_button.setEnabled(can_start)
            if hasattr(self, 'overview_action_bar'):
                self.overview_action_bar.setStyleSheet(
                    'QFrame#primaryActionBar { background:#087bfa; border:1px solid #087bfa; border-radius:14px; }'
                    if can_start else
                    'QFrame#primaryActionBar { background:#c9def8; border:1px solid #c9def8; border-radius:14px; }'
                )
                if hasattr(self, 'overview_auto_stop_label') and not can_start:
                    self.overview_auto_stop_label.setStyleSheet('color:#31577f;font-weight:800;background:transparent;')
                elif hasattr(self, 'overview_auto_stop_label'):
                    self.overview_auto_stop_label.setStyleSheet('color:#ffffff;font-weight:800;background:transparent;')
        self._update_overview_auto_stop_caption()
        for record in getattr(self, 'overview_student_cards', []):
            selected = record['id'] in selected_ids
            record['widget'].setObjectName('studentCardSelected' if selected else 'studentCard')
            record['widget'].style().unpolish(record['widget'])
            record['widget'].style().polish(record['widget'])
            record['widget'].update()

    def _start_from_overview(self):
        """Store the selected duration before the attendance/scan workflow starts."""
        if self._roster_future is not None:
            return
        try:
            custom_active = (
                int(self.scan_duration_combo.currentIndex()) == 4
                or bool(getattr(self, 'custom_duration_checkbox', None) and self.custom_duration_checkbox.isChecked())
            )
            if custom_active:
                minutes = int((self.custom_scan_minutes.text() or '').strip())
                if not 1 <= minutes <= 180:
                    raise ValueError
                seconds = minutes * 60
            else:
                seconds = int(self.scan_duration_combo.currentData() or 0)
                if not 60 <= seconds <= 180 * 60:
                    raise ValueError
            self.session_duration_seconds = max(60, min(180 * 60, seconds))
            self.settings.setValue('scan_duration_seconds', self.session_duration_seconds)
            self.settings.sync()
        except (TypeError, ValueError):
            QMessageBox.warning(
                self, 'God Eyes',
                'Enter a custom scan time from 1 to 180 minutes.' if self.ui_language == 'en' else
                'Hãy nhập thời lượng quét từ 1 đến 180 phút.'
            )
            return
        if self.attendance_selected_ids:
            if not self._ensure_server():
                QMessageBox.information(self, 'God Eyes', 'Connect to the Server first.' if self.ui_language == 'en' else 'Hãy kết nối máy chủ trước.')
                return
            self._start_face_scan_from_attendance()
        else:
            self._go_scan()

    def _prepare_attendance_from_overview(self):
        """Load this class roster and mark attendance without starting the scan yet."""
        if self.current_session is not None:
            QMessageBox.information(self, 'God Eyes', 'End the current session before changing attendance.' if self.ui_language == 'en' else 'Hãy kết thúc buổi hiện tại trước khi đổi điểm danh.')
            return
        if not self._ensure_server():
            QMessageBox.information(self, 'God Eyes', 'Connect to the Server first.' if self.ui_language == 'en' else 'Hãy kết nối máy chủ trước.')
            return
        if not self.server_selected_class_id:
            QMessageBox.information(self, 'God Eyes', 'Select a class first.' if self.ui_language == 'en' else 'Hãy chọn lớp học trước.')
            return
        if self._roster_future is not None:
            return
        self._attendance_only_flow = True
        self.overview_attendance_summary.setText('Loading student list…' if self.ui_language == 'en' else 'Đang tải danh sách học sinh…')
        self.attendance_prepare_button.setEnabled(False)
        if hasattr(self, 'overview_start_button'):
            self.overview_start_button.setEnabled(False)
        self._roster_future = self.server_executor.submit(self.server.get_roster, int(self.server_selected_class_id))
        self._async_start_timer.start()

    def page_camera(self):
        """Camera section: compact login, portal source badge, and live preview."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(26, 22, 26, 30)
        l.setSpacing(13)
        en = self.ui_language == 'en'

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(_godeyes_line_icon('camera', 22))
        icon.setObjectName('headerIcon')
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(42, 44)
        header_row.addWidget(icon)
        heading = QWidget()
        heading_layout = QVBoxLayout(heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(3)
        heading_layout.addWidget(self._label('Camera', 'title'))
        heading_layout.addWidget(self._label(
            'Connect to the camera selected in Teacher Portal.' if en else
            'Kết nối camera đã chọn trên Cổng Giáo viên.', 'muted'))
        header_row.addWidget(heading, 1)
        self.camera_source_label = self._label(
            self._camera_source_display(self.camera_source, self.camera_device_index, self.camera_host), 'pill')
        self.camera_source_label.setWordWrap(True)
        header_row.addWidget(self.camera_source_label, 0, Qt.AlignVCenter)
        l.addWidget(header)

        # Preserve hidden configuration widgets expected by the existing camera
        # code; the main screen shows no IP/host/port/stream fields.
        internal = QWidget()
        internal_layout = QVBoxLayout(internal)
        internal_layout.setContentsMargins(0, 0, 0, 0)
        self.camera_source_combo = QComboBox()
        self.camera_source_combo.addItem('Built-in camera', 'webcam')
        self.camera_source_combo.addItem('USB camera', 'usb')
        self.camera_source_combo.addItem('Wi-Fi / RTSP', 'wifi_camera')
        self.camera_source_combo.setCurrentIndex({'webcam': 0, 'usb': 1, 'wifi_camera': 2}.get(self.camera_source, 0))
        internal_layout.addWidget(self.camera_source_combo)
        self.camera_device_index_edit = QLineEdit(str(self.camera_device_index))
        self.camera_host_edit = QLineEdit(self.camera_host)
        self.camera_port_edit = QLineEdit(str(self.camera_port or 554))
        self.camera_stream_config_combo = QComboBox()
        self.camera_stream_config_combo.addItem('Stream 1', 'stream1')
        self.camera_stream_config_combo.addItem('Stream 2', 'stream2')
        self.camera_stream_config_combo.setCurrentIndex(1 if self.camera_stream == 'stream2' else 0)
        self.camera_brand_combo = QComboBox()
        self.camera_brand_combo.addItem('Tapo', 'tapo')
        for widget in (self.camera_device_index_edit, self.camera_host_edit, self.camera_port_edit,
                       self.camera_stream_config_combo, self.camera_brand_combo):
            internal_layout.addWidget(widget)
        internal.setVisible(False)
        l.addWidget(internal)

        credentials = self._card()
        credentials_layout = QVBoxLayout(credentials)
        credentials_layout.setContentsMargins(17, 14, 17, 14)
        credentials_layout.setSpacing(10)
        credentials_header = QHBoxLayout()
        credentials_header.addWidget(self._label('Camera login' if en else 'Đăng nhập camera', 'section'), 1)
        self.camera_credentials_title = self._label('Camera credentials' if en else 'Tài khoản camera', 'muted')
        credentials_header.addWidget(self.camera_credentials_title)
        credentials_layout.addLayout(credentials_header)

        self.camera_wifi_fields = QWidget()
        wifi_layout = QHBoxLayout(self.camera_wifi_fields)
        wifi_layout.setContentsMargins(0, 0, 0, 0)
        wifi_layout.setSpacing(10)
        username_field = QWidget()
        username_layout = QVBoxLayout(username_field)
        username_layout.setContentsMargins(0, 0, 0, 0)
        username_layout.setSpacing(5)
        username_layout.addWidget(self._label('Username' if en else 'Tài khoản', 'muted'))
        self.camera_username_edit = QLineEdit(self.camera_username)
        self.camera_username_edit.setMinimumHeight(42)
        self.camera_username_edit.setPlaceholderText('Username' if en else 'Nhập tài khoản camera')
        username_layout.addWidget(self.camera_username_edit)
        wifi_layout.addWidget(username_field, 1)

        password_field = QWidget()
        password_layout = QVBoxLayout(password_field)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.setSpacing(5)
        password_layout.addWidget(self._label('Password' if en else 'Mật khẩu', 'muted'))
        password_input_row = QHBoxLayout()
        password_input_row.setContentsMargins(0, 0, 0, 0)
        password_input_row.setSpacing(5)
        self.camera_password_edit = QLineEdit(self.camera_password)
        self.camera_password_edit.setMinimumHeight(42)
        self.camera_password_edit.setEchoMode(QLineEdit.Password)
        self.camera_password_edit.setPlaceholderText('Password' if en else 'Nhập mật khẩu camera')
        password_input_row.addWidget(self.camera_password_edit, 1)
        self.camera_password_toggle_button = QPushButton()
        self.camera_password_toggle_button.setIcon(QIcon(_godeyes_line_icon('eye', 18)))
        self.camera_password_toggle_button.setIconSize(QSize(18, 18))
        self.camera_password_toggle_button.setObjectName('minorAction')
        self.camera_password_toggle_button.setFixedSize(42, 42)
        self.camera_password_toggle_button.setToolTip('Show or hide password' if en else 'Hiện hoặc ẩn mật khẩu')
        self.camera_password_toggle_button.clicked.connect(self._toggle_camera_password_visibility)
        password_input_row.addWidget(self.camera_password_toggle_button)
        password_layout.addLayout(password_input_row)
        wifi_layout.addWidget(password_field, 1)
        credentials_layout.addWidget(self.camera_wifi_fields)

        self.camera_config_connect_button = QPushButton('🔗  CONNECT CAMERA' if en else '↗  KẾT NỐI CAMERA')
        self.camera_config_connect_button.setObjectName('primary')
        self.camera_config_connect_button.setFixedSize(300, 44)
        self.camera_config_connect_button.clicked.connect(self._connect_camera_from_config)
        credentials_layout.addWidget(self.camera_config_connect_button, 0, Qt.AlignLeft)

        status_frame = QFrame()
        status_frame.setObjectName('connectionStatus')
        self.camera_connection_frame = status_frame
        status_row = QHBoxLayout(status_frame)
        status_row.setContentsMargins(12, 8, 12, 8)
        status_row.setSpacing(8)
        status_dot = QLabel('●')
        self.camera_connection_dot = status_dot
        status_dot.setStyleSheet('color:#d08a00;background:transparent;font-size:15px;')
        status_row.addWidget(status_dot)
        self.camera_config_status = self._label('Camera is not connected.' if en else 'Camera chưa kết nối.', 'muted')
        self.camera_config_status.setWordWrap(True)
        self.camera_config_status.setStyleSheet('background:transparent;border:0;padding:0;color:#926300;font-weight:800;')
        status_row.addWidget(self.camera_config_status, 1)
        credentials_layout.addWidget(status_frame)
        self.camera_pref_status = self.camera_config_status
        l.addWidget(credentials)

        preview_card = self._card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(13, 13, 13, 13)
        preview_layout.setSpacing(8)
        preview_header = QHBoxLayout()
        preview_header.addWidget(self._label('Live camera preview' if en else 'Hình ảnh camera trực tiếp', 'section'), 1)
        self.camera_preview_badge = self._label('● LIVE' if en else '● TRỰC TIẾP', 'liveBadge')
        preview_header.addWidget(self.camera_preview_badge)
        preview_layout.addLayout(preview_header)
        self.camera_settings_preview = self._label(
            'Connect the camera to view the live classroom.' if en else 'Kết nối camera để xem hình ảnh lớp học trực tiếp.', 'camera')
        self.camera_settings_preview.setAlignment(Qt.AlignCenter)
        self.camera_settings_preview.setMinimumHeight(390)
        self.camera_settings_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.camera_settings_preview, 1)
        l.addWidget(preview_card, 1)

        self.cam_preview = self.camera_settings_preview
        self.cam_status = self.camera_config_status
        self.camera_config_source_label = self.camera_source_label
        self.camera_audio_test_status = self._label('', 'muted')
        self.camera_audio_test_button = QPushButton('')
        self.camera_audio_test_button.setVisible(False)
        self._refresh_camera_settings_visibility()
        self._update_camera_connection_summary()
        return self._scrollable_page(w)

    def _toggle_camera_password_visibility(self):
        """Reveal or mask the camera password without changing any saved credentials."""
        edit = getattr(self, 'camera_password_edit', None)
        if edit is not None:
            edit.setEchoMode(QLineEdit.Normal if edit.echoMode() == QLineEdit.Password else QLineEdit.Password)


    def page_scan(self):
        w=QWidget(); w.setObjectName('scanPage'); l=QVBoxLayout(w); l.setContentsMargins(28,22,28,22); l.setSpacing(12)
        l.addWidget(self._head('Student Scan','Danh sách điểm danh chỉ chọn một lần ở đầu buổi. God Eyes sẽ quét mọi người xuất hiện trong camera và chỉ đối chiếu với nhóm đã chọn.'))

        step = self._card(); sl=QHBoxLayout(step); sl.setContentsMargins(18,12,18,12); sl.setSpacing(10)
        self.scan_step1=self._label('1  ĐIỂM DANH','pill'); self.scan_step2=self._label('2  QUÉT ĐỘ TẬP TRUNG','pill'); self.scan_step3=self._label('3  LỊCH SỬ','pill')
        sl.addWidget(self.scan_step1); sl.addWidget(self.scan_step2); sl.addWidget(self.scan_step3); sl.addStretch(); l.addWidget(step)

        row=QHBoxLayout(); c=self._card(); b=QVBoxLayout(c); b.setContentsMargins(14,14,14,14); self.scan_preview=self._label('SẴN SÀNG QUÉT','camera'); self.scan_preview.setAlignment(Qt.AlignCenter); self.scan_preview.setMinimumHeight(500); b.addWidget(self.scan_preview); row.addWidget(c,3)
        side=self._card(); side.setMinimumWidth(390); side.setMaximumWidth(430)
        sb=QVBoxLayout(side); sb.setContentsMargins(20,20,20,20); sb.setSpacing(12)
        self.scan_class=self._label('Chưa chọn lớp','section'); sb.addWidget(self.scan_class)
        self.attendance_summary=self._label('Chưa chọn học sinh','muted'); self.attendance_summary.setWordWrap(True); sb.addWidget(self.attendance_summary)
        self.scan_count=self._label('0 người được nhận diện','section'); sb.addWidget(self.scan_count)
        self.scan_table=QTableWidget(0,3); self.scan_table.setHorizontalHeaderLabels(['Học sinh','Trạng thái','Khớp']); self.scan_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); self.scan_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents); self.scan_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents); self.scan_table.setWordWrap(True); self.scan_table.setEditTriggers(QAbstractItemView.NoEditTriggers); sb.addWidget(self.scan_table,1)
        self.scan_state=self._label('SẴN SÀNG','muted'); self.scan_state.setWordWrap(True); sb.addWidget(self.scan_state)
        self.scan_start=QPushButton('BẮT ĐẦU QUÉT KHUÔN MẶT  →'); self.scan_start.setObjectName('primary'); self.scan_start.setMinimumSize(250,48); self.scan_start.clicked.connect(self._start_scan_button); sb.addWidget(self.scan_start)
        self.lock_button=QPushButton('XÁC NHẬN DANH TÍNH  →'); self.lock_button.setObjectName('primary'); self.lock_button.setMinimumSize(250,44); self.lock_button.setEnabled(False); self.lock_button.clicked.connect(self._lock_ids); sb.addWidget(self.lock_button)
        change=QPushButton('Xem danh sách đã chọn'); change.clicked.connect(self._show_current_selection); sb.addWidget(change)
        row.addWidget(side,1); l.addLayout(row,1); return w


    # [AI-GENERATED / AI-ASSISTED CODE] Live Monitor UI.
    def page_monitor(self):
        w = QWidget()
        w.setObjectName('monitorPage')
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(7)

        # Compact header: keep only the essential title + session timer.
        # The camera area gets almost all remaining vertical space.
        header = QHBoxLayout()
        header.setSpacing(6)
        header_widget = self._label('Giám sát trực tiếp', 'title')
        header_widget.setStyleSheet('font-size:20px; font-weight:850;')
        header.addWidget(header_widget, 0, Qt.AlignVCenter)
        header.addStretch()
        self.timer_label = self._label('00:00 / 45:00', 'section')
        self.timer_label.setStyleSheet('font-size:11px; font-weight:800;')
        header.addWidget(self.timer_label, 0, Qt.AlignVCenter)
        header_container = QWidget()
        header_container.setLayout(header)
        header_container.setFixedHeight(34)
        l.addWidget(header_container, 0)

        body = QHBoxLayout()
        body.setSpacing(8)

        # Main camera area gets the remaining width.
        camera_card = self._card()
        cb = QVBoxLayout(camera_card)
        cb.setContentsMargins(8, 8, 8, 8)
        cb.setSpacing(4)

        camera_bar = QHBoxLayout()
        camera_bar.setSpacing(6)
        camera_bar.addWidget(self._label('CAMERA', 'eyebrow'))
        camera_bar.addStretch()
        self.mon_camera_status = self._label('AI ONLINE', 'status_success')
        self.mon_camera_status.setStyleSheet(
            'font-size:9px; padding:3px 7px;'
        )
        camera_bar.addWidget(self.mon_camera_status)
        cb.addLayout(camera_bar)

        self.mon_preview = self._label('ĐANG CHỜ HÌNH ẢNH', 'camera')
        self.mon_preview.setAlignment(Qt.AlignCenter)
        self.mon_preview.setMinimumHeight(300)
        self.mon_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        cb.addWidget(self.mon_preview, 1)

        # The camera is the primary workspace: let it expand to consume all
        # available monitor height.
        camera_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        body.addWidget(camera_card, 1)

        # Balanced right panel: wide enough for every button and label,
        # but compact enough to preserve the camera area.
        side = self._card()
        side.setFixedWidth(336)
        # Let the right control panel extend to the bottom of the monitor
        # workspace while keeping its contents compact at the top.
        side.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding
        )

        sb = QVBoxLayout(side)
        sb.setContentsMargins(12, 12, 12, 12)
        sb.setSpacing(7)

        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        status_label = self._label('SESSION STATUS', 'eyebrow')
        status_label.setStyleSheet('font-size:9px;')
        status_row.addWidget(status_label)
        status_row.addStretch()
        self.mon_status = self._label('AI ONLINE', 'status_success')
        self.mon_status.setStyleSheet(
            'font-size:9px; padding:3px 7px;'
        )
        status_row.addWidget(self.mon_status)
        sb.addLayout(status_row)

        # Two compact metric cards.
        metrics = QHBoxLayout()
        metrics.setSpacing(5)

        student_card = QFrame()
        student_card.setObjectName('metricCard')
        sm = QVBoxLayout(student_card)
        sm.setContentsMargins(8, 6, 8, 6)
        sm.setSpacing(1)
        sm.addWidget(self._label('Học sinh', 'muted'))
        self.mon_students = self._label('0 / 0', 'metric')
        self.mon_students.setStyleSheet(
            'font-size:16px; font-weight:850; padding:0;'
        )
        self.mon_students.setMaximumHeight(28)
        sm.addWidget(self.mon_students)

        event_card = QFrame()
        event_card.setObjectName('metricCard')
        em = QVBoxLayout(event_card)
        em.setContentsMargins(8, 6, 8, 6)
        em.setSpacing(1)
        em.addWidget(self._label('Quan sát', 'muted'))
        self.mon_events = self._label('0', 'metric')
        self.mon_events.setStyleSheet(
            'font-size:16px; font-weight:850; padding:0;'
        )
        self.mon_events.setMaximumHeight(28)
        em.addWidget(self.mon_events)

        metrics.addWidget(student_card, 1)
        metrics.addWidget(event_card, 1)
        sb.addLayout(metrics)

        obs_title = QHBoxLayout()
        obs_title.setSpacing(6)
        obs_title.addWidget(self._label('Quan sát gần nhất', 'section'))
        obs_title.addStretch()
        sb.addLayout(obs_title)

        # GOD EYES UI FIX V15: keep the face-box grace and expand Latest Observations.
        self.live = QListWidget()
        # The observation panel is the flexible region: it grows into the spare
        # vertical space above the controls. Keep the dBFS card compact below.
        self.live.setMinimumHeight(180)
        self.live.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.live.setWordWrap(True)
        self.live.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.live.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.live.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.live.setSpacing(4)
        self.live.setStyleSheet("""
            QListWidget {
                background: #ffffff;
                border: 1px solid #d9e4ef;
                border-radius: 10px;
                padding: 6px;
            }
            QListWidget::item {
                background: #f7fbff;
                border: 1px solid #dbe8f4;
                border-radius: 8px;
                padding: 7px 8px;
                margin: 0;
                color: #203247;
            }
            QListWidget::item:hover {
                background: #eef7ff;
            }
        """)
        self._reset_live_observations()
        sb.addWidget(self.live, 1)

        # Keep all four controls fully visible underneath the expanded
        # observations list.
        controls = QGridLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(6)

        self.pause = QPushButton('Tạm dừng giám sát')
        self.resume = QPushButton('Tiếp tục giám sát')
        self.resume.setEnabled(False)
        self.reset = QPushButton('Đặt lại nhận diện')
        self.leave = QPushButton('Kết thúc buổi học')
        self.leave.setObjectName('danger')

        for button in (self.pause, self.resume, self.reset, self.leave):
            button.setMinimumWidth(0)
            button.setFixedHeight(34)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed
            )
            button.setStyleSheet(
                'font-size:10px; padding:5px 7px; border-radius:8px;'
            )

        self.pause.clicked.connect(self._pause)
        self.resume.clicked.connect(self._resume)
        self.reset.clicked.connect(self._reset_ids)
        self.leave.clicked.connect(self._end_session)

        controls.addWidget(self.pause, 0, 0)
        controls.addWidget(self.resume, 0, 1)
        controls.addWidget(self.reset, 1, 0)
        controls.addWidget(self.leave, 1, 1)
        controls.setColumnStretch(0, 1)
        controls.setColumnStretch(1, 1)
        sb.addLayout(controls)

        # Keep dBFS as a compact, separate card below the expanding observation
        # panel. The user asked for the observation list—not this card—to fill
        # the available vertical space.
        # Values are dBFS (relative digital microphone level), not calibrated
        # physical dB SPL.
        self.audio_card = QFrame()
        self.audio_card.setObjectName('metricCard')
        self.audio_card.setFixedHeight(190)
        self.audio_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        audio_box = QVBoxLayout(self.audio_card)
        audio_box.setContentsMargins(14, 12, 14, 14)
        audio_box.setSpacing(9)

        # Keep the dBFS unit pill inside the main sound-card header, not in a
        # separate card or floating column.
        audio_head = QHBoxLayout()
        audio_head.setSpacing(8)
        audio_title = self._label('MỨC ÂM THANH TRONG LỚP', 'section')
        audio_title.setWordWrap(True)
        audio_head.addWidget(audio_title, 1)
        audio_unit = self._label('dBFS', 'pill')
        audio_unit.setAlignment(Qt.AlignCenter)
        audio_unit.setFixedSize(58, 30)
        audio_head.addWidget(audio_unit, 0, Qt.AlignTop)
        audio_box.addLayout(audio_head)

        self.audio_current_label = self._label('Hiện tại: — dBFS', 'metric')
        self.audio_current_label.setStyleSheet('font-size:17px;font-weight:850;padding:0;')
        audio_box.addWidget(self.audio_current_label)

        # Compact internal spacing keeps every audio metric visible without
        # making the dBFS card consume the whole panel.
        audio_box.addStretch(1)
        self.audio_level_bar = QProgressBar()
        self.audio_level_bar.setRange(0, 60)
        self.audio_level_bar.setValue(0)
        self.audio_level_bar.setTextVisible(False)
        self.audio_level_bar.setFixedHeight(9)
        audio_box.addWidget(self.audio_level_bar)
        audio_box.addStretch(1)

        self.audio_average_label = self._label('Trung bình buổi học: — dBFS', 'section')
        self.audio_average_label.setStyleSheet('font-size:11px;font-weight:800;padding:0;')
        audio_box.addWidget(self.audio_average_label)
        self.audio_status_label = self._label('Đang chờ microphone…', 'muted')
        self.audio_status_label.setWordWrap(True)
        self.audio_status_label.setStyleSheet('font-size:9px;padding:0;')
        audio_box.addWidget(self.audio_status_label)

        # Keep the compact dBFS card after the observation panel; only the
        # observation list receives the vertical stretch factor.
        sb.addWidget(self.audio_card, 0)

        body.addWidget(side, 0)
        # Give the camera/body the remaining vertical space.
        l.addLayout(body, 1)

        return w

    # Camera page: camera source is controlled by Teacher Portal preferences.
    def page_camera_config(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)
        layout.addWidget(self._head(
            'Camera',
            'Nguồn Built-in / USB / Wi-Fi RTSP được chọn từ Teacher Portal. Mật khẩu camera chỉ lưu trên máy này.'
        ))

        source_card = self._card()
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(18, 14, 18, 14)
        source_layout.setSpacing(5)
        source_layout.addWidget(self._label('NGUỒN CAMERA', 'eyebrow'))
        self.camera_source_combo = QComboBox()
        self.camera_source_combo.addItem('Camera tích hợp', 'webcam')
        self.camera_source_combo.addItem('Webcam USB', 'usb')
        self.camera_source_combo.addItem('Camera Wi-Fi / RTSP', 'wifi_camera')
        self.camera_source_combo.setCurrentIndex(
            {'webcam': 0, 'usb': 1, 'wifi_camera': 2}.get(self.camera_source, 0)
        )
        self.camera_source_combo.currentIndexChanged.connect(self._camera_source_changed)
        source_layout.addWidget(self.camera_source_combo)
        self.camera_config_source_label = self._label(
            self._camera_source_display(self.camera_source, self.camera_device_index, self.camera_host), 'section'
        )
        self.camera_config_source_label.setWordWrap(True)
        source_layout.addWidget(self.camera_config_source_label)
        source_layout.addWidget(self._label(
            'Để đổi loại camera, vào Teacher Portal và chọn Built-in, USB hoặc Wi-Fi/RTSP. Ở đây nhập thông tin kết nối cho nguồn đã chọn.',
            'muted'
        ))
        layout.addWidget(source_card)

        config_card = self._card()
        config = QVBoxLayout(config_card)
        config.setContentsMargins(18, 16, 18, 16)
        config.setSpacing(10)

        self.camera_device_card = QWidget()
        dev_layout = QHBoxLayout(self.camera_device_card)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.addWidget(self._label('Chỉ số camera Built-in/USB', 'section'))
        self.camera_device_index_edit = QLineEdit(str(self.camera_device_index))
        self.camera_device_index_edit.setMaximumWidth(100)
        self.camera_device_index_edit.setPlaceholderText('0')
        dev_layout.addWidget(self.camera_device_index_edit)
        dev_layout.addStretch()
        config.addWidget(self.camera_device_card)

        self.camera_wifi_fields = QWidget()
        wifi_layout = QGridLayout(self.camera_wifi_fields)
        wifi_layout.setContentsMargins(0, 0, 0, 0)
        wifi_layout.setHorizontalSpacing(12)
        wifi_layout.setVerticalSpacing(8)
        def camera_field(label, widget, row, col, span=1):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            cell_layout.addWidget(self._label(label, 'muted'))
            cell_layout.addWidget(widget)
            wifi_layout.addWidget(cell, row, col, 1, span)

        self.camera_host_edit = QLineEdit(self.camera_host)
        self.camera_host_edit.setPlaceholderText('192.168.1.100')
        self.camera_port_edit = QLineEdit(str(self.camera_port or 554))
        self.camera_port_edit.setPlaceholderText('554')
        self.camera_stream_config_combo = QComboBox()
        self.camera_stream_config_combo.addItem('Stream 1 (main)', 'stream1')
        self.camera_stream_config_combo.addItem('Stream 2 (sub)', 'stream2')
        self.camera_stream_config_combo.setCurrentIndex(1 if self.camera_stream == 'stream2' else 0)
        self.camera_username_edit = QLineEdit(self.camera_username)
        self.camera_username_edit.setPlaceholderText('Tên đăng nhập camera')
        self.camera_password_edit = QLineEdit(self.camera_password)
        self.camera_password_edit.setEchoMode(QLineEdit.Password)
        self.camera_password_edit.setPlaceholderText('Mật khẩu camera')
        camera_field('IP / Host', self.camera_host_edit, 0, 0, 2)
        camera_field('Port RTSP', self.camera_port_edit, 0, 2)
        camera_field('Stream', self.camera_stream_config_combo, 0, 3)
        camera_field('Tên đăng nhập', self.camera_username_edit, 1, 0, 2)
        camera_field('Mật khẩu', self.camera_password_edit, 1, 2, 2)
        config.addWidget(self.camera_wifi_fields)

        buttons = QHBoxLayout()
        self.camera_config_save_button = QPushButton('LƯU CẤU HÌNH')
        self.camera_config_save_button.clicked.connect(self._save_camera_config_from_page)
        self.camera_config_connect_button = QPushButton('KẾT NỐI / KIỂM TRA CAMERA')
        self.camera_config_connect_button.setObjectName('primary')
        self.camera_config_connect_button.clicked.connect(self._connect_camera_from_config)
        self.camera_config_refresh_button = QPushButton('ĐỒNG BỘ TEACHER PORTAL')
        self.camera_config_refresh_button.clicked.connect(lambda: self._load_teacher_preferences_from_server(show_status=True))
        self.camera_audio_test_button = QPushButton('KIỂM TRA ÂM THANH RTSP')
        self.camera_audio_test_button.clicked.connect(self._test_camera_audio_from_config)
        buttons.addWidget(self.camera_config_save_button)
        buttons.addWidget(self.camera_config_connect_button)
        buttons.addWidget(self.camera_config_refresh_button)
        buttons.addWidget(self.camera_audio_test_button)
        config.addLayout(buttons)
        self.camera_config_status = self._label('Chưa kết nối camera.', 'muted')
        self.camera_config_status.setWordWrap(True)
        self.camera_pref_status = self.camera_config_status
        config.addWidget(self.camera_config_status)
        self.camera_audio_test_status = self._label('Âm thanh camera chưa được kiểm tra.', 'muted')
        self.camera_audio_test_status.setWordWrap(True)
        config.addWidget(self.camera_audio_test_status)
        layout.addWidget(config_card)

        preview_card = self._card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(5)
        preview_layout.addWidget(self._label('XEM TRƯỚC CAMERA', 'eyebrow'))
        self.camera_settings_preview = self._label('Nhấn “Kết nối / Kiểm tra Camera” để xem trước.', 'camera')
        self.camera_settings_preview.setAlignment(Qt.AlignCenter)
        self.camera_settings_preview.setMinimumHeight(180)
        self.camera_settings_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.camera_settings_preview, 1)
        layout.addWidget(preview_card, 1)
        next_row = QHBoxLayout()
        next_row.addStretch()
        next_button = QPushButton('TIẾP TỤC QUÉT FACE ID  →')
        next_button.setObjectName('primary')
        next_button.clicked.connect(self._go_scan)
        next_row.addWidget(next_button)
        layout.addLayout(next_row)
        self._refresh_camera_settings_visibility()
        return w

    def _save_camera_config_from_page(self):
        try:
            idx = max(0, min(9, int(self.camera_device_index_edit.text().strip() or '0')))
            host = self.camera_host_edit.text().strip()
            port = max(1, min(65535, int(self.camera_port_edit.text().strip() or '554')))
        except ValueError:
            self.camera_config_status.setText('The port or camera index is invalid.' if self.ui_language == 'en' else 'Port hoặc chỉ số camera không hợp lệ.')
            return False
        source = str(self.camera_source_combo.currentData() or self.camera_source)
        if source in ('tapo', 'tapo_c230', 'tapo_c230_rtsp', 'rtsp', 'wifi', 'wifi/rtsp', 'wifi_rtsp'):
            source = 'wifi_camera'
        elif source in ('built_in', 'built-in', 'builtin', 'built_in_camera', 'built-in camera', 'integrated', 'computer_camera'):
            source = 'webcam'
        elif source in ('usb_camera', 'usb webcam', 'external_usb'):
            source = 'usb'
        stream = str(self.camera_stream_config_combo.currentData() or 'stream1')
        username = self.camera_username_edit.text().strip()
        password = self.camera_password_edit.text()
        self.camera_device_index = idx
        self.camera_host, self.camera_port, self.camera_stream = host, port, stream
        self.camera_username, self.camera_password = username, password
        self.settings.setValue('camera_device_index', idx)
        self.settings.setValue('camera_host', host)
        self.settings.setValue('camera_port', port)
        self.settings.setValue('camera_stream', stream)
        self.settings.setValue('camera_username', username)
        self.settings.setValue('camera_password_local', password)
        self.settings.sync()
        self.camera_config_source_label.setText(self._camera_source_display(source, idx, host))
        if self.server.connected:
            try:
                response = self._save_teacher_preferences_remote(
                    self.ui_language, self.ui_theme,
                    {'camera_source': source, 'camera_brand': self.camera_brand,
                     'camera_device_index': idx, 'camera_host': host, 'camera_port': port,
                     'camera_stream': stream, 'camera_username': username}
                )
                if isinstance(response, dict):
                    self._apply_camera_preferences(response, save_local=True)
                # The password is intentionally local-only and never sent to the Server.
                self.camera_password = password
                self.settings.setValue('camera_password_local', password)
                self.settings.sync()
                self.camera_config_status.setText('Configuration saved and synced with Teacher Portal.' if self.ui_language == 'en' else 'Đã lưu cấu hình và đồng bộ Teacher Portal.')
            except Exception as exc:
                self.camera_config_status.setText((f'Saved locally; Server sync failed: {exc}') if self.ui_language == 'en' else (f'Đã lưu trên máy; chưa đồng bộ được Server: {exc}'))
        else:
            self.camera_config_status.setText('Saved on this computer. Sign in to the Server to sync with Teacher Portal.' if self.ui_language == 'en' else 'Đã lưu trên máy này. Đăng nhập Server để đồng bộ với Teacher Portal.')
        self._refresh_camera_settings_visibility()
        return True

    def _connect_camera_from_config(self):
        """Connect using Teacher Portal's selected camera type and local credentials."""
        self.camera_username = self.camera_username_edit.text().strip() if hasattr(self, 'camera_username_edit') else self.camera_username
        self.camera_password = self.camera_password_edit.text() if hasattr(self, 'camera_password_edit') else self.camera_password
        self.settings.setValue('camera_username', self.camera_username)
        self.settings.setValue('camera_password_local', self.camera_password)
        self.settings.sync()
        source = str(self.camera_source or 'webcam')
        if source == 'wifi_camera' and (not self.camera_host or not self.camera_username or not self.camera_password):
            if not self.camera_username or not self.camera_password:
                QMessageBox.information(
                    self, 'God Eyes',
                    'Enter the camera username and password first.' if self.ui_language == 'en' else
                    'Hãy nhập tài khoản và mật khẩu camera trước.'
                )
                return
            self._open_camera_connection_dialog()
            return
        self._connect_camera()


    def _test_camera_audio_from_config(self):
        """Test RTSP audio independently of Face ID / an active Monitor session."""
        if getattr(self, 'audio_monitor', None) is not None and self.audio_monitor.isRunning():
            self.camera_audio_test_status.setText(
                'Đang có phiên Monitor đọc âm thanh. Hãy kiểm tra mức dBFS trong mục Monitor.'
                if self.ui_language != 'en' else
                'The Monitor session is already reading audio. Check dBFS on the Monitor page.'
            )
            return

        if not self._save_camera_config_from_page():
            return
        if self.camera_source != 'wifi_camera':
            self.camera_audio_test_status.setText(
                'Hãy chọn “Camera Wi-Fi / RTSP” để kiểm tra âm thanh trực tiếp từ camera.'
                if self.ui_language != 'en' else
                'Select “Wi-Fi / RTSP camera” to test audio directly from the camera.'
            )
            return

        rtsp_url = self._build_camera_url(
            self.camera_source, self.camera_host, self.camera_port,
            self.camera_stream, self.camera_username, self.camera_password
        )
        if not rtsp_url:
            self.camera_audio_test_status.setText(
                'Thiếu IP/Host, tài khoản hoặc mật khẩu camera. Hãy điền đủ rồi bấm LƯU CẤU HÌNH.'
                if self.ui_language != 'en' else
                'Camera host, username or password is missing. Fill them in and save the configuration.'
            )
            return

        self._stop_camera_audio_test(show_status=False)
        self.camera_audio_test_power_seen = False
        self.camera_audio_test_had_error = False
        worker = AudioLevelWorker(self, rtsp_url=rtsp_url)
        worker.level_power.connect(self._camera_audio_test_level_received)
        worker.audio_error.connect(self._camera_audio_test_error)
        self.camera_audio_test_worker = worker
        self.camera_audio_test_status.setText(
            'Đang kiểm tra RTSP audio trong tối đa 15 giây…'
            if self.ui_language != 'en' else
            'Testing RTSP audio for up to 15 seconds…'
        )
        worker.start()
        QTimer.singleShot(17000, self._finish_camera_audio_test)

    def _camera_audio_test_level_received(self, power):
        try:
            power = max(0.0, float(power))
            dbfs = max(-120.0, min(0.0, 10.0 * math.log10(max(power, 1e-12))))
        except Exception:
            return
        self.camera_audio_test_power_seen = True
        if hasattr(self, 'camera_audio_test_status'):
            self.camera_audio_test_status.setText(
                f'ĐÃ NHẬN ĐƯỢC ÂM THANH TỪ CAMERA: {dbfs:.1f} dBFS  •  Hãy tạo âm thanh gần camera để xem chỉ số thay đổi.'
                if self.ui_language != 'en' else
                f'CAMERA AUDIO RECEIVED: {dbfs:.1f} dBFS  •  Make a sound near the camera and verify that the value changes.'
            )

    def _camera_audio_test_error(self, message):
        self.camera_audio_test_had_error = True
        details = str(message or '').strip()
        # Keep the useful diagnostic visible without making the page excessively tall.
        if len(details) > 1100:
            details = details[:1100] + '…'
        if hasattr(self, 'camera_audio_test_status'):
            self.camera_audio_test_status.setText(
                'KIỂM TRA ÂM THANH THẤT BẠI: ' + details
                if self.ui_language != 'en' else
                'CAMERA AUDIO TEST FAILED: ' + details
            )

    def _finish_camera_audio_test(self):
        worker = getattr(self, 'camera_audio_test_worker', None)
        if worker is None:
            return
        if not self.camera_audio_test_power_seen and not self.camera_audio_test_had_error:
            if hasattr(self, 'camera_audio_test_status'):
                self.camera_audio_test_status.setText(
                    'Không nhận được mẫu âm thanh trong thời gian kiểm tra. Kiểm tra Audio của camera và đường dẫn RTSP.'
                    if self.ui_language != 'en' else
                    'No audio samples arrived during the test. Check the camera Audio setting and RTSP path.'
                )
        self._stop_camera_audio_test(show_status=False)

    def _stop_camera_audio_test(self, show_status=False):
        worker = getattr(self, 'camera_audio_test_worker', None)
        if worker is None:
            return
        try:
            worker.stop()
            if worker.isRunning():
                worker.wait(1500)
        except Exception:
            pass
        if not worker.isRunning():
            self.camera_audio_test_worker = None
        if show_status and hasattr(self, 'camera_audio_test_status'):
            self.camera_audio_test_status.setText('Đã dừng kiểm tra âm thanh.' if self.ui_language != 'en' else 'Audio test stopped.')

    # [AI-GENERATED / AI-ASSISTED CODE] History UI and evidence controls.
    def page_history(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,28,34,28); l.setSpacing(14); l.addWidget(self._head('History','Every session is independent. Evidence được lưu cục bộ và đồng bộ lên God Eyes Server.'))
        c=self._card(); b=QVBoxLayout(c); self.hist=QTableWidget(0,6); self.hist.setHorizontalHeaderLabels(['Date','Duration','Status','Students','Events','Session']); self.hist.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.hist.setSelectionBehavior(QAbstractItemView.SelectRows); self.hist.setEditTriggers(QAbstractItemView.NoEditTriggers); b.addWidget(self.hist); l.addWidget(c,1)
        buttons=QHBoxLayout(); view=QPushButton('View Session & Evidence'); delete=QPushButton('Delete'); delete_all=QPushButton('Delete All'); delete_all.setObjectName('danger'); view.clicked.connect(self._view_selected); delete.clicked.connect(self._delete_selected); delete_all.clicked.connect(self._delete_all); buttons.addWidget(view); buttons.addWidget(delete); buttons.addWidget(delete_all); buttons.addStretch(); l.addLayout(buttons); return w

    # [AI-GENERATED / AI-ASSISTED CODE] Teacher UI preferences synced with Server.
    def page_settings(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,28,34,28); l.setSpacing(14)
        l.addWidget(self._head('Settings','Appearance, language and camera.'))
        pref=self._card(); p=QVBoxLayout(pref); p.setContentsMargins(22,20,22,20); p.setSpacing(12)
        p.addWidget(self._label('Appearance','section'));         self.theme_combo=QComboBox(); self.theme_combo.addItem('Light','light'); self.theme_combo.addItem('Dark','dark'); self.theme_combo.setCurrentIndex(1 if self.ui_theme=='dark' else 0); self.theme_combo.currentIndexChanged.connect(self._preview_theme_from_settings); p.addWidget(self.theme_combo)
        p.addWidget(self._label('Language','section'));         self.language_combo=QComboBox(); self.language_combo.addItem('Vietnamese','vi'); self.language_combo.addItem('English','en'); self.language_combo.setCurrentIndex(1 if self.ui_language=='en' else 0); self.language_combo.currentIndexChanged.connect(self._preview_language_from_settings); p.addWidget(self.language_combo)
        self.settings_save_btn=QPushButton('SAVE SETTINGS'); self.settings_save_btn.setObjectName('primary'); self.settings_save_btn.clicked.connect(self._save_teacher_preferences); p.addWidget(self.settings_save_btn,0,Qt.AlignLeft)
        self.settings_pref_status=self._label('','muted'); self.settings_pref_status.setWordWrap(True); p.addWidget(self.settings_pref_status); l.addWidget(pref)

        cam=self._card(); cb=QVBoxLayout(cam); cb.setContentsMargins(22,20,22,20); cb.setSpacing(11)
        cb.addWidget(self._label('Camera','section'));         cb.addWidget(self._label('Camera source','muted'))
        self.camera_source_combo=QComboBox(); self.camera_source_combo.addItem('Built-in computer camera','webcam'); self.camera_source_combo.addItem('External USB webcam','usb'); self.camera_source_combo.addItem('Wi-Fi Camera','wifi_camera'); self.camera_source_combo.setCurrentIndex({'webcam':0,'usb':1,'wifi_camera':2}.get(self.camera_source,0)); self.camera_source_combo.currentIndexChanged.connect(self._camera_source_changed); cb.addWidget(self.camera_source_combo)

        self.wifi_camera_panel=QFrame(); self.wifi_camera_panel.setObjectName('cameraSettingsPanel'); wp=QVBoxLayout(self.wifi_camera_panel); wp.setContentsMargins(16,16,16,16); wp.setSpacing(10)
        wp.addWidget(self._label('Wi-Fi Camera','section'))
        self.camera_brand_combo=QComboBox(); self.camera_brand_combo.addItem('Tapo','tapo'); self.camera_brand_combo.setCurrentIndex(0); self.camera_brand_combo.setEnabled(False); wp.addWidget(self.camera_brand_combo)
        self.camera_connection_label=self._label('Not connected','muted'); self.camera_connection_label.setWordWrap(True); wp.addWidget(self.camera_connection_label)
        self.camera_stream_combo=QComboBox(); self.camera_stream_combo.addItem('High quality','stream1'); self.camera_stream_combo.addItem('Standard quality','stream2'); self.camera_stream_combo.setCurrentIndex(0 if self.camera_stream=='stream1' else 1); wp.addWidget(self.camera_stream_combo)
        self.camera_connect_button=QPushButton('CONNECT CAMERA'); self.camera_connect_button.setObjectName('primary'); self.camera_connect_button.clicked.connect(self._open_camera_connection_dialog); wp.addWidget(self.camera_connect_button,0,Qt.AlignLeft)
        cb.addWidget(self.wifi_camera_panel)

        self.camera_legacy_index_label=self._label('Webcam index','muted'); cb.addWidget(self.camera_legacy_index_label); self.camera_index_edit=QLineEdit(str(self.camera_device_index)); self.camera_index_edit.setPlaceholderText('0'); cb.addWidget(self.camera_index_edit)
        self.camera_pref_status=self._label('','muted'); self.camera_pref_status.setWordWrap(True); cb.addWidget(self.camera_pref_status)
        l.addWidget(cam); l.addStretch(); self._refresh_camera_settings_visibility(); self._update_camera_connection_summary(); return w
    # [AI-GENERATED / AI-ASSISTED CODE] Applies synchronized UI language/theme preferences.
    def _apply_ui_preferences(self, language: str, theme: str, save_local: bool=True):
        language = _normalize_ui_language(language)
        theme = str(theme or 'light').lower()
        if theme not in ('light', 'dark'):
            theme = 'light'

        self.ui_language = language
        self.ui_theme = theme
        self.setWindowTitle(_translate_text('God Eyes', language))

        style = DARK_STYLE if theme == 'dark' else STYLE
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(style)
        self.setStyleSheet(style)

        if hasattr(self, 'logo_view'):
            try:
                self.logo_view.style().unpolish(self.logo_view)
                self.logo_view.style().polish(self.logo_view)
                self.logo_view.update()
            except Exception:
                pass
        if hasattr(self, 'server_badge'):
            connected = bool(getattr(self, 'server', None) and self.server.connected)
            self.server_badge.setText(('SERVER • CONNECTED' if connected else 'SERVER • DISCONNECTED') if language == 'en' else ('MÁY CHỦ • ĐÃ KẾT NỐI' if connected else 'MÁY CHỦ • CHƯA KẾT NỐI'))
        if hasattr(self, 'nav') and self.nav:
            self.nav[0][0].setText('Overview' if language == 'en' else 'Tổng quan')
        if hasattr(self, 'camera_nav_button'):
            self.camera_nav_button.setText('Camera')
        if hasattr(self, 'local_ai_label'):
            self.local_ai_label.setText('LOCAL AI • OFFLINE' if language == 'en' else 'AI CỤC BỘ • NGOẠI TUYẾN')
        if hasattr(self, 'overview_start_button'):
            self.overview_start_button.setText('▶   START SCAN' if language == 'en' else '▶   BẮT ĐẦU QUÉT')
        if hasattr(self, 'overview_auto_stop_label'):
            self._update_overview_auto_stop_caption()
        if hasattr(self, 'attendance_prepare_button'):
            self.attendance_prepare_button.setText(
                ('EDIT' if language == 'en' else 'SỬA ĐIỂM DANH') if getattr(self, 'attendance_selected_ids', set()) else
                ('ATTENDANCE' if language == 'en' else 'ĐIỂM DANH')
            )
        if hasattr(self, 'overview_student_search'):
            self.overview_student_search.setPlaceholderText('Search students…' if language == 'en' else 'Tìm kiếm học sinh…')
        if hasattr(self, 'overview_select_all_button'):
            self.overview_select_all_button.setText('☑  Select all' if language == 'en' else '☑  Chọn tất cả')
        if hasattr(self, 'overview_clear_all_button'):
            self.overview_clear_all_button.setText('☐  Clear all' if language == 'en' else '☐  Bỏ chọn tất cả')
        if hasattr(self, 'duration_buttons'):
            labels = (['15 min', '30 min', '45 min', '60 min', 'Custom'] if language == 'en' else
                      ['15 phút', '30 phút', '45 phút', '60 phút', 'Tùy chỉnh'])
            for index, button in enumerate(self.duration_buttons):
                if index < len(labels): button.setText(labels[index])
        if hasattr(self, 'overview_students_placeholder') and not getattr(self, 'server_roster', []):
            self.overview_students_placeholder.setText(
                'Select a class, then mark attendance to load students.' if language == 'en' else
                'Chọn lớp, sau đó bấm Điểm danh để tải danh sách học sinh.')
        if hasattr(self, 'overview_ready_badge'):
            ready = bool(getattr(self, 'selected_server_roster', []))
            selected = bool(getattr(self, 'attendance_selected_ids', set()))
            self.overview_ready_badge.setText(('READY TO SCAN' if ready else ('FACE ID REQUIRED' if selected else 'READY TO PREPARE')) if language == 'en' else
                                               ('SẴN SÀNG QUÉT' if ready else ('CẦN FACE ID' if selected else 'SẴN SÀNG CHUẨN BỊ')))
        if hasattr(self, 'camera_preview_badge'):
            connected = bool(self._camera_alive()) if hasattr(self, '_camera_alive') else False
            self.camera_preview_badge.setText(('LIVE • CONNECTED' if connected else 'LIVE • WAITING') if language == 'en' else
                                               ('TRỰC TIẾP • ĐÃ KẾT NỐI' if connected else 'TRỰC TIẾP • ĐANG CHỜ'))
        if hasattr(self, 'scan_duration_combo'):
            duration_names = {900: ('15 minutes', '15 phút'), 1800: ('30 minutes', '30 phút'),
                              2700: ('45 minutes', '45 phút'), 3600: ('60 minutes', '60 phút'),
                              0: ('Custom', 'Tùy chỉnh')}
            for combo_index in range(self.scan_duration_combo.count()):
                value = int(self.scan_duration_combo.itemData(combo_index) or 0)
                pair = duration_names.get(value)
                if pair:
                    self.scan_duration_combo.setItemText(combo_index, pair[0] if language == 'en' else pair[1])
        if hasattr(self, 'camera_source_label'):
            self.camera_source_label.setText(self._camera_source_display(self.camera_source, self.camera_device_index, self.camera_host))
        if hasattr(self, 'camera_credentials_title'):
            self._refresh_camera_settings_visibility()
        if hasattr(self, 'overview_auto_stop_label'):
            self._update_overview_auto_stop_caption()
        if hasattr(self, 'camera_username_edit'):
            self.camera_username_edit.setPlaceholderText('Username' if language == 'en' else 'Nhập tài khoản camera')
        if hasattr(self, 'camera_password_edit'):
            self.camera_password_edit.setPlaceholderText('Password' if language == 'en' else 'Nhập mật khẩu camera')
        if hasattr(self, 'class_combo') and hasattr(self, 'class_info'):
            self._populate_class_combo()
        if getattr(self, 'server_roster', None) and hasattr(self, 'overview_students_grid'):
            self._populate_overview_student_cards(self.server_roster)

        if save_local and hasattr(self, 'settings'):
            try:
                self.settings.setValue('ui_language', language)
                self.settings.setValue('ui_theme', theme)
                self.settings.sync()
            except Exception:
                pass

        try:
            _translate_runtime_widget_texts(self, language)
        except Exception:
            pass

        if hasattr(self, 'language_combo'):
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(1 if language == 'en' else 0)
            self.language_combo.blockSignals(False)
        if hasattr(self, 'theme_combo'):
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(1 if theme == 'dark' else 0)
            self.theme_combo.blockSignals(False)
        if hasattr(self, 'server_user_edit'):
            try:
                self.server_user_edit.setText(
                    self.server_username or (self.server_profile or {}).get('username', '')
                )
            except Exception:
                pass

    def _preview_theme_from_settings(self, _index):
        if hasattr(self,'theme_combo'): self._apply_ui_preferences(self.ui_language,self.theme_combo.currentData(),save_local=False)

    def _preview_language_from_settings(self, _index):
        if hasattr(self,'language_combo'): self._apply_ui_preferences(self.language_combo.currentData(),self.ui_theme,save_local=False)

    def _get_teacher_preferences_remote(self):
        getter=getattr(self.server,'get_teacher_preferences',None)
        if callable(getter): return getter()
        data=self.server._request('GET','/api/v1/teacher/preferences')
        if not isinstance(data,dict): raise ServerClientError('Server did not return valid teacher settings.')
        return data

    def _save_teacher_preferences_remote(self, language: str, theme: str, camera: dict):
        saver=getattr(self.server,'save_teacher_preferences',None)
        if callable(saver): return saver(language,theme,camera)
        payload={'language':language,'theme':theme}; payload.update({k:v for k,v in camera.items() if k in {'camera_source','camera_brand','camera_device_index','camera_host','camera_port','camera_stream','camera_username'}})
        data=self.server._request('POST','/api/v1/teacher/preferences',self.server._json_bytes(payload))
        if not isinstance(data,dict): raise ServerClientError('Server did not save teacher settings.')
        return data

    def _load_teacher_preferences_from_server(self, show_status=False):
        if not self.server.connected:return False
        try:
            data=self._get_teacher_preferences_remote()
            self._apply_ui_preferences(str(data.get('language','vi')).lower(),str(data.get('theme','light')).lower(),save_local=True)
            self._apply_camera_preferences(data,save_local=True)
            if show_status:
                status_widget = getattr(self, 'camera_config_status', None) or getattr(self, 'settings_pref_status', None)
                if status_widget is not None:
                    status_widget.setText('Server settings synced.' if self.ui_language == 'en' else 'Cài đặt Server đã đồng bộ.')
            return True
        except Exception as exc:
            self.server_sync_text=f'PREFERENCES SYNC ERROR • {exc}'
            if show_status:
                status_widget = getattr(self, 'camera_config_status', None) or getattr(self, 'settings_pref_status', None)
                if status_widget is not None:
                    status_widget.setText(f'Unable to sync settings • {exc}' if self.ui_language == 'en' else f'Không đồng bộ được cài đặt • {exc}')
            return False

    def _save_teacher_preferences(self):
        if not self.server.connected:
            self.settings_pref_status.setText('Server not connected.' if self.ui_language=='en' else 'Server chưa kết nối.'); return
        language=str(self.language_combo.currentData()); theme=str(self.theme_combo.currentData())
        source,idx,host,port,stream,username,password=self._camera_config_from_widgets()
        camera={'camera_source':source,'camera_brand':self.camera_brand,'camera_device_index':idx,'camera_host':host,'camera_port':port,'camera_stream':stream,'camera_username':username}
        try:
            data=self._save_teacher_preferences_remote(language,theme,camera)
            self._apply_ui_preferences(data.get('language',language),data.get('theme',theme),save_local=True)
            self._apply_camera_preferences(data,save_local=True)
            self.settings.setValue('camera_password_local',password); self.settings.sync(); self.camera_password=password
            self.settings_pref_status.setText('Saved to your Teacher account.' if self.ui_language=='en' else 'Đã lưu vào tài khoản giáo viên.')
            self._load_teacher_preferences_from_server(show_status=False)
        except Exception as exc:
            self.settings_pref_status.setText(f'Unable to save settings • {exc}')

    def _schedule_teacher_preferences_sync(self):
        """Fetch Teacher Portal preferences in the background when it is safe to apply them."""
        if not self.server.connected or self.current_session is not None:
            return
        future = self._teacher_preferences_future
        if future is not None and not future.done():
            return
        try:
            self._teacher_preferences_future = self.server_executor.submit(self._get_teacher_preferences_remote)
        except Exception:
            self._teacher_preferences_future = None

    def _poll_teacher_preferences_sync(self):
        future = self._teacher_preferences_future
        if future is None or not future.done():
            return
        self._teacher_preferences_future = None
        if self.current_session is not None or not self.server.connected:
            return
        try:
            data = future.result()
            if not isinstance(data, dict):
                return
            language = _normalize_ui_language(data.get('language', self.ui_language))
            theme = str(data.get('theme', self.ui_theme) or self.ui_theme).lower()
            if language != self.ui_language or theme != self.ui_theme:
                self._apply_ui_preferences(language, theme, save_local=True)
            self._apply_camera_preferences(data, save_local=True)
        except Exception:
            # Background sync is best-effort. The next scheduled poll retries it.
            pass

    def _sync_teacher_preferences(self):
        if self.server.connected:
            self._load_teacher_preferences_from_server(show_status=True)

    def _set_nav_state(self,idx):
        for b,page_idx in self.nav:
            active = page_idx == idx
            b.setObjectName('nav_active' if active else 'nav')
            b.style().unpolish(b)
            b.style().polish(b)
        if hasattr(self, 'camera_nav_button'):
            active = idx == 1
            self.camera_nav_button.setObjectName('nav_active' if active else 'nav')
            self.camera_nav_button.style().unpolish(self.camera_nav_button)
            self.camera_nav_button.style().polish(self.camera_nav_button)

    # [AI-GENERATED / AI-ASSISTED CODE] Page switching logic.
    def _set_page(self,idx):
        if idx == 0 and hasattr(self, 'combined_overview_camera_scroll'):
            self.combined_overview_camera_scroll.verticalScrollBar().setValue(0)
        if idx==2:
            if self.attendance_selected_ids or self.current_session is not None:
                self._set_nav_state(2); self.pages.setCurrentIndex(2); return
            return self._go_scan()
        if idx==3 and self.current_session is None:return
        self._set_nav_state(idx); self.pages.setCurrentIndex(idx)
        if idx==0:self._refresh_metrics()


    # [AI-GENERATED / AI-ASSISTED CODE] Direct page switching helper.
    def _set_page_direct(self,idx):
        self._set_nav_state(idx)
        self.pages.setCurrentIndex(idx)
        if idx == 0 and hasattr(self, 'combined_overview_camera_scroll'):
            self.combined_overview_camera_scroll.verticalScrollBar().setValue(0)

    def _set_server_status(self, text, connected=None):
        self.server_status_text = str(text)
        if connected is None:
            connected = self.server.connected
        self.server_badge.setText(('SERVER • CONNECTED' if connected else 'SERVER • DISCONNECTED') if self.ui_language == 'en' else ('MÁY CHỦ • ĐÃ KẾT NỐI' if connected else 'MÁY CHỦ • CHƯA KẾT NỐI'))
        self.server_settings_status.setText(self.server_status_text) if hasattr(self, 'server_settings_status') else None

    def _refresh_server_classes(self):
        if not self.server.connected:
            self.server_classes=[]
            if hasattr(self,'class_combo'):
                self.class_combo.clear(); self.class_combo.addItem('Server not connected' if self.ui_language == 'en' else 'Chưa kết nối máy chủ', None)
                self.class_info.setText('Server not connected.' if self.ui_language == 'en' else 'Máy chủ chưa kết nối.')
            return False
        try:
            self.server_classes=self.server.get_classes()
            self._populate_class_combo()
            self._set_server_status(f"Đã kết nối • {len(self.server_classes)} lớp học", True)
            return True
        except ServerClientError as exc:
            self._set_server_status(str(exc), False)
            return False

    def _populate_class_combo(self):
        if not hasattr(self,'class_combo'):
            return
        selected=self.server_selected_class_id
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        if not self.server_classes:
            self.class_combo.addItem('No classes available' if self.ui_language == 'en' else 'Chưa có lớp học', None)
            self.class_info.setText('Create a class on the Server first.' if self.ui_language == 'en' else 'Hãy tạo lớp học trên máy chủ trước.')
        else:
            index_to_select=0
            for idx, item in enumerate(self.server_classes):
                text=(f"{item.get('name','Class')} • {item.get('code','')} • {item.get('student_count',0)} students" if self.ui_language == 'en' else f"{item.get('name','Lớp')} • {item.get('code','')} • {item.get('student_count',0)} học sinh")
                self.class_combo.addItem(text, int(item.get('id')))
                if int(item.get('id')) == int(selected or 0):
                    index_to_select=idx
            self.class_combo.setCurrentIndex(index_to_select)
            self.class_info.setText('Choose a class before starting a session.' if self.ui_language == 'en' else 'Chọn lớp trước khi bắt đầu buổi quét.')
        self.class_combo.blockSignals(False)
        self._class_changed(self.class_combo.currentIndex())

    def _queue_overview_roster_load(self, class_id):
        """Load a selected class into inline Overview student cards, without starting a scan."""
        try:
            class_id = int(class_id or 0)
        except (TypeError, ValueError):
            return
        if class_id <= 0 or self.current_session is not None:
            return
        if not getattr(getattr(self, 'server', None), 'connected', False):
            return
        if getattr(self, '_roster_future', None) is not None:
            return
        if getattr(self, 'server_roster', None):
            return

        en = getattr(self, 'ui_language', 'vi') == 'en'
        self._attendance_only_flow = True
        if hasattr(self, 'overview_attendance_summary'):
            self.overview_attendance_summary.setText(
                'Loading students for this class…' if en else 'Đang tải danh sách học sinh của lớp…')
        if hasattr(self, 'overview_students_placeholder'):
            self.overview_students_placeholder.setText(
                'Loading students…' if en else 'Đang tải danh sách học sinh…')
            self.overview_students_placeholder.setVisible(True)
        if hasattr(self, 'overview_students_host'):
            self.overview_students_host.setVisible(False)
        if hasattr(self, 'attendance_prepare_button'):
            self.attendance_prepare_button.setEnabled(False)
        if hasattr(self, 'class_combo'):
            self.class_combo.setEnabled(False)
        self._roster_future = self.server_executor.submit(self.server.get_roster, class_id)
        self._async_start_timer.start()

    def _class_changed(self, index):
        if not hasattr(self,'class_combo'):
            return
        value=self.class_combo.itemData(index)
        new_class_id = int(value) if value else 0
        old_class_id = int(getattr(self, 'server_selected_class_id', 0) or 0)
        self.server_selected_class_id = new_class_id
        if new_class_id != old_class_id and self.current_session is None:
            self.attendance_selected_ids.clear()
            self.attendance_absent_ids.clear()
            self.server_roster = []
            self.selected_server_roster = []
            self.absent_server_roster = []
            self.expected_student_count = 0
            if hasattr(self, 'overview_students_grid'):
                self._populate_overview_student_cards([])
            if hasattr(self, 'overview_attendance_summary'):
                self.overview_attendance_summary.setText('Attendance has not been completed.' if self.ui_language == 'en' else 'Chưa điểm danh cho buổi quét này.')
            if hasattr(self, 'attendance_prepare_button'):
                self.attendance_prepare_button.setText('ATTENDANCE' if self.ui_language == 'en' else 'ĐIỂM DANH')
        item=next((x for x in self.server_classes if int(x.get('id',0))==self.server_selected_class_id), None)
        if item:
            self.class_info.setText((f"{item.get('student_count',0)} students • select the students to scan below" if self.ui_language == 'en' else f"{item.get('student_count',0)} học sinh • chọn học sinh cần quét bên dưới"))
            # The reference UI displays student cards immediately after class selection.
            # This only loads the roster and never starts a scan automatically.
            if (self.current_session is None and getattr(self.server, 'connected', False)
                    and not self.server_roster and self._roster_future is None):
                self._queue_overview_roster_load(new_class_id)
        elif hasattr(self,'class_info'):
            self.class_info.setText('No class selected' if self.ui_language == 'en' else 'Chưa chọn lớp')

    def _open_server_login(self):
        if hasattr(self,'server_url_edit') and self.server_url_edit.text().strip():
            try: self.server.set_base_url(self.server_url_edit.text().strip())
            except Exception: pass
        dialog=ServerLoginDialog(self,self.server)
        if dialog.exec()!=QDialog.Accepted:
            return False
        self.server_profile=dialog.profile
        self.server_username=dialog.username
        self.server.save_config(SERVER_CONFIG_PATH)
        if hasattr(self,'server_url_edit'): self.server_url_edit.setText(self.server.base_url)
        if hasattr(self,'server_user_edit'): self.server_user_edit.setText(self.server_username)
        self._set_server_status(f"Đã kết nối • {self.server_profile.get('full_name') or self.server_username}", True)
        self._load_teacher_preferences_from_server(show_status=False)
        self._refresh_server_classes()
        return True

    def _ensure_server(self):
        if self.server.connected:
            return self._refresh_server_classes()
        return self._open_server_login()

    def _disconnect_server(self):
        # Explicit logout means this Windows installation should forget the linked
        # Teacher account. Closing the app does not revoke the device session.
        self.server.logout(revoke_device=True)
        self.settings.remove('device_token')
        self.settings.remove('password')
        self.settings.setValue('remember_login', False)
        self.settings.sync()
        self.server_profile=None
        self.server_classes=[]
        self.server_roster=[]
        self.selected_server_roster=[]
        self.absent_server_roster=[]
        self.attendance_selected_ids=set()
        self.attendance_absent_ids=set()
        self.expected_student_count=0
        self.server_session_id=None
        self.server_username=''
        self._set_server_status('Đã đăng xuất khỏi server.', False)
        if hasattr(self,'class_combo'):
            self._populate_class_combo()

    # GOD EYES V13 ATTENDANCE STATE FIX
# Fixes: canonical bulk selection, stale roster after session finish, visible active-session lock.
# [AI-GENERATED / AI-ASSISTED CODE] Session flow: download class roster, choose attendance once, then scan on demand.
    def _go_scan(self):
        """Enter the scan screen immediately, then fetch Server data off the UI thread."""
        if self.attendance_selected_ids:
            if self.current_session:
                self._set_page_direct(3)
            else:
                self._start_monitor_from_attendance()
            return

        if not self._ensure_server():
            self.scan_state.setText('SERVER CHƯA KẾT NỐI')
            return
        if not self.server_selected_class_id:
            self.scan_state.setText('CHƯA CHỌN LỚP HỌC')
            QMessageBox.information(self,'God Eyes','Hãy chọn lớp học trước khi điểm danh.')
            return

        # Show the scan page before doing any network work. The previous version
        # called get_roster() synchronously, so a slow Render request made the
        # Start button appear dead and blocked camera rendering at the same time.
        self._set_page_direct(2)
        self.scan_state.setText('ĐANG TẢI DANH SÁCH HỌC SINH…')
        self.scan_start.setEnabled(False)
        QApplication.processEvents()

        if self._roster_future is not None and not self._roster_future.done():
            return True
        class_id = int(self.server_selected_class_id)
        self._roster_future = self.server_executor.submit(self.server.get_roster, class_id)
        self._async_start_timer.start()
        return True

    def _poll_async_start(self):
        """Poll background Server operations without ever blocking the Qt event loop."""
        # Roster loading phase.
        if self._roster_future is not None and self._roster_future.done():
            future = self._roster_future
            self._roster_future = None
            attendance_only = bool(getattr(self, '_attendance_only_flow', False))
            self._attendance_only_flow = False
            try:
                roster_response = future.result()
                roster = list(roster_response.get('students') or []) if isinstance(roster_response,dict) else []
            except ServerClientError as exc:
                self.scan_start.setEnabled(True)
                self.scan_state.setText(str(exc))
                if attendance_only and hasattr(self, 'attendance_prepare_button'):
                    self.attendance_prepare_button.setEnabled(True)
                    if hasattr(self, 'class_combo'): self.class_combo.setEnabled(True)
                    if hasattr(self, 'overview_start_button'): self.overview_start_button.setEnabled(False)
                    self.overview_attendance_summary.setText('Could not load students.' if self.ui_language == 'en' else 'Không tải được danh sách học sinh.')
                if self._session_create_future is None:
                    self._async_start_timer.stop()
                return
            except Exception as exc:
                self.scan_start.setEnabled(True)
                self.scan_state.setText(f'ROSTER ERROR • {exc}')
                if attendance_only and hasattr(self, 'attendance_prepare_button'):
                    self.attendance_prepare_button.setEnabled(True)
                    if hasattr(self, 'class_combo'): self.class_combo.setEnabled(True)
                    if hasattr(self, 'overview_start_button'): self.overview_start_button.setEnabled(False)
                    self.overview_attendance_summary.setText('Could not load students.' if self.ui_language == 'en' else 'Không tải được danh sách học sinh.')
                if self._session_create_future is None:
                    self._async_start_timer.stop()
                return

            if not roster:
                self.scan_start.setEnabled(True)
                QMessageBox.information(self,'God Eyes', 'This class has no students on the Server.' if self.ui_language == 'en' else 'Lớp này chưa có học sinh trên máy chủ.')
                if attendance_only and hasattr(self, 'attendance_prepare_button'):
                    self.attendance_prepare_button.setEnabled(True)
                    if hasattr(self, 'class_combo'): self.class_combo.setEnabled(True)
                    if hasattr(self, 'overview_start_button'): self.overview_start_button.setEnabled(False)
                    self.overview_attendance_summary.setText('No students found.' if self.ui_language == 'en' else 'Không tìm thấy học sinh.')
                if self._session_create_future is None:
                    self._async_start_timer.stop()
                return

            # For the Overview workflow, show the roster as inline cards so the teacher
            # can select/deselect students directly on this screen. Keep the same default
            # behavior as the old dialog (everyone selected initially) and retain the
            # existing dialog unchanged for non-Overview scan flows.
            selected_before = set(self.attendance_selected_ids)
            roster_ids = {int(item.get('id') or 0) for item in roster}
            if attendance_only:
                selected_ids = (selected_before & roster_ids) if selected_before else set(roster_ids)
            else:
                dialog=AttendanceSelectionDialog(self, roster)
                if dialog.exec()!=QDialog.Accepted:
                    self.scan_start.setEnabled(True)
                    self.scan_state.setText('ATTENDANCE CANCELLED' if self.ui_language == 'en' else 'ĐÃ HỦY ĐIỂM DANH')
                    if self._session_create_future is None:
                        self._async_start_timer.stop()
                    return
                selected_ids=dialog.selected_ids()
            self.attendance_selected_ids=set(selected_ids)
            self.attendance_absent_ids={int(item.get('id') or 0) for item in roster}-set(selected_ids)
            if hasattr(self, 'overview_attendance_summary'):
                self.overview_attendance_summary.setText((f'Attendance: {len(selected_ids)}/{len(roster)} selected' if self.ui_language == 'en' else f'Đã chọn {len(selected_ids)}/{len(roster)} học sinh; {len(roster) - len(selected_ids)} học sinh không được quét.'))
            self.server_roster=roster
            self.selected_server_roster=[item for item in roster if int(item.get('id') or 0) in self.attendance_selected_ids and item.get('ready') and item.get('face_embedding')]
            self.absent_server_roster=[item for item in roster if int(item.get('id') or 0) in self.attendance_absent_ids]
            self.expected_student_count=len(selected_ids)
            if attendance_only and hasattr(self, 'overview_students_grid'):
                self._populate_overview_student_cards(roster)
            self.student_count=0; self.present_detected_ids=set(); self.pending_scan=False
            class_item=next((x for x in self.server_classes if int(x.get('id',0))==self.server_selected_class_id),None)
            class_text=f"{class_item.get('name','Lớp')} • {class_item.get('code','')}" if class_item else 'Lớp đã chọn'
            self.scan_class.setText(class_text)
            self.attendance_summary.setText(f'Đã chọn {len(selected_ids)}/{len(roster)} học sinh • loại trừ {len(self.absent_server_roster)} học sinh vắng khỏi Face ID')
            self.scan_count.setText((f'0/{self.expected_student_count} students identified' if self.ui_language == 'en' else f'0/{self.expected_student_count} học sinh đã được nhận diện'))
            self.scan_state.setText('Ready to start the scan.' if self.ui_language == 'en' else 'Đã sẵn sàng để bắt đầu quét.')
            if attendance_only:
                ready_count = len(self.selected_server_roster)
                self.overview_attendance_summary.setText(
                    (f'Attendance complete: {len(selected_ids)}/{len(roster)} selected • {ready_count} Face IDs ready' if self.ui_language == 'en' else
                     f'Đã điểm danh: {len(selected_ids)}/{len(roster)} học sinh • {ready_count} khuôn mặt sẵn sàng')
                )
                self.attendance_prepare_button.setText('ATTENDANCE' if self.ui_language == 'en' else 'ĐIỂM DANH')
                self.attendance_prepare_button.setEnabled(True)
                self.attendance_prepare_button.setVisible(False)
                if hasattr(self, 'overview_start_button'):
                    self.overview_start_button.setEnabled(bool(selected_ids) and bool(ready_count))
                if hasattr(self, 'class_combo'):
                    self.class_combo.setEnabled(True)
                self._set_page_direct(0)
            else:
                if hasattr(self, 'overview_start_button'): self.overview_start_button.setEnabled(True)
                if hasattr(self,'class_combo'): self.class_combo.setEnabled(False)
                # Attendance is fixed for this session. Enter the dedicated Face ID scan stage.
                self.pending_face_scan = True
                self.face_scan_active = False
                self._start_face_scan_from_attendance()

        # Session creation phase. The remote request/recovery runs off the UI thread.
        if self._session_create_future is not None and self._session_create_future.done():
            future = self._session_create_future
            self._session_create_future = None
            try:
                remote = future.result()
                if self.pending_face_scan:
                    self.pending_face_scan = False
                    self._finish_local_session_and_start_ai(remote, mode='scan', go_monitor=False)
                else:
                    self._finish_local_session_and_start_ai(remote, mode='monitor', go_monitor=True)
            except Exception as exc:
                self.scan_start.setEnabled(True)
                self.scan_start.setText('BẮT ĐẦU GIÁM SÁT  →')
                self.scan_state.setText(f'KHÔNG THỂ KHỞI ĐỘNG BUỔI HỌC • {exc}')
                QMessageBox.critical(self, 'God Eyes', f'Không thể khởi động buổi học.\n\n{exc}')

        if self._roster_future is None and self._session_create_future is None:
            self._async_start_timer.stop()

    def _show_current_selection(self):
        if not self.attendance_selected_ids:
            return
        rows=[item for item in self.server_roster if int(item.get('id') or 0) in self.attendance_selected_ids]
        lines=[]
        for item in rows:
            lines.append(f"• {item.get('student_code','')} — {item.get('full_name','')}")
        absent=[item for item in self.server_roster if int(item.get('id') or 0) in self.attendance_absent_ids]
        message = f"Học sinh được chọn ({len(rows)}):\n" + "\n".join(lines[:30])
        if len(lines)>30:
            message += f"\n… và {len(lines)-30} học sinh khác."
        if absent:
            message += f"\n\nĐã đánh dấu vắng: {len(absent)} học sinh."
        QMessageBox.information(self,'Danh sách tham gia buổi học',message)

    # [AI-GENERATED / AI-ASSISTED CODE] Camera state check.
    def _camera_config_from_widgets(self):
        if hasattr(self, 'camera_source_combo'):
            source = str(self.camera_source_combo.currentData() or self.camera_source)
        else:
            source = self.camera_source
        source = _normalize_camera_source(source)
        index_widget = getattr(self, 'camera_device_index_edit', None) or getattr(self, 'camera_index_edit', None)
        try:
            idx = max(0, min(9, int((index_widget.text() if index_widget is not None else self.camera_device_index) or 0)))
        except Exception:
            idx = 0
        host_widget = getattr(self, 'camera_host_edit', None)
        host = host_widget.text().strip() if host_widget is not None else self.camera_host
        port_widget = getattr(self, 'camera_port_edit', None)
        try:
            port = max(1, min(65535, int((port_widget.text() if port_widget is not None else self.camera_port) or 554)))
        except Exception:
            port = 554
        stream_widget = getattr(self, 'camera_stream_config_combo', None) or getattr(self, 'camera_stream_combo', None)
        stream = str(stream_widget.currentData() if stream_widget is not None else self.camera_stream or 'stream1')
        username_widget = getattr(self, 'camera_username_edit', None)
        username = username_widget.text().strip() if username_widget is not None else self.camera_username
        password_widget = getattr(self, 'camera_password_edit', None)
        password = password_widget.text() if password_widget is not None else self.camera_password
        return source, idx, host, port, stream, username, password

    def _camera_source_display(self, source, idx, host):
        en = getattr(self, 'ui_language', 'vi') == 'en'
        if source == 'wifi_camera':
            return 'Wi-Fi / RTSP camera' if en else 'Camera Wi-Fi / RTSP'
        if source == 'usb':
            return 'USB camera' if en else 'Camera USB'
        if source == 'webcam':
            return 'Built-in camera' if en else 'Camera tích hợp'
        return str(source)

    def _build_camera_url(self,source,host,port,stream,username,password):
        if source!='wifi_camera' or not host or not username or not password: return ''
        return f'rtsp://{quote(str(username),safe="")}:{quote(str(password),safe="")}@{host}:{int(port)}/{stream}'

    def _camera_type_for_server(self):
        return {'webcam':'WEBCAM','usb':'USB_WEBCAM','wifi_camera':'WIFI_CAMERA'}.get(self.camera_source,'WEBCAM')

    def _refresh_camera_settings_visibility(self):
        source = _normalize_camera_source(getattr(self, 'camera_source', 'webcam'))
        if hasattr(self, 'camera_source_combo'):
            source = _normalize_camera_source(self.camera_source_combo.currentData() or source)
        is_wifi = source == 'wifi_camera'
        if hasattr(self, 'wifi_camera_panel'):
            self.wifi_camera_panel.setVisible(is_wifi)
        if hasattr(self, 'camera_wifi_fields'):
            self.camera_wifi_fields.setVisible(is_wifi)
        if hasattr(self, 'camera_credentials_title'):
            self.camera_credentials_title.setText(
                ('Camera account' if self.ui_language == 'en' else 'Tài khoản camera') if is_wifi else
                ('Camera connection' if self.ui_language == 'en' else 'Kết nối camera')
            )
        if hasattr(self, 'camera_legacy_index_label'):
            self.camera_legacy_index_label.setVisible(False)
        if hasattr(self, 'camera_index_edit'):
            self.camera_index_edit.setVisible(False)
        if hasattr(self, 'camera_device_card'):
            self.camera_device_card.setVisible(False)
        if hasattr(self, 'camera_wifi_fields'):
            self.camera_wifi_fields.setVisible(is_wifi)

    def _camera_source_changed(self,_index):
        self.camera_source=str(self.camera_source_combo.currentData() or 'webcam')
        self._refresh_camera_settings_visibility()
        self._update_camera_connection_summary()

    def _apply_camera_preferences(self,data,save_local=True):
        if not isinstance(data,dict): return
        # Teacher Portal API versions may return camera settings flat or nested.
        camera_data={}
        for nested_key in ('camera','camera_settings','camera_preferences','device_settings','preferences','settings'):
            nested_value=data.get(nested_key)
            if isinstance(nested_value,dict):
                camera_data.update(nested_value)
        camera_data.update({k:v for k,v in data.items() if not isinstance(v,dict)})
        def pick(*keys, default=None):
            for key in keys:
                value=camera_data.get(key)
                if value is not None and value != '':
                    return value
            return default
        was_camera_running = self._camera_alive() if hasattr(self, '_camera_alive') else False
        previous_source = str(getattr(self, 'camera_source', 'webcam') or 'webcam')
        source = _normalize_camera_source(pick('camera_source','camera_type','source','camera_mode','video_source','camera_input','input_source', default=self.camera_source))
        brand=str(pick('camera_brand','brand',default=self.camera_brand) or self.camera_brand or 'tapo').lower()
        if brand!='tapo': brand='tapo'
        try: idx=max(0,min(9,int(pick('camera_device_index','camera_index','device_index','webcam_index','usb_camera_index',default=self.camera_device_index))))
        except Exception: idx=self.camera_device_index
        host=str(pick('camera_host','camera_ip','host','ip_address','rtsp_host',default=self.camera_host) or '').strip()
        try: port=max(1,min(65535,int(pick('camera_port','rtsp_port','port',default=self.camera_port))))
        except Exception: port=554
        stream=str(pick('camera_stream','stream','stream_profile',default=self.camera_stream) or 'stream1').lower()
        if stream not in ('stream1','stream2'): stream='stream1'
        username=str(pick('camera_username','camera_account_username','username','user_name',default=self.camera_username) or '').strip()
        username_changed = username != self.camera_username
        changed=(source,brand,idx,host,port,stream,username)!=(self.camera_source,self.camera_brand,self.camera_device_index,self.camera_host,self.camera_port,self.camera_stream,self.camera_username)
        self.camera_source,self.camera_brand,self.camera_device_index,self.camera_host,self.camera_port,self.camera_stream,self.camera_username=source,brand,idx,host,port,stream,username
        if save_local:
            self.settings.setValue('camera_source',source); self.settings.setValue('camera_brand',brand); self.settings.setValue('camera_device_index',idx); self.settings.setValue('camera_host',host); self.settings.setValue('camera_port',port); self.settings.setValue('camera_stream',stream); self.settings.setValue('camera_username',username); self.settings.sync()
        if hasattr(self,'camera_source_combo'):
            self.camera_source_combo.blockSignals(True)
            self.camera_source_combo.setCurrentIndex({'webcam':0,'usb':1,'wifi_camera':2}.get(source,0))
            self.camera_source_combo.blockSignals(False)
            # The redesigned Camera page uses camera_device_index_edit and
            # camera_stream_config_combo. The old camera_index_edit and
            # camera_stream_combo belong to the removed Settings page, so only
            # update them when that legacy page actually exists.
            if hasattr(self, 'camera_index_edit'):
                self.camera_index_edit.setText(str(idx))
            if hasattr(self, 'camera_stream_combo'):
                self.camera_stream_combo.setCurrentIndex(0 if stream == 'stream1' else 1)
            self._refresh_camera_settings_visibility()
            if hasattr(self,'camera_brand_combo'):
                self.camera_brand_combo.setCurrentIndex(0)
        if hasattr(self,'camera_source_label'):
            self.camera_source_label.setText(self._camera_source_display(source,idx,host))
        if hasattr(self, 'camera_config_source_label'):
            self.camera_config_source_label.setText(self._camera_source_display(source, idx, host))
        if hasattr(self, 'camera_device_index_edit'):
            self.camera_device_index_edit.setText(str(idx))
        if hasattr(self, 'camera_host_edit'):
            self.camera_host_edit.setText(host)
        if hasattr(self, 'camera_port_edit'):
            self.camera_port_edit.setText(str(port))
        if hasattr(self, 'camera_stream_config_combo'):
            self.camera_stream_config_combo.setCurrentIndex(1 if stream == 'stream2' else 0)
        if username_changed and hasattr(self, 'camera_username_edit') and not self.camera_username_edit.hasFocus():
            self.camera_username_edit.setText(username)
        self._refresh_camera_settings_visibility()
        self._update_camera_connection_summary()
        # If Teacher Portal changes Built-in / USB / Wi-Fi while idle, close the
        # old capture and reconnect with the new source. Never swap cameras mid-session.
        if changed and self.current_session is None and was_camera_running:
            self._stop_camera_test()
            can_reconnect = (source != 'wifi_camera') or bool(host and username and self.camera_password)
            if can_reconnect:
                QTimer.singleShot(250, self._connect_camera)

    def _save_local_camera_settings(self):
        values=self._camera_config_from_widgets(); self.camera_source,self.camera_device_index,self.camera_host,self.camera_port,self.camera_stream,self.camera_username,self.camera_password=values
        source,idx,host,port,stream,username,password=values
        self.settings.setValue('camera_source',source); self.settings.setValue('camera_brand',self.camera_brand); self.settings.setValue('camera_device_index',idx); self.settings.setValue('camera_host',host); self.settings.setValue('camera_port',port); self.settings.setValue('camera_stream',stream); self.settings.setValue('camera_username',username); self.settings.setValue('camera_password_local',password); self.settings.sync(); return values

    def _update_camera_connection_summary(self):
        if not hasattr(self,'camera_connection_label'): return
        if self.camera_source!='wifi_camera':
            self.camera_connection_label.setText('Use the selected computer camera.' if self.ui_language=='en' else 'Đang dùng camera trên máy tính.')
        elif self.camera_host:
            self.camera_connection_label.setText('Connected camera settings saved.' if self.ui_language=='en' else 'Đã lưu cấu hình camera đã kết nối.')
        else:
            self.camera_connection_label.setText('Not connected' if self.ui_language=='en' else 'Chưa kết nối')

    def _open_camera_connection_dialog(self):
        if hasattr(self, 'camera_username_edit'):
            self.camera_username = self.camera_username_edit.text().strip()
        if hasattr(self, 'camera_password_edit'):
            self.camera_password = self.camera_password_edit.text()
        dialog=CameraConnectionDialog(self, self.camera_brand, self.camera_username, self.camera_password, self.camera_stream, self.ui_language)
        if dialog.exec()!=QDialog.Accepted:
            return False
        info=dialog.connection_info()
        self.camera_source='wifi_camera'; self.camera_brand=info['brand']; self.camera_host=info['host']; self.camera_port=info.get('port',554); self.camera_stream=info.get('stream','stream1'); self.camera_username=info['username']; self.camera_password=info['password']
        self.settings.setValue('camera_source','wifi_camera'); self.settings.setValue('camera_brand',self.camera_brand); self.settings.setValue('camera_host',self.camera_host); self.settings.setValue('camera_port',self.camera_port); self.settings.setValue('camera_stream',self.camera_stream); self.settings.setValue('camera_username',self.camera_username); self.settings.setValue('camera_password_local',self.camera_password); self.settings.sync()
        self.settings.setValue('camera_username', self.camera_username)
        self.settings.setValue('camera_password_local', self.camera_password)
        self.settings.sync()
        if hasattr(self, 'camera_username_edit'): self.camera_username_edit.setText(self.camera_username)
        if hasattr(self, 'camera_password_edit'): self.camera_password_edit.setText(self.camera_password)
        self._refresh_camera_settings_visibility(); self._update_camera_connection_summary()
        try:
            if self.server.connected:
                data=self._save_teacher_preferences_remote(self.ui_language,self.ui_theme,{
                    'camera_source':'wifi_camera','camera_brand':self.camera_brand,'camera_device_index':self.camera_device_index,'camera_host':self.camera_host,'camera_port':self.camera_port,'camera_stream':self.camera_stream,'camera_username':self.camera_username,
                })
                self._apply_camera_preferences(data,save_local=True)
        except Exception as exc:
            self.camera_pref_status.setText(f'Camera connected locally, but Server sync failed • {exc}')
        self._connect_camera(); return True

    def _test_camera_from_settings(self):
        self._open_camera_connection_dialog()

    def _stop_camera_test(self):
        if self.camera:
            try: self.camera.stop(); self.camera.wait(900)
            except Exception: pass
            self.camera=None
        if hasattr(self,'camera_pref_status'): self.camera_pref_status.setText('Camera stopped.' if self.ui_language=='en' else 'Đã dừng camera.')

    def _camera_alive(self):return self.camera is not None and self.camera.isRunning()

    def _resolve_model_dir(self):
        candidates = [
            MODEL_DIR,
            Path(r'D:\GodEyesServer\data\face_models'),
        ]
        for candidate in candidates:
            if (candidate / 'face_detection_yunet_2023mar.onnx').exists():
                return candidate
        return MODEL_DIR

    def _set_ai_server_roster_compatible(self):
        """Load the selected Server Face ID roster into whichever AI API is available."""
        if self.ai is None:
            raise RuntimeError('AI worker chưa được khởi tạo.')
        roster = list(self.selected_server_roster or [])
        setter = getattr(self.ai, 'set_server_roster', None)
        if callable(setter):
            setter(roster)
            return
        identity = getattr(self.ai, 'identity', None)
        setter = getattr(identity, 'set_server_roster', None) if identity is not None else None
        if callable(setter):
            setter(roster)
            return
        raise RuntimeError('AI engine không hỗ trợ tải Face ID từ Server. Hãy đồng bộ lại engine.py.')

    def _start_scan_button(self):
        # Start the actual Face ID scan after attendance is fixed.
        if getattr(self,'_start_scan_busy',False): return
        if not self.attendance_selected_ids:
            self._go_scan(); return
        if self.face_scan_active: return
        self._start_scan_busy=True
        try:
            if not self._camera_alive():
                self.pending_scan=True; self.scan_state.setText('ĐANG KẾT NỐI CAMERA…'); self.scan_start.setEnabled(False)
                if not self._connect_camera(): self.pending_scan=False; self.scan_start.setEnabled(True)
                return
            self.pending_scan=False; self.scan_state.setText('AI ĐANG KHỞI ĐỘNG • ĐANG QUÉT FACE ID…')
            if self.current_session is None:
                self.pending_face_scan=True; self._start_face_scan_from_attendance(); return
            if self._start_scan():
                self.face_scan_active=True; self.scan_start.setEnabled(False); self.scan_start.setText('ĐANG QUÉT FACE ID…')
        except Exception as exc:
            import traceback; traceback.print_exc(); self.pending_scan=False; self.scan_start.setEnabled(True)
            self.scan_state.setText(f'AI ERROR • {str(exc).strip() or exc.__class__.__name__}')
        finally:
            self._start_scan_busy=False

    # [AI-GENERATED / AI-ASSISTED CODE] Camera connection control.
    def _connect_camera(self):
        try:
            source,idx,host,port,stream,username,password=self._camera_config_from_widgets()
            self.camera_source,self.camera_device_index,self.camera_host,self.camera_port,self.camera_stream,self.camera_username,self.camera_password=source,idx,host,port,stream,username,password
        except Exception as exc:
            import traceback
            traceback.print_exc()
            if hasattr(self,'cam_status'):
                self.cam_status.setText(f'CAMERA ERROR • {exc}')
            return False
        if source=='wifi_camera' and (not host or not username or not password):
            return self._open_camera_connection_dialog()
        if self._camera_alive(): self._stop_camera_test()
        rtsp=self._build_camera_url(source,host,port,stream,username,password)
        if source=='wifi_camera' and not rtsp:
            if hasattr(self,'camera_pref_status'): self.camera_pref_status.setText('Camera connection settings are incomplete.' if self.ui_language=='en' else 'Cấu hình kết nối camera chưa đầy đủ.')
            return False
        if hasattr(self,'camera_source_label'): self.camera_source_label.setText(self._camera_source_display(source,idx,host))
        if hasattr(self,'cam_status'): self.cam_status.setText('CONNECTING…')
        if hasattr(self,'camera_pref_status'): self.camera_pref_status.setText('Connecting to camera…' if self.ui_language=='en' else 'Đang kết nối camera…')
        self.camera=CameraWorker(idx,source,rtsp); self.camera.state.connect(self._camera_state); self.camera.start(); return True

    def _camera_state(self,text):
        text=str(text or '').strip(); upper=text.upper()
        # The capture worker emits technical English status strings. Present a
        # single-language status in the app, while preserving error details.
        if upper.startswith('CONNECTED'):
            text = 'Camera connected.' if self.ui_language == 'en' else 'Đã kết nối camera.'
        elif upper.startswith('CONNECTING'):
            text = 'Connecting to camera…' if self.ui_language == 'en' else 'Đang kết nối camera…'
        elif upper.startswith(('CAMERA ERROR', 'ERROR')):
            detail = text.split(':', 1)[1].strip() if ':' in text else text
            text = (f'Camera error: {detail}' if self.ui_language == 'en' else f'Lỗi camera: {detail}')
        if hasattr(self,'cam_status'): self.cam_status.setText(text)
        if hasattr(self,'camera_pref_status'): self.camera_pref_status.setText(text)
        connected=upper.startswith('CONNECTED')
        if hasattr(self, 'camera_connection_frame'):
            if connected:
                self.camera_connection_frame.setStyleSheet('QFrame#connectionStatus { background:#e6f8ef; border:1px solid #d3f1e1; border-radius:11px; }')
                if hasattr(self, 'camera_connection_dot'):
                    self.camera_connection_dot.setStyleSheet('color:#11a666;background:transparent;font-size:15px;')
                if hasattr(self, 'cam_status'):
                    self.cam_status.setStyleSheet('background:transparent;border:0;padding:0;color:#07854d;font-weight:800;')
            elif upper.startswith(('CAMERA ERROR', 'ERROR', 'WARNING')):
                self.camera_connection_frame.setStyleSheet('QFrame#connectionStatus { background:#fff1f2; border:1px solid #ffd9df; border-radius:11px; }')
                if hasattr(self, 'camera_connection_dot'):
                    self.camera_connection_dot.setStyleSheet('color:#d92d43;background:transparent;font-size:15px;')
                if hasattr(self, 'cam_status'):
                    self.cam_status.setStyleSheet('background:transparent;border:0;padding:0;color:#c32332;font-weight:800;')
            else:
                self.camera_connection_frame.setStyleSheet('QFrame#connectionStatus { background:#edf5ff; border:1px solid #d8e8ff; border-radius:11px; }')
                if hasattr(self, 'camera_connection_dot'):
                    self.camera_connection_dot.setStyleSheet('color:#1678f8;background:transparent;font-size:15px;')
                if hasattr(self, 'cam_status'):
                    self.cam_status.setStyleSheet('background:transparent;border:0;padding:0;color:#195da8;font-weight:800;')
        if hasattr(self, 'camera_preview_badge'):
            if connected:
                self.camera_preview_badge.setText('LIVE • CONNECTED' if self.ui_language == 'en' else 'TRỰC TIẾP • ĐÃ KẾT NỐI')
            elif upper.startswith(('CAMERA ERROR', 'ERROR')):
                self.camera_preview_badge.setText('CONNECTION ERROR' if self.ui_language == 'en' else 'LỖI KẾT NỐI')
            elif upper.startswith('CONNECTING'):
                self.camera_preview_badge.setText('CONNECTING…' if self.ui_language == 'en' else 'ĐANG KẾT NỐI…')
        if hasattr(self,'scan_state') and self.pages.currentIndex()==2 and connected:
            self.scan_state.setText('Camera ready • AI will start scanning.' if self.ui_language=='en' else 'Camera đã sẵn sàng • AI sẽ bắt đầu quét.')
        if connected and self.pending_face_scan:
            self.pending_face_scan=False
            self._start_face_scan_from_attendance()
        elif connected and self.pending_monitor_after_camera:
            self.pending_monitor_after_camera=False
            self._start_monitor_from_attendance()
        elif connected and self.pending_scan:
            self.pending_scan=False
            if self._start_scan():
                self.face_scan_active=True; self.scan_start.setEnabled(False); self.scan_start.setText('ĐANG QUÉT FACE ID…')
        elif ('CAMERA ERROR' in upper or 'WARNING' in upper) and (self.pending_scan or self.pending_monitor_after_camera or self.pending_face_scan):
            self.pending_scan=False; self.pending_monitor_after_camera=False; self.pending_face_scan=False; self.scan_start.setEnabled(True)

    def _board_changed(self,text):
        self.board_side=text
        if self.ai:self.ai.behavior.set_board_side(text)

    # [AI-GENERATED / AI-ASSISTED CODE] Receives the newest camera frame.
    def _frame_arrived(self,frame):
        self.latest=frame

    def _start_face_scan_from_attendance(self):
        if not self.attendance_selected_ids: return False
        self._set_page_direct(2)
        self.scan_start.setEnabled(True); self.scan_start.setText('BẮT ĐẦU QUÉT FACE ID  →')
        if not self._camera_alive():
            self.pending_face_scan=True; self.scan_state.setText('ĐANG KẾT NỐI CAMERA…')
            if not self._connect_camera(): self.pending_face_scan=False; return False
            return True
        if self.current_session is None:
            class_id=int(self.server_selected_class_id or 0)
            if class_id<=0: self.scan_state.setText('CHƯA CHỌN LỚP HỌC'); return False
            if self._session_create_future is None:
                self._session_create_future=self.server_executor.submit(self._create_remote_session_safe,class_id,self._camera_type_for_server())
                self._async_start_timer.start()
            self.scan_state.setText('ĐÃ SẴN SÀNG • BẤM BẮT ĐẦU QUÉT KHUÔN MẶT')
            return True
        if self._start_scan():
            self.face_scan_active=True; self.scan_start.setEnabled(False); self.scan_start.setText('ĐANG QUÉT FACE ID…'); return True
        return False

    def _start_monitor_from_attendance(self):
        """Show the monitor page immediately; create the Server session in the background."""
        if not self.attendance_selected_ids:
            return False
        if not self._camera_alive():
            self.pending_monitor_after_camera = True
            self._set_page_direct(2)
            self.scan_state.setText('ĐANG KẾT NỐI CAMERA…')
            if not self._connect_camera():
                self.pending_monitor_after_camera = False
                self.scan_start.setEnabled(True)
                return False
            return False

        # Put the user into the monitor screen before any network request.
        self._set_page_direct(3)
        self.mon_status.setText('ĐANG KHỞI TẠO BUỔI HỌC…')
        self.mon_camera_status.setText('STARTING…')
        QApplication.processEvents()

        if self.current_session is not None:
            self._start_scan(mode='monitor')
            self._start_monitor()
            return True

        if self._session_create_future is None:
            class_id = int(self.server_selected_class_id or 0)
            if class_id <= 0:
                self._set_page_direct(2)
                self.scan_state.setText('CHƯA CHỌN LỚP HỌC')
                return False
            self._session_create_future = self.server_executor.submit(
                self._create_remote_session_safe,
                class_id,
                self._camera_type_for_server(),
            )
            self._async_start_timer.start()
        return True

    def _create_remote_session_safe(self, class_id, camera_type):
        """Create/recover a remote session without touching Qt widgets."""
        try:
            return self.server.create_session(
                class_id,
                CLIENT_VERSION,
                camera_type,
                self.scan_date_context,
            )
        except ServerClientError as exc:
            if int(getattr(exc, 'status', 0) or 0) != 409 or not getattr(exc, 'session_id', None):
                raise
            old_session_id = int(exc.session_id)
            self.server.finish_session(old_session_id, 0)
            return self.server.create_session(
                class_id,
                CLIENT_VERSION,
                camera_type,
                self.scan_date_context,
            )

    def _finish_local_session_and_start_ai(self, remote, mode='monitor', go_monitor=True):
        """Finalize local session state on the Qt thread after remote creation completes."""
        if not isinstance(remote, dict) or not remote.get('session_id'):
            raise RuntimeError('Server không trả về session_id hợp lệ.')

        full_roster = list(remote.get('roster') or self.server_roster or [])
        self.server_session_id = int(remote['session_id'])
        self.server_roster = full_roster
        selected_ids = set(self.attendance_selected_ids)
        self.selected_server_roster = [
            item for item in full_roster
            if int(item.get('id') or 0) in selected_ids
            and item.get('ready')
            and item.get('face_embedding')
        ]
        self.absent_server_roster = [
            item for item in full_roster
            if int(item.get('id') or 0) in self.attendance_absent_ids
        ]
        ready_count = sum(1 for item in self.selected_server_roster if item.get('ready'))
        if ready_count <= 0:
            raise RuntimeError('KHÔNG CÓ FACE ID READY TRONG NHÓM ĐƯỢC CHỌN')

        self.current_session = self.db.create_session(self.board_side)
        # Lock attendance controls while the current session's roster is in use.
        self._refresh_overview_attendance_state()
        self._verified_track_identity = {}
        self._student_last_visible_at = {}
        self._event_last_saved_at = {}
        self._student_last_alert_at = {}
        self.audio_power_sum = 0.0
        self.audio_samples_count = 0
        self.audio_last_dbfs = None
        self.audio_avg_dbfs = None
        if hasattr(self, 'audio_current_label'):
            self.audio_current_label.setText('Hiện tại: — dBFS')
            self.audio_average_label.setText('Trung bình buổi học: Đang tích lũy…')
        self.session_start = time.time()
        self.session_elapsed = 0.0
        self.session_deadline_monotonic = None
        self.event_count = 0
        self.student_count = 0
        self.present_detected_ids = set()
        self._reset_live_observations()
        self.last_heartbeat = time.time()
        self.server_sync_text = (
            f'SERVER SESSION #{self.server_session_id} • '
            f'{ready_count}/{len(self.selected_server_roster)} Face ID READY • '
            f'LỚP {len(full_roster)} HS'
        )
        self._sync_absent_students(self.server_session_id)

        # Use the same AI configuration requirements as before; only startup timing changed.
        fps = self._ai_fps()
        if self.ai is None:
            self.ai = AIWorker(self.camera, self._resolve_model_dir(), self.board_side)
            self.ai.tracks_ready.connect(self._tracks)
            self.ai.status.connect(self._ai_status)
            self.ai.event_ready.connect(self._event)
            self._set_ai_server_roster_compatible()
            self.ai.configure(self.current_session, mode, fps, self.board_side)
            self.ai.start()
        else:
            self._set_ai_server_roster_compatible()
            self.ai.configure(self.current_session, mode, fps, self.board_side)

        self.session_deadline_monotonic = time.monotonic() + max(60, int(getattr(self, 'session_duration_seconds', 45 * 60)))
        self._arm_session_watchdog()
        if go_monitor:
            self.face_scan_active=False; self._set_page_direct(3)
            self.scan_start.setEnabled(False); self.scan_start.setText('ĐANG GIÁM SÁT…')
            self.mon_status.setText('AI ĐANG CHẠY…'); self.mon_camera_status.setText('AI ONLINE'); self._start_monitor()
        else:
            self.face_scan_active=True; self._set_page_direct(2)
            self.scan_start.setEnabled(False); self.scan_start.setText('ĐANG QUÉT FACE ID…')
            self.scan_state.setText(f'ĐANG QUÉT KHUÔN MẶT • {len(self.selected_server_roster)}/{self.expected_student_count} học sinh có dữ liệu khuôn mặt • AI đang đối chiếu…')

    def _start_scan(self, mode='scan'):
        """Legacy synchronous AI start path retained as a safe fallback."""
        if not self._camera_alive():
            self.scan_state.setText('CAMERA NOT READY')
            return False
        if not self.server.connected:
            self.scan_state.setText('SERVER NOT CONNECTED')
            return False
        class_id = int(self.server_selected_class_id or 0)
        if class_id <= 0:
            self.scan_state.setText('CHƯA CHỌN LỚP HỌC')
            return False
        if not self.selected_server_roster:
            self.scan_state.setText('CHƯA CÓ HỌC SINH CÓ FACE ID ĐƯỢC CHỌN')
            return False
        if self.current_session is None:
            remote = self._create_remote_session_safe(class_id, self._camera_type_for_server())
            self._finish_local_session_and_start_ai(remote)
            return True
        fps = self._ai_fps()
        if self.ai is None:
            self.ai = AIWorker(self.camera, self._resolve_model_dir(), self.board_side)
            self.ai.tracks_ready.connect(self._tracks)
            self.ai.status.connect(self._ai_status)
            self.ai.event_ready.connect(self._event)
            self._set_ai_server_roster_compatible()
            self.ai.configure(self.current_session, mode, fps, self.board_side)
            self.ai.start()
        else:
            self._set_ai_server_roster_compatible()
            self.ai.configure(self.current_session, mode, fps, self.board_side)
        self.scan_state.setText(f'ĐANG QUÉT • {len(self.selected_server_roster)}/{self.expected_student_count} học sinh được chọn có Face ID • SFace đang đối chiếu…')
        return True

    def _sync_absent_students(self, server_session_id):
        """Record manually excluded students as ABSENT observations on the Server."""
        if not self.absent_server_roster:
            return
        events=[]
        captured_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        for item in self.absent_server_roster:
            student_id=int(item.get('id') or 0)
            if student_id <= 0:
                continue
            events.append({
                'student_id':student_id,
                'student_code':str(item.get('student_code') or '')[:80],
                'full_name':str(item.get('full_name') or '')[:160],
                'observed_at':captured_at,
                'event_type':'ABSENT',
                'confidence':1.0,
                'assessment':'ABSENT',
                'details':'Học sinh được giáo viên đánh dấu vắng trước khi bắt đầu quét.',
            })
        if not events:
            return
        try:
            result=self.server.post_events(server_session_id, events)
            self.event_count += int(result.get('inserted') or 0)
            self.mon_events.setText(f'{self.event_count} events')
            for item in self.absent_server_roster:
                code=str(item.get('student_code') or '').strip()
                name=str(item.get('full_name') or '').strip()
                label=f'{code} • {name}' if code and name else (name or code or 'STUDENT')
                row=self.scan_table.rowCount(); self.scan_table.insertRow(row)
                self.scan_table.setItem(row,0,QTableWidgetItem(label))
                self.scan_table.setItem(row,1,QTableWidgetItem('ABSENT'))
                self.scan_table.setItem(row,2,QTableWidgetItem('EXCLUDED'))
            self.server_sync_text=f'SERVER SYNC • {len(events)} ABSENT observation(s)'
        except Exception as exc:
            self.server_sync_text=f'ABSENT SYNC ERROR • {exc}'

    # [AI-GENERATED / AI-ASSISTED CODE] Requests identity locking from the AI engine.
    def _lock_ids(self):
        if not self.ai:
            self.scan_state.setText('AI chưa khởi động • hãy bấm BẮT ĐẦU QUÉT FACE ID.'); return
        mapping=self.ai.lock_ids()
        allowed={int(x.get('id') or 0) for x in self.selected_server_roster}
        valid=[label for label in mapping.values() if self.ai.identity.student_id_for_label(label)>0 and self.ai.identity.student_id_for_label(label) in allowed]
        if not valid:
            self.scan_state.setText('CHƯA KHÓA ĐƯỢC DANH TÍNH • giữ khuôn mặt rõ 2–3 giây trước camera'); return
        self.student_count=len(set(valid)); self.face_scan_active=False; self._set_page_direct(3); self._start_monitor()

    def _ai_fps(self):
        """Return the fixed AI processing rate used by Scan/Monitor.

        The Settings page is intentionally not part of the current navigation,
        so Scan/Monitor must never depend on a UI performance widget.
        """
        return float(DEFAULT_AI_FPS)

    # [AI-GENERATED / AI-ASSISTED CODE] Starts live monitoring mode.
    def _start_monitor(self):
        if not self.ai:return
        fps=self._ai_fps()
        self.ai.configure(self.current_session,'monitor',fps,self.board_side)
        self._reset_live_observations()
        self.paused=False
        self.session_elapsed=0.0
        self.session_start=time.time()
        self.session_elapsed=0.0
        limit = max(60, min(180 * 60, int(getattr(self, 'session_duration_seconds', 45 * 60))))
        # Some legacy start routes enter monitor without setting a deadline.
        # Give those routes the same mandatory timer/watchdog as Overview starts.
        if self.current_session is not None and self.session_deadline_monotonic is None:
            self.session_deadline_monotonic = time.monotonic() + limit
            self._arm_session_watchdog()
        self.timer_label.setText(f'00:00 / {limit // 60:02d}:{limit % 60:02d}')
        if hasattr(self, 'session_timer') and not self.session_timer.isActive():
            self.session_timer.start(250)
        self.pause.setEnabled(True)
        self.resume.setEnabled(False)
        self.mon_status.setText('AI ONLINE • TỰ ĐỘNG XÁC THỰC FACE ID • ĐANG QUÉT ĐỘ TẬP TRUNG')
        self._start_audio_monitor()

    # [AI-GENERATED / AI-ASSISTED CODE] Updates student overlays and scan table from AI tracking output.
    # GOD EYES FACE IDENTITY SAFETY V16
    def _tracks(self,payload):
        items=list(payload or [])
        if self.paused:
            self.overlay_items=[]
            return

        # Snapshot the latest camera-rate boxes before replacing the detector
        # payload. If inference briefly returns no item for a selected, already
        # verified track (head turn / blink / short occlusion), keep that same
        # box visible for a short grace window instead of making it flicker off.
        # This is never a new detection and must not count attendance or create
        # an observation. No box is invented for a track never verified before.
        previous_overlay_by_track = {}
        for previous_item in list(getattr(self, 'overlay_items', []) or []):
            try:
                previous_tid = int(previous_item.get('track_id') or 0)
                previous_box = tuple(float(v) for v in previous_item.get('box', ()))
                if previous_tid > 0 and len(previous_box) == 4:
                    previous_overlay_by_track[previous_tid] = dict(previous_item)
            except Exception:
                continue

        # Privacy boundary: only render tracks resolved to a student selected during attendance.
        # If the same tracker briefly loses a face (turning sideways/occlusion), retain its last
        # verified identity for at most 2.0 seconds. This does not identify a new person or log
        # observations while the track is only predicted.
        now_mono = time.monotonic()
        allowed_ids = {int(x.get('id') or 0) for x in self.selected_server_roster}
        cache = getattr(self, '_verified_track_identity', {})
        visible_at = getattr(self, '_student_last_visible_at', {})
        for tid, cached in list(cache.items()):
            if now_mono - float(cached.get('last_confirmed') or 0.0) > 2.25:
                cache.pop(tid, None)

        verified_items = []
        disallowed_track_ids = set()
        uncertain_labels = {'IDENTITY UNCERTAIN', 'CHƯA XÁC ĐỊNH DANH TÍNH', 'UNKNOWN', 'UNCERTAIN'}
        for original in items:
            if not isinstance(original, dict):
                continue
            item = dict(original)
            label = str(item.get('student') or '').strip()
            try:
                track_id = int(item.get('track_id') or 0)
                missed = max(0, int(item.get('missed') or 0))
            except Exception:
                track_id, missed = 0, 0
            try:
                student_id = int(item.get('student_id') or 0)
            except Exception:
                student_id = 0
            if student_id <= 0 and label and label.upper() not in uncertain_labels and self.ai is not None:
                try:
                    student_id = int(self.ai.identity.student_id_for_label(label) or 0)
                except Exception:
                    student_id = 0

            if student_id > 0:
                # An explicit match to an unselected student must never inherit another identity.
                if student_id not in allowed_ids:
                    if track_id > 0:
                        disallowed_track_ids.add(track_id)
                    continue
                cached = cache.get(track_id) if track_id > 0 else None
                direct_identity = bool(label) and label.upper() not in uncertain_labels
                if not direct_identity and cached and int(cached.get('student_id') or 0) == student_id:
                    label = str(cached.get('student') or label)
                if not label or label.upper() in uncertain_labels:
                    roster_match = next((r for r in self.selected_server_roster if int(r.get('id') or 0) == student_id), None)
                    if roster_match:
                        code = str(roster_match.get('student_code') or '').strip()
                        name = str(roster_match.get('full_name') or '').strip()
                        label = f'{code} • {name}' if code and name else (name or code or label)
                item['student'] = label
                item['student_id'] = student_id
                # GOD EYES FACE IDENTITY SAFETY V16
                # Identity labels are only fresh when the engine explicitly reports a current-frame face.
                # Do not assume a face is visible just because a track still carries a student label.
                face_visible = bool(item.get('face_visible', item.get('face_detected', False)))
                if track_id > 0 and missed == 0 and direct_identity and face_visible:
                    cache[track_id] = {
                        'student_id': student_id,
                        'student': label,
                        'last_confirmed': now_mono,
                        'box': tuple(item.get('box') or ()),
                        # Keep the last real detector item as the geometry and
                        # metadata source for a short no-detection grace period.
                        'last_item': dict(item),
                    }
                    visible_at[student_id] = now_mono
                elif not direct_identity or not face_visible:
                    # Keep a recently verified identity visible, but never count a cached ID as a
                    # fresh detection or use it to generate a new observation while the face is lost.
                    item['_identity_grace'] = True
                    item['missed'] = max(1, missed)
                verified_items.append(item)
                continue

            # An explicitly uncertain face must not inherit a previous student's identity.
            # Keep the old identity for a short visual grace period only when no face is
            # currently visible AND the box geometry still matches the previously verified track.
            if track_id > 0 and (not label or label.upper() in uncertain_labels) and missed <= 12:
                cached = cache.get(track_id)
                current_face_visible = bool(item.get('face_visible', item.get('face_detected', False)))
                can_hold_identity = False
                cached_id = 0
                age = float('inf')
                if cached and not current_face_visible:
                    try:
                        cached_id = int(cached.get('student_id') or 0)
                        age = now_mono - float(cached.get('last_confirmed') or 0.0)
                        old_box = tuple(float(v) for v in cached.get('box', ()))
                        new_box = tuple(float(v) for v in item.get('box', ()))
                        if len(old_box) == 4 and len(new_box) == 4 and min(old_box[2], old_box[3], new_box[2], new_box[3]) > 4:
                            ox, oy, ow, oh = old_box
                            nx, ny, nw, nh = new_box
                            old_cx, old_cy = ox + ow * 0.5, oy + oh * 0.5
                            new_cx, new_cy = nx + nw * 0.5, ny + nh * 0.5
                            center_scale = max(60.0, 0.5 * (oh + nh))
                            center_shift = math.hypot(old_cx - new_cx, old_cy - new_cy) / center_scale
                            old_area = max(1.0, ow * oh)
                            new_area = max(1.0, nw * nh)
                            size_ratio = max(old_area, new_area) / min(old_area, new_area)
                            can_hold_identity = (cached_id in allowed_ids and 0.0 <= age <= 1.25
                                                 and center_shift <= 0.55 and size_ratio <= 2.5)
                    except Exception:
                        can_hold_identity = False

                if can_hold_identity:
                    item['student_id'] = cached_id
                    item['student'] = str(cached.get('student') or 'IDENTITY UNCERTAIN')
                    # Visual continuity only: neutral color, no fresh attendance/event.
                    item['_identity_grace'] = True
                    item['face_visible'] = False
                    item['face_detected'] = False
                    item['missed'] = max(1, missed)
                    verified_items.append(item)
                else:
                    # Keep a neutral box if the tracker supplies usable geometry.
                    # A visible-but-unmatched face is never given a cached identity.
                    try:
                        raw_box = tuple(float(v) for v in item.get('box', ()))
                    except Exception:
                        raw_box = ()
                    if len(raw_box) == 4 and raw_box[2] > 4 and raw_box[3] > 4:
                        item['student_id'] = 0
                        item['student'] = 'IDENTITY UNCERTAIN'
                        item['_unidentified_face_box'] = True
                        item['_identity_grace'] = False
                        item['face_visible'] = current_face_visible
                        item['face_detected'] = current_face_visible
                        verified_items.append(item)

        # A track can disappear from the detector payload entirely for one or
        # more inference cycles. Reuse only a recently verified, selected track
        # and only for the existing short grace window. Mark it missed so it is
        # not counted as a fresh presence or used to emit new events/evidence.
        verified_track_ids = set()
        for verified_item in verified_items:
            try:
                verified_tid = int(verified_item.get('track_id') or 0)
                if verified_tid > 0:
                    verified_track_ids.add(verified_tid)
            except Exception:
                continue

        for tid, cached in list(cache.items()):
            if tid in verified_track_ids or tid in disallowed_track_ids:
                continue
            try:
                cached_id = int(cached.get('student_id') or 0)
                age = now_mono - float(cached.get('last_confirmed') or 0.0)
            except Exception:
                continue
            if cached_id not in allowed_ids or age < 0.0 or age > 1.25:
                continue

            base_item = previous_overlay_by_track.get(tid) or cached.get('last_item')
            if not isinstance(base_item, dict):
                continue
            held_item = dict(base_item)
            box = tuple(held_item.get('box') or cached.get('box') or ())
            if len(box) != 4:
                continue
            roster_match = next(
                (r for r in self.selected_server_roster if int(r.get('id') or 0) == cached_id),
                None,
            )
            fallback_label = str(cached.get('student') or '')
            if roster_match:
                code = str(roster_match.get('student_code') or '').strip()
                name = str(roster_match.get('full_name') or '').strip()
                fallback_label = f'{code} • {name}' if code and name else (name or code or fallback_label)
            previous_missed = 0
            try:
                previous_missed = int(held_item.get('missed') or 0)
            except Exception:
                pass
            held_item.update({
                'track_id': int(tid),
                'student_id': cached_id,
                'student': fallback_label,
                'box': box,
                'missed': min(12, max(1, previous_missed + 1)),
                'face_visible': False,
                'face_detected': False,
                '_identity_grace': True,
            })
            verified_items.append(held_item)
            verified_track_ids.add(tid)

        self._verified_track_identity = cache
        self._student_last_visible_at = visible_at
        items = verified_items
        if self.pages.currentIndex()==3:
            grouped={}
            for item in items:
                label=str(item.get('student') or '')
                missed=int(item.get('missed') or 0)
                # A locked identity gets a short predicted-box grace period.
                # This prevents flicker while retaining the selected-student boundary.
                if missed>12:
                    continue
                rank=(float(item.get('face_match_score') or item.get('score') or 0.0),
                      float(item.get('score') or 0.0), int(item.get('track_id') or 0))
                group_key = (f'{label}__track_{int(item.get("track_id") or 0)}'
                             if item.get('_unidentified_face_box') else label)
                current=grouped.get(group_key)
                if current is None or rank>current[0]:
                    grouped[group_key]=(rank,item)
            items=[value[1] for value in grouped.values()]

        self.overlay_updated_at=time.monotonic()
        self.overlay_items=items

        # Re-anchor the camera-FPS follower to current, selected-student tracks only.
        if self.pages.currentIndex()==3:
            self._rt_box_by_track = {}
            self._rt_points = {}
            for item in items:
                try:
                    tid=int(item.get('track_id') or 0)
                    box=tuple(float(v) for v in item.get('box',(0,0,0,0)))
                    if tid>0:
                        self._rt_box_by_track[tid]=box
                except Exception:
                    continue

        for item in items:
            if item.get('missed') != 0:
                continue
            try:
                sid=int(item.get('student_id') or 0)
                if sid <= 0 and self.ai:
                    sid=int(self.ai.identity.student_id_for_label(item.get('student')) or 0)
            except Exception:
                sid=0
            if sid>0 and sid in allowed_ids:
                self.present_detected_ids.add(sid)
        self.student_count=len(self.present_detected_ids)
        self.mon_students.setText((f'{self.student_count} / {self.expected_student_count} selected students detected' if self.ui_language == 'en' else f'{self.student_count} / {self.expected_student_count} học sinh được chọn đã nhận diện'))
        if hasattr(self,'scan_count'):
            self.scan_count.setText((f'{self.student_count}/{self.expected_student_count} students identified' if self.ui_language == 'en' else f'{self.student_count}/{self.expected_student_count} học sinh đã được nhận diện'))
        if hasattr(self,'lock_button'):
            self.lock_button.setEnabled(self.face_scan_active and self.student_count > 0)
        self.scan_table.setRowCount(0)
        seen=set()
        for item in items:
            label=item.get('student')
            if label in seen:
                continue
            seen.add(label)
            row=self.scan_table.rowCount()
            self.scan_table.insertRow(row)
            if item.get('_unidentified_face_box'):
                state = 'Unidentified' if self.ui_language == 'en' else 'Chưa xác định'
            elif item.get('_identity_grace') or item.get('missed') != 0:
                state = 'Reacquiring' if self.ui_language == 'en' else 'Đang nhận diện lại'
            else:
                state = 'Visible' if self.ui_language == 'en' else 'Đang thấy'
            self.scan_table.setItem(row,0,QTableWidgetItem(str(label)))
            self.scan_table.setItem(row,1,QTableWidgetItem(state))
            self.scan_table.setItem(row,2,QTableWidgetItem(f"{float(item.get('face_match_score') or item.get('score') or 0.0)*100:.0f}%"))
        for absent_item in self.absent_server_roster:
            code=str(absent_item.get('student_code') or '').strip()
            name=str(absent_item.get('full_name') or '').strip()
            label=f'{code} • {name}' if code and name else (name or code or 'STUDENT')
            row=self.scan_table.rowCount()
            self.scan_table.insertRow(row)
            self.scan_table.setItem(row,0,QTableWidgetItem(label))
            self.scan_table.setItem(row,1,QTableWidgetItem('ABSENT' if self.ui_language == 'en' else 'VẮNG'))
            self.scan_table.setItem(row,2,QTableWidgetItem('EXCLUDED' if self.ui_language == 'en' else 'ĐÃ LOẠI TRỪ'))
        if hasattr(self,'attendance_summary'):
            self.attendance_summary.setText(
                (f'Selected present: {self.expected_student_count} • Confirmed by AI: {self.student_count} • Absent/excluded: {len(self.absent_server_roster)}' if self.ui_language == 'en' else
                 f'Có mặt đã chọn: {self.expected_student_count} • AI đã xác thực: {self.student_count} • Vắng/loại trừ: {len(self.absent_server_roster)}')
            )
        if items:
            self.scan_state.setText(
                (f'MONITORING • {self.student_count}/{self.expected_student_count} selected students verified' if self.ui_language == 'en' else
                 f'ĐANG GIÁM SÁT • {self.student_count}/{self.expected_student_count} học sinh được chọn đã xác thực')
            )

    # [AI-GENERATED / AI-ASSISTED CODE] Displays AI status in the interface.
    def _ai_status(self,text):
        self.ai_status_text=text
        if self.pages.currentIndex()==2:
            self.scan_state.setText(text)
        if self.current_session and self.pages.currentIndex()==3:
            self.mon_status.setText(text)
            if hasattr(self, 'mon_camera_status'):
                upper = str(text).upper()
                if 'ERROR' in upper or 'WAITING' in upper:
                    self.mon_camera_status.setText('AI WARNING')
                    self.mon_camera_status.setObjectName('status_warning')
                elif 'ONLINE' in upper or 'RESUMED' in upper:
                    self.mon_camera_status.setText('AI ONLINE')
                    self.mon_camera_status.setObjectName('status_success')
                self.mon_camera_status.style().unpolish(self.mon_camera_status)
                self.mon_camera_status.style().polish(self.mon_camera_status)

    # [AI-GENERATED / AI-ASSISTED CODE] Empty-state helper for the observation panel.
    def _reset_live_observations(self):
        if not hasattr(self, 'live'):
            return
        self.live.clear()
        item = QListWidgetItem('CHƯA CÓ QUAN SÁT\n\nCác Observation mới sẽ xuất hiện tại đây trong lúc AI giám sát buổi học.')
        item.setData(Qt.UserRole, '__EMPTY_OBSERVATION__')
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(self.palette().color(self.palette().ColorRole.PlaceholderText))
        item.setSizeHint(item.sizeHint())
        self.live.addItem(item)

    # [AI-GENERATED / AI-ASSISTED CODE] Saves AI events/evidence and synchronizes them to server.
    def _show_anonymous_behavior_event(self, typ, conf, assessment):
        """GOD EYES BEHAVIOR-ID SEPARATION V18 / FOCUS ALERTS V20: local-only, unidentified event."""
        # GOD EYES FOCUS ALERTS V20: lower head-turn threshold only for local anonymous alerts.
        if not self.current_session or not hasattr(self, 'live'):
            return
        try:
            confidence = float(conf or 0.0)
        except (TypeError, ValueError):
            return
        if not math.isfinite(confidence):
            return
        if 1.0 < confidence <= 100.0:
            confidence /= 100.0
        if not 0.0 <= confidence <= 1.0:
            return
        event_type = str(typ or 'OBSERVATION')
        # Engine caps OB_TALKING at 0.90; a 0.93 UI floor made it impossible to show.
        # Events already require sustained signals in BehaviorEngine. These are
        # local-only anonymous observations, so keep thresholds attainable while
        # still labelling them as suspicions rather than confirmed student records.
        thresholds = {
            'HEAD_TURN_LEFT': 0.82,
            'HEAD_TURN_RIGHT': 0.82,
            'OB_TALKING': 0.78,
            'OB_DISTRACT': 0.85,
            'OB_SLEEP': 0.90,
        }
        if confidence < thresholds.get(event_type, 0.90):
            return
        import re
        text_detail = str(assessment or '')
        match = re.search(r'Track\s*#\s*(\d+)', text_detail, re.IGNORECASE)
        track_key = match.group(1) if match else '?'
        now_mono = time.monotonic()
        cooldowns = getattr(self, '_anonymous_event_last_at', {})
        key = (track_key, event_type)
        if now_mono - float(cooldowns.get(key, 0.0) or 0.0) < 30.0:
            return
        cooldowns[key] = now_mono
        self._anonymous_event_last_at = cooldowns
        titles = {
            'HEAD_TURN_LEFT': 'NGHI VẤN MẤT TẬP TRUNG • QUAY ĐẦU TRÁI',
            'HEAD_TURN_RIGHT': 'NGHI VẤN MẤT TẬP TRUNG • QUAY ĐẦU PHẢI',
            'OB_TALKING': 'NGHI VẤN NÓI CHUYỆN / QUAY KHỎI CAMERA',
            'OB_DISTRACT': 'NGHI VẤN MẤT TẬP TRUNG',
            'OB_SLEEP': 'NGHI VẤN NGỦ GẬT',
        }
        title = titles.get(event_type, event_type)
        if self.live.count() and self.live.item(0).data(Qt.UserRole) == '__EMPTY_OBSERVATION__':
            self.live.takeItem(0)
        note = (
            f'{title}\n{time.strftime("%H:%M:%S")} • Người chưa xác định (Track #{track_key})\n'
            f'Độ tin cậy: {confidence * 100:.0f}%\n{text_detail}\n'
            'Chưa gán cho học sinh; sự kiện này chỉ hiển thị tạm thời trên máy.'
        )
        row = QListWidgetItem(note)
        row.setData(Qt.UserRole, '__ANONYMOUS_ONLY__')
        self.live.insertItem(0, row)
        try:
            row.setSizeHint(row.sizeHint())
        except Exception:
            pass
        while self.live.count() > 12:
            self.live.takeItem(12)
        self.event_count = int(getattr(self, 'event_count', 0) or 0) + 1
        if hasattr(self, 'mon_events'):
            self.mon_events.setText(f'{self.event_count} events')

    def _event(self,sid,typ,conf,assessment,evidence):
        # GOD EYES ANONYMOUS TURN ALERT V23.2
        # Unidentified turn alerts are shown locally only. Never persist evidence,
        # create a student record, or send an anonymous event to the Server.
        _v232_label = str(sid or '').strip().upper()
        if _v232_label in {'IDENTITY UNCERTAIN', 'UNKNOWN', 'UNCERTAIN', 'CHƯA XÁC ĐỊNH DANH TÍNH'}:
            if not getattr(self, 'current_session', None) or getattr(self, 'ai', None) is None:
                return
            _v232_type = str(typ or 'OBSERVATION').strip().upper()
            if _v232_type not in {'HEAD_TURN_LEFT', 'HEAD_TURN_RIGHT'}:
                return
            try:
                _v232_conf = float(conf or 0.0)
            except (TypeError, ValueError):
                return
            if 1.0 < _v232_conf <= 100.0:
                _v232_conf /= 100.0
            if not math.isfinite(_v232_conf) or not (0.82 <= _v232_conf <= 1.0):
                return
            _v232_assessment = str(assessment or '').strip()
            _v232_track = _v232_assessment.split('•', 1)[0].strip() or 'Track chưa xác định'
            _v232_now = time.monotonic()
            _v232_seen = getattr(self, '_anonymous_turn_alert_last', None)
            if not isinstance(_v232_seen, dict):
                _v232_seen = {}
                self._anonymous_turn_alert_last = _v232_seen
            _v232_key = (_v232_track, _v232_type)
            if _v232_now - float(_v232_seen.get(_v232_key, 0.0) or 0.0) < 30.0:
                return
            _v232_seen[_v232_key] = _v232_now
            if not hasattr(self, 'live'):
                return
            try:
                if self.live.count() and self.live.item(0).data(Qt.UserRole) == '__EMPTY_OBSERVATION__':
                    self.live.takeItem(0)
            except Exception:
                pass
            _v232_en = str(getattr(self, 'ui_language', 'vi') or 'vi').lower().startswith('en')
            if _v232_en:
                _v232_title = 'POSSIBLE DISTRACTION • HEAD TURN LEFT' if _v232_type == 'HEAD_TURN_LEFT' else 'POSSIBLE DISTRACTION • HEAD TURN RIGHT'
                _v232_text = f'{_v232_title}\n{time.strftime("%H:%M:%S")} • {_v232_track}\nConfidence: {_v232_conf * 100:.0f}%\nLocal-only warning; identity is not confirmed.'
            else:
                _v232_title = 'NGHI VẤN MẤT TẬP TRUNG • QUAY ĐẦU TRÁI' if _v232_type == 'HEAD_TURN_LEFT' else 'NGHI VẤN MẤT TẬP TRUNG • QUAY ĐẦU PHẢI'
                _v232_text = f'{_v232_title}\n{time.strftime("%H:%M:%S")} • {_v232_track}\nĐộ tin cậy: {_v232_conf * 100:.0f}%\nChỉ hiển thị cục bộ; danh tính chưa được xác nhận.'
            _v232_item = QListWidgetItem(_v232_text)
            _v232_item.setData(Qt.UserRole, '__LOCAL_ANONYMOUS_TURN__')
            self.live.insertItem(0, _v232_item)
            while self.live.count() > 12:
                self.live.takeItem(12)
            return


        if not self.current_session or not self.ai:
            return
        label = str(sid or '').strip()
        if label == 'IDENTITY UNCERTAIN':
            self._show_anonymous_behavior_event(typ, conf, assessment)
            return
        if not label:
            return
        try:
            resolved_student_id = int(self.ai.identity.student_id_for_label(label) or 0)
        except Exception:
            resolved_student_id = 0
        selected_ids = {int(item.get('id') or 0) for item in self.selected_server_roster}
        # Fail closed: no local evidence, observation row, or upload for an
        # unselected / unidentified person, even if the engine emits an event.
        if resolved_student_id <= 0 or resolved_student_id not in selected_ids:
            return

        # Normalize percentage-shaped confidence values from older engine builds.
        try:
            confidence = float(conf or 0.0)
        except (TypeError, ValueError):
            return
        if not math.isfinite(confidence):
            return
        if 1.0 < confidence <= 100.0:
            confidence /= 100.0
        if confidence < 0.0 or confidence > 1.0:
            return

        event_type = str(typ or 'OBSERVATION')
        # Conservative event gates reduce low-confidence false alarms without changing
        # the face detector itself. Thresholds are applied before writing evidence or syncing.
        confidence_thresholds = {
            'HEAD_TURN_LEFT': 0.82,
            'HEAD_TURN_RIGHT': 0.82,
            # Thresholds align with the engine's bounded confidence; temporal gates remain the primary false-alert filter.
            'OB_TALKING': 0.84,
            'OB_DISTRACT': 0.74,
            'OB_SLEEP': 0.70,
        }
        if confidence < confidence_thresholds.get(event_type, 0.92):
            return

        now_mono = time.monotonic()
        last_visible = float(getattr(self, '_student_last_visible_at', {}).get(resolved_student_id, 0.0) or 0.0)
        # Don't log behaviour attributed to a student after their verified face has been missing.
        # Sleep may be reported after a verified head-down sequence followed by face loss; keep attribution only within that bounded interval.
        max_face_age = 10.0 if event_type == 'OB_SLEEP' else (5.0 if event_type in ('HEAD_TURN_LEFT', 'HEAD_TURN_RIGHT') else 1.75)
        if last_visible <= 0.0 or now_mono - last_visible > max_face_age:
            return
        last_student_event = float(getattr(self, '_student_last_alert_at', {}).get(resolved_student_id, 0.0) or 0.0)
        event_key = (resolved_student_id, event_type)
        last_same_event = float(getattr(self, '_event_last_saved_at', {}).get(event_key, 0.0) or 0.0)
        if now_mono - last_student_event < 10.0 or now_mono - last_same_event < 30.0:
            return
        conf = confidence
        event_assessment=str(assessment or event_type)
        titles={
            'HEAD_TURN_LEFT':'QUAY ĐẦU TRÁI',
            'HEAD_TURN_RIGHT':'QUAY ĐẦU PHẢI',
            'OB_TALKING':'NGHI VẤN NÓI CHUYỆN',
            'OB_DISTRACT':'CÚI ĐẦU / NGHI VẤN MẤT TẬP TRUNG',
            'OB_SLEEP':'NGHI VẤN NGỦ GẬT'
        }
        title=titles.get(event_type,event_type)
        if self.live.count() and self.live.item(0).data(Qt.UserRole)=='__EMPTY_OBSERVATION__':self.live.takeItem(0)
        stamp=time.strftime('%H-%M-%S')
        path=EVIDENCE_DIR/self.current_session/f'{sid.replace(" ","_")}_{stamp}_{event_type}.jpg'
        path.parent.mkdir(parents=True,exist_ok=True)
        # Never create an event row or consume the cooldown without a valid evidence frame.
        if not isinstance(evidence, np.ndarray) or evidence.size == 0:
            return
        try:
            saved = cv2.imwrite(str(path),evidence,[cv2.IMWRITE_JPEG_QUALITY,90])
        except Exception:
            saved = False
        if not saved:
            return
        self._student_last_alert_at[resolved_student_id] = now_mono
        self._event_last_saved_at[event_key] = now_mono
        self.db.add_event(self.current_session,sid,event_type,conf,event_assessment,path)
        self.event_count+=1
        self.mon_events.setText(f'{self.event_count} events')
        event_text=(f'{title}\n{time.strftime("%H:%M:%S")}  •  {sid}\nConfidence: {conf*100:.0f}%\n{event_assessment}')
        item=QListWidgetItem(event_text); item.setData(Qt.UserRole,str(path)); self.live.insertItem(0,item)
        try:item.setSizeHint(item.sizeHint())
        except Exception:pass
        while self.live.count()>12:self.live.takeItem(12)
        if self.server_session_id and self.server.connected and self.ai:
            student_id=resolved_student_id
            if student_id>0 and student_id in selected_ids:
                ok,encoded=cv2.imencode('.jpg',evidence,[cv2.IMWRITE_JPEG_QUALITY,90])
                if ok:
                    captured_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
                    future=self.server_executor.submit(self._sync_event_remote,self.server_session_id,student_id,sid,event_type,float(conf),event_assessment,captured_at,bytes(encoded))
                    self.remote_futures.append(future)

    def _sync_event_remote(self,server_session_id,student_id,sid,typ,conf,assessment,captured_at,image_bytes):
        try:
            evidence_response=self.server.upload_evidence(server_session_id, student_id, typ, conf, captured_at, image_bytes)
            evidence_id=int(evidence_response.get('id') or 0)
            self.server.post_events(server_session_id,[{
                'student_id':student_id,
                'student_code':str(sid).split(' • ',1)[0][:80],
                'full_name':str(sid).split(' • ',1)[1][:160] if ' • ' in str(sid) else str(sid)[:160],
                'observed_at':captured_at,
                'event_type':typ,
                'confidence':conf,
                'assessment':assessment,
                'details':assessment,
                'evidence_id':evidence_id,
            }])
            self.server_sync_text=f'SERVER SYNC • OBSERVATION • #{evidence_id}'
        except Exception as exc:
            self.server_sync_text=f'SERVER SYNC ERROR • {exc}'

    def _sync_render_rate_to_camera(self):
        """Match UI paint cadence to the camera's reported FPS.

        This changes only the paint timer. AI inference stays independent, so
        camera-rate overlay updates do not increase YOLO/YuNet/SFace workload.
        """
        camera = self.camera
        if camera is None:
            return

        try:
            fps = float(getattr(camera, 'actual_fps', 0.0) or 0.0)
        except Exception:
            fps = 0.0

        if fps <= 1.0:
            return

        # Exact enough for common 15/20/24/25/30/50/60 FPS cameras.
        interval_ms = int(round(1000.0 / max(1.0, min(120.0, fps))))
        interval_ms = max(8, min(66, interval_ms))

        if interval_ms != self._rt_render_interval_ms:
            self._rt_render_interval_ms = interval_ms
            try:
                self.render_timer.setInterval(interval_ms)
            except Exception:
                pass

    def _reset_realtime_overlay_tracker(self):
        self._rt_prev_gray = None
        self._rt_prev_sequence = 0
        self._rt_points = {}
        self._rt_box_by_track = {}
        self._rt_last_render_sequence = 0

    @staticmethod
    def _flow_points_for_box(gray, box):
        try:
            x, y, w, h = [int(round(v)) for v in box]
            H, W = gray.shape[:2]
            x1 = max(0, min(W - 2, x))
            y1 = max(0, min(H - 2, y))
            x2 = max(x1 + 2, min(W - 1, x + max(8, w)))
            y2 = max(y1 + 2, min(H - 1, y + max(8, h)))
            roi = gray[y1:y2, x1:x2]
            if roi.size < 80:
                return None

            # Sparse corners are far cheaper than dense optical flow and are
            # sufficient for translating a locked person box at camera FPS.
            pts = cv2.goodFeaturesToTrack(
                roi,
                maxCorners=14,
                qualityLevel=0.018,
                minDistance=max(5, int(min(w, h) * 0.06)),
                blockSize=7,
                useHarrisDetector=False,
            )
            if pts is None:
                # Stable fallback points if the person's clothes/region has few
                # detectable corners.
                cx = x1 + 0.5 * max(2, x2 - x1)
                cy = y1 + 0.5 * max(2, y2 - y1)
                return np.array(
                    [[[cx, cy], [cx + 0.20 * max(4, w), cy],
                      [cx, cy + 0.18 * max(4, h)]]],
                    dtype=np.float32,
                ).reshape(-1, 1, 2)

            pts[:, 0, 0] += x1
            pts[:, 0, 1] += y1
            return pts.astype(np.float32)
        except Exception:
            return None

    def _realtime_follow_overlay(self, frame):
        """Move locked boxes on every camera frame between AI detections.

        Optical flow supplies the per-frame translation. A very small velocity
        fallback is used when the flow points temporarily disappear. The AI box
        is still the authoritative anchor and periodically corrects all drift.
        """
        if self.pages.currentIndex() != 3 or not self.overlay_items:
            self._rt_prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            try:
                self._rt_prev_sequence = int(self.camera.latest_sequence())
            except Exception:
                self._rt_prev_sequence = 0
            self._rt_points.clear()
            self._rt_box_by_track.clear()
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        try:
            seq = int(self.camera.latest_sequence())
        except Exception:
            seq = self._rt_prev_sequence + 1

        # Skip duplicate camera frames.
        if self._rt_prev_gray is None or seq == self._rt_prev_sequence:
            self._rt_prev_gray = gray
            self._rt_prev_sequence = seq
            return

        prev_gray = self._rt_prev_gray

        active_ids = set()
        updated_items = []

        for item in self.overlay_items:
            try:
                tid = int(item.get('track_id') or 0)
                bx, by, bw, bh = [float(v) for v in item.get('box', (0, 0, 0, 0))]
            except Exception:
                continue

            if tid <= 0 or bw <= 4 or bh <= 4:
                continue

            active_ids.add(tid)

            # On each AI refresh, the payload becomes the authoritative anchor.
            # Between refreshes, _rt_box_by_track carries the live camera-rate box.
            current_box = self._rt_box_by_track.get(tid, (bx, by, bw, bh))
            points = self._rt_points.get(tid)

            if points is None or len(points) < 3:
                points = self._flow_points_for_box(prev_gray, current_box)

            dx = dy = 0.0
            flow_ok = False

            if points is not None and len(points) >= 3:
                try:
                    next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                        prev_gray,
                        gray,
                        points,
                        None,
                        winSize=(17, 17),
                        maxLevel=2,
                        criteria=(
                            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                            12,
                            0.025,
                        ),
                    )

                    if next_pts is not None and status is not None:
                        good_prev = points[status.reshape(-1) == 1]
                        good_next = next_pts[status.reshape(-1) == 1]

                        if len(good_prev) >= 3:
                            delta = good_next - good_prev
                            dx = float(np.median(delta[:, 0]))
                            dy = float(np.median(delta[:, 1]))

                            # Prevent a single erroneous flow vector from
                            # launching the locked box across the frame.
                            max_step = max(
                                8.0,
                                min(0.22 * max(current_box[3], 30.0), 55.0),
                            )
                            dx = float(np.clip(dx, -max_step, max_step))
                            dy = float(np.clip(dy, -max_step, max_step))
                            flow_ok = True

                            kept = good_next.reshape(-1, 1, 2).astype(np.float32)
                            self._rt_points[tid] = kept

                except Exception:
                    flow_ok = False

            if not flow_ok:
                # Fallback to the velocity supplied by the AI tracker, capped
                # to one camera-frame-sized step.
                try:
                    vx, vy, _, _ = [
                        float(v) for v in
                        (item.get('box_follow_velocity') or (0, 0, 0, 0))
                    ]
                    camera_fps = float(getattr(self.camera, 'actual_fps', 30.0) or 30.0)
                    dt = 1.0 / max(10.0, min(120.0, camera_fps))
                    dx = float(np.clip(vx * dt, -55.0, 55.0))
                    dy = float(np.clip(vy * dt, -55.0, 55.0))
                except Exception:
                    dx = dy = 0.0

                # Force point reinitialization on the next frame.
                self._rt_points.pop(tid, None)

            nb = (
                current_box[0] + dx,
                current_box[1] + dy,
                current_box[2],
                current_box[3],
            )
            self._rt_box_by_track[tid] = nb

            out = dict(item)
            out['box'] = tuple(int(round(v)) for v in nb)
            updated_items.append(out)

        # Remove stale per-track flow state.
        for tid in list(self._rt_points):
            if tid not in active_ids:
                self._rt_points.pop(tid, None)
        for tid in list(self._rt_box_by_track):
            if tid not in active_ids:
                self._rt_box_by_track.pop(tid, None)

        self.overlay_items = updated_items
        self._rt_prev_gray = gray
        self._rt_prev_sequence = seq
        self._rt_last_render_sequence = seq

    # [AI-GENERATED / AI-ASSISTED CODE] Draws student boxes, IDs, and observation metadata.
    def _draw_overlay(self,frame):
        out=frame.copy()
        H,W=out.shape[:2]
        unicode_labels = []
        for p in self.overlay_items:
            sid=str(p.get('student') or 'IDENTITY UNCERTAIN')
            reacquiring=bool(p.get('_identity_grace'))
            uncertain=(sid.upper() in {'IDENTITY UNCERTAIN', 'CHƯA XÁC ĐỊNH DANH TÍNH', 'UNKNOWN', 'UNCERTAIN'})
            face_box=p.get('face_box')
            face_visible=bool(p.get('face_visible', p.get('face_detected', False)))
            use_face_box=bool(
                uncertain and not reacquiring and face_visible
                and isinstance(face_box, (tuple, list)) and len(face_box) >= 4
            )
            try:
                chosen_box = face_box if use_face_box else p.get('box', (0,0,0,0))
                bx,by,bw,bh=[float(v) for v in chosen_box[:4]]
            except Exception:
                continue

            if not use_face_box:
                # Person-box velocity is valid for the person track only. Do not
                # apply it to the separate current-frame face rectangle.
                try:
                    updated_at = float(p.get('box_updated_at') or 0.0)
                    age = max(0.0, min(0.16, time.time() - updated_at))
                    vx,vy,vw,vh = [
                        float(v) for v in (p.get('box_follow_velocity') or (0.0,0.0,0.0,0.0))
                    ]
                    bx += vx * age
                    by += vy * age
                    bw += vw * age
                    bh += vh * age
                except Exception:
                    pass

            x,y,w,h=[int(round(v)) for v in (bx,by,bw,bh)]
            if w<=4 or h<=4:
                continue
            # Clamp immediately to the current frame. Never draw an old box outside
            # the current image after a resize/crop change.
            x=max(0,min(W-2,x)); y=max(0,min(H-2,y))
            w=max(8,min(W-x-2,w)); h=max(8,min(H-y-2,h))
            sid=str(p.get('student') or 'IDENTITY UNCERTAIN')
            reacquiring = bool(p.get('_identity_grace'))
            uncertain=(sid.upper() in {'IDENTITY UNCERTAIN', 'CHƯA XÁC ĐỊNH DANH TÍNH', 'UNKNOWN', 'UNCERTAIN'})
            # A grace box stays neutral and explicitly says the face is being
            # reacquired; it must not look like a fresh positive recognition.
            pen=(145,158,174) if (uncertain or reacquiring) else (35,100,235)
            # Thick high-visibility rectangle so the box is readable at 720p and
            # remains easy to follow while the student moves.
            box_thickness=4 if min(W,H)>=720 else 3
            cv2.rectangle(out,(x,y),(x+w,y+h),pen,box_thickness,cv2.LINE_AA)

            # A short neutral label preserves the visible frame without claiming
            # that Face ID has been confirmed in the current frame.
            if reacquiring:
                text = 'REACQUIRING FACE' if self.ui_language == 'en' else 'ĐANG NHẬN DIỆN LẠI'
            elif uncertain:
                text = 'UNIDENTIFIED' if self.ui_language == 'en' else 'CHƯA XÁC ĐỊNH'
            else:
                text=sid
            scale=0.70 if min(W,H)>=720 else 0.60
            font_px=11 if min(W,H)<720 else 13
            label_font=QFont('Segoe UI',font_px,QFont.Weight.DemiBold)
            font_metrics=QFontMetrics(label_font)
            # OpenCV Hershey metrics do not measure Vietnamese Unicode correctly.
            # Size with the same Qt font used to paint the label, then elide safely.
            tw=font_metrics.horizontalAdvance(str(text))
            # Fit the label against the frame width, not the remaining space to the right
            # of the face. This prevents a near-edge face from collapsing the label panel.
            label_w=min(max(64,tw+24),max(1,W-4))
            label_h=max(38,font_metrics.height()+14)
            # Put label above box when there is room; otherwise place inside top.
            if y>=label_h+8:
                top=y
            else:
                top=min(H-4,y+label_h)
            left=max(2,min(x,max(2,W-label_w-2)))
            right=min(W-2,left+label_w)
            label_top=max(2,top-label_h)
            label_bottom=min(H-2,top)
            # OpenCV's built-in Hershey fonts do not support Vietnamese diacritics.
            # Keep boxes in OpenCV, but draw their labels with Qt/Segoe UI in one
            # batch so student names render as Unicode without converting each box.
            unicode_labels.append((left, label_top, right, label_bottom, str(text), tuple(pen), scale))

            if not uncertain and not reacquiring:
                # Keep secondary telemetry smaller so it does not compete with identity.
                yaw=float(p.get('head_yaw_deg',0.0))
                mouth=float(p.get('mouth_activity',0.0))
                task=float(p.get('task_activity',0.0))
                meta=f'ID #{int(p.get("track_id") or 0)}   Yaw {yaw:+.0f}°   Mouth {mouth*100:.0f}%   Task {task*100:.0f}%'
                meta_y=min(H-10,y+h+24)
                cv2.putText(out,meta,(x+4,meta_y),cv2.FONT_HERSHEY_SIMPLEX,.46,(35,100,235),2,cv2.LINE_AA)
        if unicode_labels:
            rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
            qimage = QImage(rgb.data, int(W), int(H), int(rgb.strides[0]), QImage.Format_RGB888).copy()
            painter = QPainter(qimage)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            for left, label_top, right, label_bottom, label_text, pen_bgr, scale in unicode_labels:
                rect = QRect(int(left), int(label_top), max(1, int(right-left)), max(1, int(label_bottom-label_top)))
                painter.fillRect(rect, QColor(248, 251, 255))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(int(pen_bgr[2]), int(pen_bgr[1]), int(pen_bgr[0])), 2))
                painter.drawRoundedRect(rect, 4, 4)
                font_px = 11 if min(W, H) < 720 else 13
                label_font = QFont('Segoe UI', font_px, QFont.Weight.DemiBold)
                painter.setFont(label_font)
                text_rect = QRect(int(left + 12), int(label_top), max(1, int(right-left-18)), max(1, int(label_bottom-label_top)))
                # Elide oversized labels with an ellipsis instead of drawing text beyond the video frame.
                visible_label = QFontMetrics(label_font).elidedText(str(label_text), Qt.TextElideMode.ElideRight, text_rect.width())
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, visible_label)
            painter.end()
            qimage = qimage.convertToFormat(QImage.Format_RGB888)
            raw = np.frombuffer(qimage.constBits(), dtype=np.uint8, count=qimage.sizeInBytes())
            rendered = raw.reshape(qimage.height(), qimage.bytesPerLine())[:, :qimage.width()*3]
            rendered = rendered.reshape(qimage.height(), qimage.width(), 3).copy()
            out = cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)
        return out

    # [AI-GENERATED / AI-ASSISTED CODE] Renders camera frames into the current UI page.
    def _render(self):
        frame=None
        if self.camera is not None:
            try:
                self._sync_render_rate_to_camera()
                frame=self.camera.get_latest_frame(copy=False)
            except Exception:
                frame=None
        if frame is None:
            frame=self.latest
        if frame is None:
            return

        idx=self.pages.currentIndex()
        if idx == 1 and hasattr(self, 'camera_preview_badge'):
            self.camera_preview_badge.setText('LIVE • CONNECTED' if self.ui_language == 'en' else 'TRỰC TIẾP • ĐÃ KẾT NỐI')

        # Advance the locked box on every available camera frame. This is
        # deliberately separate from AI inference.
        if idx == 3:
            self._realtime_follow_overlay(frame)

        frame=self._draw_overlay(frame) if idx in (2,3) else frame
        target={1:getattr(self,'camera_settings_preview',None),2:getattr(self,'scan_preview',None),3:getattr(self,'mon_preview',None)}.get(idx)
        if target:target.setPixmap(pixmap(frame,target.size(),fill=True))

    def _start_audio_monitor(self):
        if self.audio_monitor is not None and self.audio_monitor.isRunning():
            return

        self._stop_camera_audio_test(show_status=False)
        self.audio_monitor_error_text = ''
        rtsp_url = ''
        try:
            source, idx, host, port, stream, username, password = self._camera_config_from_widgets()
            self.camera_source, self.camera_device_index, self.camera_host = source, idx, host
            self.camera_port, self.camera_stream = port, stream
            self.camera_username, self.camera_password = username, password
        except Exception:
            source = str(getattr(self, 'camera_source', '')).lower()
        if str(source).lower() == 'wifi_camera':
            rtsp_url = self._build_camera_url(
                self.camera_source, self.camera_host, self.camera_port,
                self.camera_stream, self.camera_username, self.camera_password
            )

        worker = AudioLevelWorker(self, rtsp_url=rtsp_url)
        worker.level_power.connect(self._audio_level_received)
        worker.audio_error.connect(self._audio_monitor_error)
        self.audio_monitor = worker
        if rtsp_url:
            message = (
                'Đang đọc âm thanh từ camera RTSP… Nếu chưa có số dBFS, hãy thử nút “KIỂM TRA ÂM THANH RTSP” ở mục Camera.'
                if self.ui_language != 'en' else
                'Reading audio from the camera RTSP stream… If dBFS stays blank, use “TEST RTSP AUDIO” on the Camera page.'
            )
        else:
            message = (
                'Đang lấy mẫu âm thanh từ microphone máy tính…'
                if self.ui_language != 'en' else
                'Reading audio from the computer microphone…'
            )
        self.audio_status_label.setText(message)
        worker.start()

    def _audio_monitor_error(self, message):
        self.audio_monitor_error_text = str(message or '')
        if hasattr(self, 'audio_status_label'):
            short = self.audio_monitor_error_text[:260]
            if str(getattr(self, 'camera_source', '')).lower() == 'wifi_camera':
                prefix_vi = 'Không đọc được âm thanh từ camera RTSP: '
                prefix_en = 'Could not read audio from the camera RTSP stream: '
            else:
                prefix_vi = 'Không đo được âm thanh từ microphone máy tính: '
                prefix_en = 'Could not read audio from the computer microphone: '
            self.audio_status_label.setText(
                (prefix_vi if self.ui_language != 'en' else prefix_en) + short
            )

    def _audio_level_received(self, power):
        try:
            power = max(0.0, float(power))
            if not math.isfinite(power):
                return
        except Exception:
            return
        dbfs = 10.0 * math.log10(max(power, 1e-12))
        dbfs = max(-120.0, min(0.0, dbfs))
        self.audio_last_dbfs = dbfs
        if self.current_session is not None and not self.paused and self.session_start is not None:
            self.audio_power_sum += power
            self.audio_samples_count += 1
        self.audio_avg_dbfs = self._audio_average_dbfs()
        if hasattr(self, 'audio_current_label'):
            current_prefix = 'Current:' if self.ui_language == 'en' else 'Hiện tại:'
            average_prefix = 'Session average:' if self.ui_language == 'en' else 'Trung bình buổi học:'
            avg_text = f'{self.audio_avg_dbfs:.1f} dBFS' if self.audio_avg_dbfs is not None else ('Accumulating…' if self.ui_language == 'en' else 'Đang tích lũy…')
            self.audio_current_label.setText(f'{current_prefix} {dbfs:.1f} dBFS')
            self.audio_level_bar.setValue(max(0, min(60, int(round(dbfs + 60)))))
            self.audio_average_label.setText(f'{average_prefix} {avg_text}')
            if not self.audio_monitor_error_text:
                self.audio_status_label.setText(
                    'Energy-averaged over active monitoring time; paused time is excluded.'
                    if self.ui_language == 'en' else
                    'Tính trung bình theo năng lượng trong thời gian giám sát; tạm dừng sẽ không cộng mẫu.'
                )

    def _audio_average_dbfs(self):
        if not self.audio_samples_count or self.audio_power_sum <= 0:
            return None
        return max(-120.0, min(0.0, 10.0 * math.log10(max(self.audio_power_sum / self.audio_samples_count, 1e-12))))

    def _stop_audio_monitor(self):
        worker = getattr(self, 'audio_monitor', None)
        if worker is None:
            return
        try:
            worker.stop()
            if worker.isRunning():
                worker.wait(1800)
        except Exception:
            pass
        if not worker.isRunning():
            self.audio_monitor = None

    # [AI-GENERATED / AI-ASSISTED CODE] Session timer and mandatory force-stop watchdog.
    def _arm_session_watchdog(self):
        """Start an OS-thread timer that can stop AI even if the Qt UI is blocked."""
        self._cancel_session_watchdog()
        if not self.current_session or self.session_deadline_monotonic is None:
            return
        session_key = str(self.current_session)
        worker = self.ai
        delay = max(0.01, float(self.session_deadline_monotonic) - time.monotonic())
        timer = threading.Timer(delay, self._session_watchdog_expired, args=(session_key, worker))
        timer.daemon = True
        with self._session_watchdog_lock:
            self._session_watchdog_key = session_key
            self._session_watchdog_timer = timer
        timer.start()

    def _cancel_session_watchdog(self):
        with self._session_watchdog_lock:
            timer = self._session_watchdog_timer
            self._session_watchdog_timer = None
            self._session_watchdog_key = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _session_watchdog_expired(self, session_key, worker):
        # This callback runs outside the Qt event loop. It only stops the captured
        # AI worker here; all database/UI cleanup is delivered through a Qt signal.
        with self._session_watchdog_lock:
            if self._session_watchdog_key != str(session_key):
                return
            self._session_watchdog_timer = None
        workers = []
        if worker is not None:
            workers.append(worker)
        # Audio monitoring is supplemental to scanning; stop it too at the limit.
        audio_worker = getattr(self, 'audio_monitor', None)
        if audio_worker is not None and audio_worker not in workers:
            workers.append(audio_worker)
        for active_worker in workers:
            try:
                active_worker.stop()
            except Exception:
                pass
            try:
                if active_worker.isRunning():
                    active_worker.wait(300)
                if active_worker.isRunning():
                    active_worker.terminate()
                    active_worker.wait(300)
            except Exception:
                pass
        self.force_timeout_signal.emit(str(session_key))

    def _handle_force_timeout_signal(self, session_key):
        if self.current_session is not None and str(self.current_session) == str(session_key):
            self._finish('SESSION TIME LIMIT')

    def _active_session_seconds(self):
        elapsed=float(getattr(self,'session_elapsed',0.0) or 0.0)
        if self.session_start is not None and not self.paused:
            elapsed += max(0.0,time.time()-float(self.session_start))
        return elapsed

    def _tick(self):
        # Hard deadline is checked before page/pause checks. It therefore fires
        # even while paused, on another page, or while the live monitor is hidden.
        if self.current_session and self.session_deadline_monotonic is not None:
            remaining = self.session_deadline_monotonic - time.monotonic()
            if remaining <= 0:
                self._finish('SESSION TIME LIMIT')
                return
        if self.server_session_id and self.server.connected and time.time() - self.last_heartbeat >= 20:
            sid = self.server_session_id
            self.last_heartbeat = time.time()
            self.server_executor.submit(self._heartbeat_remote, sid)
        if not self.current_session or self.pages.currentIndex() != 3:
            return
        sec = int(self._active_session_seconds())
        limit = max(60, int(getattr(self, 'session_duration_seconds', 45 * 60)))
        self.timer_label.setText(f'{sec // 60:02d}:{sec % 60:02d} / {limit // 60:02d}:{limit % 60:02d}')

    def _heartbeat_remote(self,server_session_id):
        try:
            self.server.heartbeat(server_session_id)
            self.server_sync_text=f'SERVER ONLINE • SESSION #{server_session_id}'
        except Exception as exc:
            self.server_sync_text=f'SERVER HEARTBEAT ERROR • {exc}'

    # [AI-GENERATED / AI-ASSISTED CODE] Pause-session behavior.
    def _pause(self):
        if self.paused:return
        if self.session_start is not None:
            self.session_elapsed += max(0.0,time.time()-self.session_start)
        self.session_start=None
        if self.ai:self.ai.set_active(False)
        self.overlay_items=[]
        self._reset_realtime_overlay_tracker()
        self.paused=True
        self.pause.setEnabled(False)
        self.resume.setEnabled(True)
        self.mon_status.setText('PAUSED • TARGET HIDDEN')
        self._render()
        self.db.update_session(self.current_session,'PAUSED SESSION',self._active_session_seconds(),self.student_count,bool(self.ai and self.ai.identity.locked),self.board_side)

    # [AI-GENERATED / AI-ASSISTED CODE] Resume-session behavior.
    def _resume(self):
        if not self.ai or not self.current_session:return
        self.paused=False
        self.session_start=time.time()
        self.pause.setEnabled(True)
        self.resume.setEnabled(False)
        self.overlay_items=[]
        self._reset_realtime_overlay_tracker()
        # Force a fresh current-frame Face ID verification; never reuse the old box.
        self.ai.resume_relock()
        self.mon_status.setText('RESUMING • VERIFYING FACE ID…')
        self._render()

    # [AI-GENERATED / AI-ASSISTED CODE] Identity reset without reopening attendance selection.
    def _reset_ids(self):
        if not self.attendance_selected_ids:
            self._go_scan(); return
        if self.ai:
            self.ai.reset_identity(); self._set_ai_server_roster_compatible(); self.ai.set_active(True)
        self.present_detected_ids=set(); self.student_count=0
        self.session_start=None; self.session_elapsed=0.0
        if hasattr(self,'timer_label'): self.timer_label.setText(f"00:00 / {max(60, int(getattr(self, 'session_duration_seconds', 45 * 60))) // 60:02d}:00")
        self.scan_count.setText(f'0/{self.expected_student_count} học sinh đã được nhận diện')
        self.scan_state.setText('ĐÃ RESET • tự động xác thực Face ID và tiếp tục giám sát')
        self._set_page_direct(3)
        self._start_monitor()

# [AI-GENERATED / AI-ASSISTED CODE] Session ending/completion controls.
    def _end_session(self):self._finish('ENDED EARLY')
    def _complete(self):self._finish('SESSION COMPLETE')

    # [AI-GENERATED / AI-ASSISTED CODE] Saves final session state and closes the active AI worker.
    def _finish(self,status):
        if not self.current_session:
            return
        self._cancel_session_watchdog()
        configured_limit = max(60, int(getattr(self, 'session_duration_seconds', 45 * 60)))
        duration = configured_limit if status == 'SESSION TIME LIMIT' else min(configured_limit, self._active_session_seconds())
        locked=bool(self.ai and self.ai.identity.locked); remote_id=self.server_session_id
        hard_timeout = status == 'SESSION TIME LIMIT'
        # At the deadline, stop the AI worker first. Do not wait for microphone
        # shutdown, database sync, or the Server before ending face processing.
        if hard_timeout and self.ai:
            worker = self.ai
            stopped = False
            try:
                worker.stop()
                stopped = bool(worker.wait(250))
            except Exception:
                stopped = False
            if not stopped:
                try:
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(250)
                except Exception:
                    pass
            self.ai = None
        avg_decibel_dbfs = self._audio_average_dbfs()
        self._stop_audio_monitor()
        if self.ai:
            worker = self.ai
            try:
                worker.stop()
                worker.wait(900)
            except Exception:
                pass
            self.ai = None
        self.db.update_session(self.current_session,status,duration,self.student_count,locked,self.board_side)
        pending=[future for future in self.remote_futures if not future.done()]
        if pending and status != 'SESSION TIME LIMIT':
            try:
                wait(pending, timeout=15)
            except Exception:
                pass
        self.remote_futures=[future for future in self.remote_futures if not future.done()]
        if remote_id and self.server.connected:
            if status == 'SESSION TIME LIMIT':
                # Do not let a slow network response delay the mandatory local stop.
                try:
                    self.server_executor.submit(self.server.finish_session, remote_id, int(duration), avg_decibel_dbfs=avg_decibel_dbfs)
                except Exception as exc:
                    self.server_sync_text=f'SERVER FINISH ERROR • {exc}'
            else:
                try:
                    self.server.finish_session(remote_id, int(duration), avg_decibel_dbfs=avg_decibel_dbfs)
                    self.server_sync_text=f'SERVER SESSION #{remote_id} • COMPLETED'
                except Exception as exc:
                    self.server_sync_text=f'SERVER FINISH ERROR • {exc}'
        # Keep the class roster/cards after ending a session. Clearing server_roster
        # here left stale cards on screen while selection logic saw an empty roster;
        # bulk and manual selection then appeared to do nothing.
        self.current_session=None; self.session_start=None; self.session_elapsed=0.0; self.session_deadline_monotonic=None; self.paused=False; self.pending_monitor_after_camera=False; self.timer_label.setText(f"00:00 / {max(60, int(getattr(self, 'session_duration_seconds', 45 * 60))) // 60:02d}:00") if hasattr(self,'timer_label') else None; self.server_session_id=None; self.selected_server_roster=[]; self.absent_server_roster=[]; self.attendance_selected_ids=set(); self.attendance_absent_ids=set(); self.expected_student_count=0; self.present_detected_ids=set(); self.student_count=0; self.scan_count.setText('0 người được nhận diện') if hasattr(self,'scan_count') else None; self.scan_start.setEnabled(True) if hasattr(self,'scan_start') else None; self.lock_button.setEnabled(False) if hasattr(self,'lock_button') else None; self.class_combo.setEnabled(True) if hasattr(self,'class_combo') else None; self._refresh_metrics(); self._set_page_direct(0)
        if hasattr(self, 'attendance_prepare_button'):
            self.attendance_prepare_button.setText('ATTENDANCE' if self.ui_language == 'en' else 'ĐIỂM DANH')
        if hasattr(self, 'overview_attendance_summary'):
            if hard_timeout:
                self.overview_attendance_summary.setText('The scan was forcibly stopped because its time limit expired.' if self.ui_language == 'en' else 'Buổi quét đã buộc dừng vì hết thời gian.')
            else:
                self.overview_attendance_summary.setText('Attendance has not been completed.' if self.ui_language == 'en' else 'Chưa điểm danh cho buổi quét này.')
        # Re-sync checkbox visuals and unlock controls against the retained roster.
        self._refresh_overview_attendance_state()
        if hasattr(self, 'overview_start_button'):
            self.overview_start_button.setEnabled(bool(self.attendance_selected_ids) and bool(self.selected_server_roster))

    # [AI-GENERATED / AI-ASSISTED CODE] Dashboard metrics refresh.
    def _refresh_metrics(self):
        # v26 removed the old Home metric widgets, but _set_page(0) and
        # _finish() still call this refresh hook. Keep the hook safe when the
        # Home page does not define self.hm.
        if not hasattr(self, 'hm'):
            return
        sessions,events=self.db.counts()
        self.hm[0].setText(str(sessions))
        self.hm[1].setText(str(events))
        self.hm[2].setText('ONLINE' if self.server.connected else 'OFFLINE')

    # [AI-GENERATED / AI-ASSISTED CODE] History table refresh.
    def _refresh_history(self):
        self.hist.setRowCount(0)
        for sid,started,ended,duration,status,students,events,board in self.db.sessions():
            r=self.hist.rowCount(); self.hist.insertRow(r); values=[time.strftime('%Y-%m-%d %H:%M',time.localtime(started)),f'{int(duration)//60:02d}:{int(duration)%60:02d}',status,str(students),str(events),sid[:8]]
            for c,v in enumerate(values):self.hist.setItem(r,c,QTableWidgetItem(v))
            self.hist.item(r,5).setData(Qt.UserRole,sid)

    # [AI-GENERATED / AI-ASSISTED CODE] Reads the selected session ID.
    def _selected_sid(self):
        rows=self.hist.selectionModel().selectedRows(); return self.hist.item(rows[0].row(),5).data(Qt.UserRole) if rows else None

    # [AI-GENERATED / AI-ASSISTED CODE] Evidence viewer dialog.
    def _view_selected(self):
        sid=self._selected_sid()
        if not sid:return
        events=self.db.events(sid); dialog=QDialog(self); dialog.setWindowTitle('God Eyes • Evidence Viewer'); dialog.resize(1220,720); layout=QHBoxLayout(dialog)
        left=QVBoxLayout(); table=QTableWidget(0,6); table.setHorizontalHeaderLabels(['Time','Student','Observation','Confidence','Assessment','Evidence']); table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setEditTriggers(QAbstractItemView.NoEditTriggers); left.addWidget(table); layout.addLayout(left,3)
        right=QVBoxLayout(); preview=QLabel('SELECT AN OBSERVATION'); preview.setObjectName('camera'); preview.setAlignment(Qt.AlignCenter); preview.setMinimumSize(430,360); right.addWidget(preview,1); info=self._label('Evidence viewer','section'); right.addWidget(info); detail=self._label('','muted'); detail.setWordWrap(True); right.addWidget(detail)
        for ts,student,typ,conf,assessment,evidence in events:
            r=table.rowCount();table.insertRow(r);vals=[time.strftime('%H:%M:%S',time.localtime(ts)),student,typ.replace('_',' ').title(),f'{conf*100:.0f}%',assessment,Path(evidence).name if evidence else '']
            for c,v in enumerate(vals):table.setItem(r,c,QTableWidgetItem(v))
            if evidence:table.item(r,5).setData(Qt.UserRole,evidence)
        def show(row,_=0):
            item=table.item(row,5); path=item.data(Qt.UserRole) if item else None
            if path and Path(path).exists():
                img=cv2.imread(path)
                if img is not None:
                    preview.setPixmap(pixmap(img,preview.size())); detail.setText(f'{Path(path).name}\n{table.item(row,1).text()} • {table.item(row,2).text()} • {table.item(row,3).text()}')
            else:detail.setText('Evidence frame is not available on disk.')
        table.cellClicked.connect(show); table.currentCellChanged.connect(lambda row,*args: show(row) if row>=0 else None)
        buttons=QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(dialog.reject); right.addWidget(buttons); layout.addLayout(right,2); dialog.exec()

    # [AI-GENERATED / AI-ASSISTED CODE] Deletes one session and its evidence.
    def _delete_selected(self):
        sid=self._selected_sid()
        if not sid:return
        if QMessageBox.question(self,'Delete Session','Delete this session, events and evidence?')==QMessageBox.Yes:
            shutil.rmtree(EVIDENCE_DIR/sid,ignore_errors=True); self.db.delete_session(sid); self._refresh_history(); self._refresh_metrics()

    # [AI-GENERATED / AI-ASSISTED CODE] Deletes all sessions and evidence.
    def _delete_all(self):
        if QMessageBox.question(self,'Delete All','Delete all history and evidence?')==QMessageBox.Yes:
            shutil.rmtree(EVIDENCE_DIR,ignore_errors=True);EVIDENCE_DIR.mkdir(parents=True,exist_ok=True);self.db.delete_all();self._refresh_history();self._refresh_metrics()

    # [AI-GENERATED / AI-ASSISTED CODE] Application shutdown and resource cleanup.
    def closeEvent(self,event):
        self._cancel_session_watchdog()
        self._stop_audio_monitor()
        if self.ai:
            self.ai.stop();self.ai.wait(700)
        if self.camera:
            self.camera.stop();self.camera.wait(900)
        try:
            if hasattr(self, 'avatar_poll_timer'):
                self.avatar_poll_timer.stop()
            self.server_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self.avatar_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self.server.logout()
        self.db.close();event.accept()

    def _finish_auto_login(self):
        self.server_profile = self.server.profile
        self.server_username = str((self.server_profile or {}).get('username') or '')
        self.server.save_config(SERVER_CONFIG_PATH)
        if hasattr(self, 'server_url_edit'):
            self.server_url_edit.setText(self.server.base_url)
        if hasattr(self, 'server_user_edit'):
            self.server_user_edit.setText(self.server_username)
        self._set_server_status(
            f"Đã tự đăng nhập • {((self.server_profile or {}).get('full_name') or self.server_username)}",
            True
        )
        self._load_teacher_preferences_from_server(show_status=False)
        self._refresh_server_classes()

    def _auto_login_with_launch_token(self, server_url: str, launch_token: str):
        try:
            self.server.set_base_url(server_url)
            self.server.exchange_launch_token(launch_token)

            # The Server launch token carries the selected scan date/class.
            launch_context = getattr(self.server, 'launch_context', {}) or {}
            scan_date = str(launch_context.get('scan_date') or '').strip()
            class_id = int(launch_context.get('class_id') or 0)
            if scan_date:
                self.scan_date_context = scan_date
            if class_id > 0:
                self.server_selected_class_id = class_id

            # exchange_launch_token returns a fresh persistent device token.
            self.settings.setValue('server_url', self.server.base_url)
            self.settings.setValue('username', self.server.profile.get('username', ''))
            if self.server.device_token:
                self.settings.setValue('device_token', self.server.device_token)
            self.settings.remove('password')
            self.settings.setValue('remember_login', True)
            self.settings.sync()
            self._finish_auto_login()
            return True
        except Exception as exc:
            self._set_server_status(f"Tự đăng nhập từ Server thất bại • {exc}", False)
            return False

    def _auto_login_with_device_token(self):
        token = str(self.settings.value('device_token', '') or self.server.device_token or '').strip()
        if not token:
            return False
        try:
            self.server.device_token = token
            self.server.set_base_url(str(self.settings.value('server_url', self.server.base_url) or self.server.base_url))
            self.server.login_with_device_token(token)
            self._finish_auto_login()
            return True
        except ServerClientError as exc:
            # Only forget the persistent credential when the Server explicitly
            # rejects it. A network failure should preserve it for the next run.
            if exc.status in (401, 403):
                self.settings.remove('device_token')
                self.settings.sync()
                self.server.device_token = ''
            self._set_server_status(f"Tự đăng nhập thất bại • {exc}", False)
            return False
        except Exception as exc:
            self._set_server_status(f"Tự đăng nhập thất bại • {exc}", False)
            return False

    def _auto_login_from_legacy_credentials(self):
        # Migration path for older installations that stored the password.
        username = str(self.settings.value('username', '') or '').strip()
        password = str(self.settings.value('password', '') or '')
        remember = bool(self.settings.value('remember_login', False, type=bool))
        if not username or not password or not remember:
            return False
        try:
            self.server.set_base_url(str(self.settings.value('server_url', self.server.base_url) or self.server.base_url))
            self.server.login(username, password)
            token = self.server.register_device('God Eyes Desktop', CLIENT_VERSION)
            self.settings.setValue('device_token', token)
            self.settings.setValue('username', username)
            self.settings.setValue('remember_login', True)
            self.settings.remove('password')
            self.settings.sync()
            self._finish_auto_login()
            return True
        except Exception as exc:
            self._set_server_status(f"Không thể chuyển tài khoản cũ sang đăng nhập tự động • {exc}", False)
            return False

    def _prompt_server_login(self):
        if self.server.connected:
            self._refresh_server_classes()
        elif self._auto_login_with_device_token():
            return
        elif self._auto_login_from_legacy_credentials():
            return
        else:
            self._open_server_login()


# Add Camera navigation as a scroll shortcut. Overview remains above the Camera
# panel on the same screen; the button does not open a separate camera page.
def _god_eyes_add_camera_navigation():
    original_build = MainWindow._build

    def _build_with_camera_shortcut(self):
        original_build(self)
        try:
            side_layout = self.centralWidget().layout().itemAt(0).widget().layout()
            camera_btn = QPushButton('Camera')
            camera_btn.setIcon(QIcon(_godeyes_line_icon('camera', 18)))
            camera_btn.setIconSize(QSize(18, 18))
            camera_btn.setObjectName('nav')
            camera_btn.clicked.connect(lambda checked=False: self._set_page(1))
            stretch_index = None
            for i in range(side_layout.count()):
                item = side_layout.itemAt(i)
                if item is not None and item.spacerItem() is not None:
                    stretch_index = i
                    break
            if stretch_index is None:
                side_layout.addWidget(camera_btn)
            else:
                side_layout.insertWidget(stretch_index, camera_btn)
            self.camera_nav_button = camera_btn
        except Exception:
            pass

    MainWindow._build = _build_with_camera_shortcut


_god_eyes_add_camera_navigation()

# [AI-GENERATED / AI-ASSISTED CODE] Windows URI protocol registration.
def _register_god_eyes_protocol():
    """Register godeyes:// for the current packaged EXE (HKCU, no admin rights)."""
    if os.name != 'nt':
        return
    try:
        import winreg
        exe_path = str(Path(sys.executable).resolve())
        if not getattr(sys, 'frozen', False):
            # During source development, do not register the Python interpreter.
            return
        base = r'Software\Classes\godeyes'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:God Eyes Protocol')
            winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r'\DefaultIcon') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'"{exe_path}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r'\shell\open\command') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'"{exe_path}" "%1"')
    except Exception:
        # URI registration is convenience-only; the desktop app must still start.
        pass


def _normalise_protocol_args(argv):
    """Convert godeyes://launch?... into the existing launch-token CLI arguments."""
    cleaned = [argv[0]] if argv else [sys.argv[0]]
    server_url = ''
    launch_token = ''
    for raw in argv[1:]:
        value = str(raw or '').strip()
        if value.lower().startswith('godeyes://'):
            try:
                parsed = urlparse(value)
                params = parse_qs(parsed.query)
                launch_token = str((params.get('token') or [''])[0]).strip()
                server_url = str((params.get('server_url') or [''])[0]).strip()
            except Exception:
                pass
            continue
        cleaned.append(raw)
    if launch_token:
        cleaned += ['--launch-token', launch_token]
    if server_url:
        cleaned += ['--server-url', server_url]
    return cleaned


class FaceEnrollmentWindow(QMainWindow):
    """Capture one student's reference portrait and submit it for server-side Face ID enrollment."""
    def __init__(self, server_url, student_id, enroll_token, camera_index=0):
        super().__init__()
        self.server_url = str(server_url or '').strip().rstrip('/')
        self.student_id = int(student_id)
        self.enroll_token = str(enroll_token or '').strip()
        self.camera_index = int(camera_index)
        self._latest_frame = None
        self._busy = False
        self._cap = None
        self.setWindowTitle('God Eyes - Đăng ký Face ID')
        self.resize(780, 620)
        self.setMinimumSize(620, 500)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        title = QLabel('ĐĂNG KÝ FACE ID HỌC SINH')
        title.setStyleSheet('font-size:20px;font-weight:800;color:#163a67;')
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        hint = QLabel('Đặt đúng một học sinh trước camera, đủ sáng và nhìn thẳng. Ảnh được chụp sẽ vừa lưu làm ảnh hồ sơ vừa dùng để tạo Face ID.')
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet('font-size:13px;color:#52657b;padding:2px 8px;')
        layout.addWidget(hint)
        self.preview = QLabel('Đang khởi động camera…')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(480, 330)
        self.preview.setStyleSheet('background:#101b2a;border:1px solid #cbd8e8;border-radius:14px;color:#ffffff;font-size:15px;')
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.preview, 1)
        self.status = QLabel('Kiểm tra camera…')
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet('color:#52657b;font-size:12px;')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.cancel_button = QPushButton('Đóng')
        self.cancel_button.setMinimumHeight(40)
        self.cancel_button.clicked.connect(self.close)
        self.capture_button = QPushButton('CHỤP ẢNH VÀ ĐĂNG KÝ FACE ID')
        self.capture_button.setMinimumHeight(44)
        self.capture_button.setStyleSheet('QPushButton{background:#2475d0;color:#fff;border:0;border-radius:9px;padding:8px 18px;font-weight:800;} QPushButton:hover{background:#1a63b4;} QPushButton:disabled{background:#9bb7d8;color:#edf3fa;}')
        self.capture_button.clicked.connect(self._submit_enrollment)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.capture_button)
        layout.addLayout(buttons)
        self.setCentralWidget(root)

        try:
            self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                self._cap.release()
                self._cap = cv2.VideoCapture(self.camera_index)
        except Exception:
            self._cap = cv2.VideoCapture(self.camera_index)
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._refresh_camera)
        if self._cap is not None and self._cap.isOpened():
            self.status.setText('Camera đã kết nối. Kiểm tra khuôn mặt trong khung hình trước khi đăng ký.')
            self._preview_timer.start(33)
        else:
            self.preview.setText('Không mở được camera. Hãy kiểm tra camera hoặc chọn đúng thiết bị.')
            self.status.setText('Camera chưa sẵn sàng.')
            self.capture_button.setEnabled(False)

    def _refresh_camera(self):
        if self._busy or self._cap is None or not self._cap.isOpened():
            return
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self.status.setText('Chưa đọc được hình ảnh từ camera. Kiểm tra kết nối camera.')
            return
        self._latest_frame = frame.copy()
        self.preview.setPixmap(pixmap(frame, self.preview.size(), fill=True))

    def _submit_enrollment(self):
        if self._busy:
            return
        if self._latest_frame is None:
            QMessageBox.warning(self, 'God Eyes', 'Chưa có hình ảnh từ camera. Vui lòng đợi camera hoạt động.')
            return
        if not self.server_url or not self.enroll_token or self.student_id <= 0:
            QMessageBox.critical(self, 'God Eyes', 'Thiếu địa chỉ Server hoặc thông tin đăng ký Face ID.')
            return
        ok, encoded = cv2.imencode('.jpg', self._latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            QMessageBox.critical(self, 'God Eyes', 'Không thể xử lý ảnh từ camera. Vui lòng thử lại.')
            return
        payload = {
            'token': self.enroll_token,
            'student_id': self.student_id,
            'photo_base64': base64.b64encode(encoded.tobytes()).decode('ascii'),
        }
        request = urllib.request.Request(
            self.server_url + '/api/v1/face-enrollment',
            data=json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'GodEyes/1.0'},
            method='POST',
        )
        self._busy = True
        self.capture_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status.setText('Đang gửi ảnh và tạo Face ID trên Server…')
        if self._preview_timer.isActive():
            self._preview_timer.stop()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8', errors='replace') or '{}')
            if not result.get('success'):
                raise RuntimeError(str(result.get('detail') or 'Server không xác nhận đăng ký thành công.'))
            QMessageBox.information(self, 'God Eyes', 'Đăng ký Face ID thành công. Ảnh vừa chụp đã được lưu làm ảnh hồ sơ học sinh.')
            self.close()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode('utf-8', errors='replace')).get('detail', '')
            except Exception:
                detail = ''
            QMessageBox.warning(self, 'God Eyes', str(detail or f'Server trả về lỗi HTTP {exc.code}.'))
            self._restore_after_failure()
        except Exception as exc:
            QMessageBox.warning(self, 'God Eyes', f'Không thể đăng ký Face ID: {str(exc).strip() or exc.__class__.__name__}')
            self._restore_after_failure()

    def _restore_after_failure(self):
        self._busy = False
        self.capture_button.setEnabled(self._cap is not None and self._cap.isOpened())
        self.cancel_button.setEnabled(True)
        if self._cap is not None and self._cap.isOpened() and not self._preview_timer.isActive():
            self._preview_timer.start(33)
        self.status.setText('Đăng ký chưa hoàn tất. Hãy kiểm tra ảnh/camera rồi thử lại.')

    def closeEvent(self, event):
        try:
            if hasattr(self, '_preview_timer') and self._preview_timer.isActive():
                self._preview_timer.stop()
        except Exception:
            pass
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        super().closeEvent(event)


# [AI-GENERATED / AI-ASSISTED CODE] Application entry point.
def main():
    _register_god_eyes_protocol()
    protocol_argv = _normalise_protocol_args(sys.argv)
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--face-enroll', action='store_true')
    parser.add_argument('--student-id', type=int, default=0)
    parser.add_argument('--enroll-token', default='')
    parser.add_argument('--server-url', default='http://127.0.0.1:8000')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--launch-token', default='')
    args = parser.parse_args(protocol_argv[1:])

    app = QApplication([protocol_argv[0]])
    app.setApplicationName('God Eyes')
    app.setFont(QFont('Segoe UI',10))

    if args.face_enroll:
        if args.student_id <= 0 or not args.enroll_token:
            QMessageBox.critical(None, 'God Eyes', 'Thiếu student_id hoặc enrollment token.')
            return 2
        window = FaceEnrollmentWindow(args.server_url, args.student_id, args.enroll_token, args.camera)
        window.show()
        return app.exec()

    window = MainWindow()
    window.show()
    if args.launch_token:
        QTimer.singleShot(80, lambda: window._auto_login_with_launch_token(args.server_url, args.launch_token) or QTimer.singleShot(100, window._prompt_server_login))
    else:
        # Direct double-click opens the app and reuses the persistent Teacher
        # device session without showing the login dialog.
        QTimer.singleShot(120, window._prompt_server_login)
    return app.exec()

if __name__ == '__main__':
    main()