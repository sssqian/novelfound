# -*- coding: utf-8 -*-
"""本地书管理：查看 / 删除已导入的本地书，清理失效记录。

为什么要单独做这一块（用户实测反馈）：

* 「移出书架」只删了书架那一行，**导入的文件、缓存、浏览历史、阅读进度全都留着**；
* 界面上原本**没有任何入口**能彻底删掉一本导入的书；
* 删掉记录后，历史与缓存里会留下"死引用"（点开会报"本地文件已丢失"）。

所以这里提供：
1. 列表：书名 / 格式 / 章数 / 占用空间 / 导入时间；
2. 「删除选中的」：删文件 + 记录 + 该书的缓存 + 浏览历史 + 阅读进度（删除前有明细确认）；
3. 「重新导入」：重新选一个文件替换这本书；
4. 「打开文件位置」：在资源管理器里定位到导入的文件；
5. 「清理失效记录」：扫掉指向已不存在本地书的 书架/进度/历史/缓存 条目。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)


def human_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def human_time(stamp: float) -> str:
    if not stamp:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


class LocalManagerDialog(QDialog):
    """本地书管理窗口。"""

    def __init__(self, books, library, history, cache,
                 on_delete: Callable[[Dict], None],
                 on_cleanup: Callable[[], Dict],
                 parent: Optional[QWidget] = None,
                 on_reimport: Optional[Callable[[Dict], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("本地书管理")
        self.resize(880, 520)
        self.books = books
        self.library = library
        self.history = history
        self.cache = cache
        self._on_delete = on_delete
        self._on_cleanup = on_cleanup
        self._on_reimport = on_reimport

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.summary = QLabel("", self)
        self.summary.setObjectName("sectionTitle")
        root.addWidget(self.summary)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["书名", "格式", "章数", "占用", "导入时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        root.addWidget(self.table, 1)

        self.hint = QLabel(
            "「移出书架」只是从书架隐藏；要连文件、缓存、历史一起清掉，用这里的「删除选中」。",
            self)
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.cleanup_button = QPushButton("清理失效记录", self)
        self.cleanup_button.setToolTip("清掉指向已删除本地书的书架/进度/历史/缓存条目")
        self.cleanup_button.clicked.connect(self._cleanup)
        buttons.addWidget(self.cleanup_button)
        buttons.addStretch(1)
        self.reveal_button = QPushButton("打开文件位置", self)
        self.reveal_button.clicked.connect(self._reveal)
        buttons.addWidget(self.reveal_button)
        self.reimport_button = QPushButton("重新导入…", self)
        self.reimport_button.clicked.connect(self._reimport)
        buttons.addWidget(self.reimport_button)
        self.delete_button = QPushButton("删除选中", self)
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._delete)
        buttons.addWidget(self.delete_button)
        close_button = QPushButton("关闭", self)
        close_button.setObjectName("primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.reload()

    # ------------------------------------------------------------------ 数据
    def reload(self) -> None:
        """重新读一遍本地书列表。"""
        items: List[Dict] = list(self.books.all())
        self.table.setRowCount(len(items))
        total = 0
        for row, item in enumerate(items):
            path = self.books.file_path(item)
            size = path.stat().st_size if path.is_file() else 0
            cover = item.get("cover_file")
            if cover:
                cover_path = self.books.files_dir / cover
                if cover_path.is_file():
                    size += cover_path.stat().st_size
            total += size
            cells = [item.get("title", ""), str(item.get("format", "")).upper(),
                     f"{len(item.get('chapters') or [])}",
                     human_size(size), human_time(item.get("added_at", 0))]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column == 0:
                    cell.setData(Qt.UserRole, item.get("id"))
                self.table.setItem(row, column, cell)
        self.summary.setText(f"已导入 {len(items)} 本，共占用 {human_size(total)}")
        self._update_buttons()

    def selected_items(self) -> List[Dict]:
        ids = {self.table.item(index.row(), 0).data(Qt.UserRole)
               for index in self.table.selectionModel().selectedRows()}
        return [item for item in self.books.all() if item.get("id") in ids]

    def _update_buttons(self) -> None:
        has = bool(self.selected_items())
        for button in (self.delete_button, self.reveal_button, self.reimport_button):
            button.setEnabled(has)

    # ------------------------------------------------------------------ 操作
    def _reveal(self) -> None:
        for item in self.selected_items():
            path = self.books.file_path(item)
            if not path.is_file():
                continue
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
            return

    def _reimport(self) -> None:
        if self._on_reimport is None:
            return
        for item in self.selected_items()[:1]:
            self._on_reimport(item)
        self.reload()

    def _delete(self) -> None:
        for item in self.selected_items():
            self._on_delete(item)
        self.reload()

    def _cleanup(self) -> None:
        result = self._on_cleanup() or {}
        parts = [f"书架/进度 {result.get('library', 0)} 条",
                 f"浏览历史 {result.get('history', 0)} 条",
                 f"缓存 {result.get('cache', 0)} 行"]
        QMessageBox.information(
            self, "清理完成",
            "已清理失效记录：\n\n" + "\n".join(f"· {p}" for p in parts))
        self.reload()

    # ------------------------------------------------------------------ 测试
    def row_titles(self) -> List[str]:
        return [self.table.item(row, 0).text() for row in range(self.table.rowCount())]

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)
