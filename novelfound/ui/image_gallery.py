# -*- coding: utf-8 -*-
"""「本书插图」浏览器：把 EPUB 里的插图都列出来看。

为什么要单独做：很多 EPUB（尤其网友自制版）把插图塞进了包里，**却没有在正文里引用**，
正文里自然永远看不到（实测一本《诡秘之主》有 15 张图零引用，另有 1 张被 1394 章引用）。
这个窗口按包内清单列出**所有**图片，点缩略图看大图，并提供「存到本地」。

图片字节由 :class:`~novelfound.sources.local.LocalSource` 提供（走本地文件，不联网），
解码结果按路径缓存，翻看时不会反复解压。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter, QVBoxLayout,
                             QWidget)

THUMB = QSize(96, 128)
PREVIEW_MAX = 900


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1048576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


class ImageGalleryDialog(QDialog):
    """插图浏览窗口：左侧缩略图列表，右侧大图预览。"""

    def __init__(self, items: List[dict], loader: Callable[[str], bytes],
                 parent: Optional[QWidget] = None, title: str = "本书插图"):
        super().__init__(parent)
        self.setWindowTitle(f"{title}（{len(items)} 张）")
        self.resize(920, 640)
        self._items = list(items)
        self._loader = loader
        self._raw: Dict[str, bytes] = {}          # 路径 → 原始字节（只读一次）
        self._images: Dict[str, QImage] = {}      # 路径 → 解码结果

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        split = QSplitter(Qt.Horizontal, self)
        root.addWidget(split, 1)

        self.list = QListWidget(split)
        self.list.setObjectName("galleryList")
        self.list.setIconSize(THUMB)
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSpacing(8)
        self.list.currentItemChanged.connect(lambda *_: self._show_current())
        split.addWidget(self.list)

        right = QWidget(split)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self.preview = QLabel("点左侧缩略图查看大图", right)
        self.preview.setObjectName("galleryPreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 240)
        self.preview.setWordWrap(True)
        right_layout.addWidget(self.preview, 1)
        self.info = QLabel("", right)
        self.info.setObjectName("muted")
        self.info.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.info)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 660])

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("另存为…", self)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_current)
        buttons.addWidget(self.save_button)
        close_button = QPushButton("关闭", self)
        close_button.setObjectName("primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self._populate()

    # ------------------------------------------------------------------ 构建
    def _populate(self) -> None:
        for index, item in enumerate(self._items):
            entry = QListWidgetItem(f"{item.get('name', '')}\n{human_size(item.get('size', 0))}")
            entry.setData(Qt.UserRole, item.get("path", ""))
            entry.setTextAlignment(Qt.AlignHCenter)
            entry.setSizeHint(QSize(THUMB.width() + 24, THUMB.height() + 44))
            thumb = self._image(item.get("path", ""))
            if not thumb.isNull():
                entry.setIcon(QIcon(QPixmap.fromImage(thumb.scaled(
                    THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))))
            self.list.addItem(entry)
        if self._items:
            self.list.setCurrentRow(0)

    def _image(self, path: str) -> QImage:
        if path in self._images:
            return self._images[path]
        data = self._raw.get(path)
        if data is None:
            try:
                data = self._loader(path)
            except Exception:      # noqa: BLE001 - 单张图读失败不该影响窗口
                data = b""
            self._raw[path] = data
        image = QImage()
        if data:
            image.loadFromData(data)
        self._images[path] = image
        return image

    # ------------------------------------------------------------------ 预览
    def _current_path(self) -> str:
        """当前选中的图片路径；没有选中项时退回第一张（打开窗口就该有图可看）。"""
        item = self.list.currentItem()
        if item is None and self.list.count():
            item = self.list.item(0)
        return item.data(Qt.UserRole) if item is not None else ""

    def _show_current(self) -> None:
        path = self._current_path()
        image = self._image(path)
        self.save_button.setEnabled(not image.isNull())
        if image.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("这张图读不出来（可能格式不支持）")
            self.info.setText(Path(path).name)
            return
        shown = image
        if image.width() > PREVIEW_MAX or image.height() > PREVIEW_MAX:
            shown = image.scaled(PREVIEW_MAX, PREVIEW_MAX, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        self.preview.setPixmap(QPixmap.fromImage(shown))
        raw = self._raw.get(path, b"")
        self.info.setText(f"{Path(path).name}　·　{image.width()}×{image.height()}"
                          f"　·　{human_size(len(raw))}　·　{path}")

    def _save_current(self) -> None:
        path = self._current_path()
        data = self._raw.get(path, b"")
        if not data:
            return
        target, _ = QFileDialog.getSaveFileName(self, "另存插图", Path(path).name)
        if target:
            Path(target).write_bytes(data)

    # ------------------------------------------------------------------ 测试
    def image_paths(self) -> List[str]:
        """列出的图片路径（自检用）。"""
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def current_image_size(self) -> tuple:
        image = self._image(self._current_path())
        return (image.width(), image.height())
