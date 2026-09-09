# -*- coding: utf-8 -*-
"""网络找书源对话框（方案 C）。

流程：填书名 → 用搜索引擎找候选站点 → 自动逐个探测 → 列表里勾选真正可用的
→ 一键保存启用。

只做"推荐"，不自动改配置；被过滤掉的站点也会列出来并说明原因，
避免用户以为程序"什么都没找到"。
"""
from __future__ import annotations

import json
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..errors import guard_slot
from ..net import HttpSession
from ..sources.discover import ENGINES, DiscoveredSource, DiscoveryOutcome
from ..tasks import DiscoverTask, TaskManager


class SourceDiscoverDialog(QDialog):
    """用搜索引擎发现新书源。"""

    sources_changed = pyqtSignal()

    def __init__(self, config: AppConfig, http: HttpSession, task_manager: TaskManager,
                 book_title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.http = http
        self.task_manager = task_manager
        self.outcome: Optional[DiscoveryOutcome] = None
        self._task: Optional[DiscoverTask] = None
        self._results: List[DiscoveredSource] = []

        self.setWindowTitle("网络找书源")
        self.resize(780, 640)

        layout = QVBoxLayout(self)
        tip = QLabel(
            "所有已启用书源都搜不到时，可以用这里：程序会去搜索引擎找可能的站点，"
            "再自动验证「能搜到这本书 + 能解析目录和正文」，最后只推荐真正可用的。\n"
            "只读公开页面，不执行站点脚本、不处理登录与验证码；请自行确认内容来源合法。",
            self)
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        query_row = QHBoxLayout()
        query_row.addWidget(QLabel("书名", self))
        self.title_edit = QLineEdit(self)
        self.title_edit.setObjectName("plain")
        self.title_edit.setText(book_title or self.config.get("last_search") or "")
        self.title_edit.returnPressed.connect(self.on_start)
        query_row.addWidget(self.title_edit, 1)

        query_row.addWidget(QLabel("引擎", self))
        self.engine_box = QComboBox(self)
        self.engine_box.addItem("自动（推荐）", "")
        for key, config_ in ENGINES.items():
            self.engine_box.addItem(config_["name"], key)
        query_row.addWidget(self.engine_box)

        query_row.addWidget(QLabel("最多探测", self))
        self.limit_box = QSpinBox(self)
        self.limit_box.setRange(1, 10)
        self.limit_box.setSuffix(" 个站")
        self.limit_box.setValue(int(self.config.get("discover_max_sites") or 5))
        self.limit_box.valueChanged.connect(
            lambda v: self.config.set("discover_max_sites", int(v)))
        query_row.addWidget(self.limit_box)
        layout.addLayout(query_row)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("开始查找", self)
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.on_start)
        buttons.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.on_cancel)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setPlaceholderText("查找进度会显示在这里…")
        layout.addWidget(self.log)

        self.result_list = QListWidget(self)
        self.result_list.setMinimumHeight(180)
        self.result_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.result_list, 1)

        self.preview = QLabel("", self)
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.preview)

        action_row = QHBoxLayout()
        select_all = QPushButton("全选可用", self)
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        action_row.addWidget(select_all)
        select_none = QPushButton("全不选", self)
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        action_row.addWidget(select_none)
        action_row.addStretch(1)
        self.save_button = QPushButton("保存选中书源", self)
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save)
        action_row.addWidget(self.save_button)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.reject)
        action_row.addWidget(close_button)
        layout.addLayout(action_row)

    # ------------------------------------------------------------------ 查找
    @guard_slot
    def on_start(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            self.log.setPlainText("请先填写书名。")
            return
        self.log.clear()
        self.result_list.clear()
        self.preview.setText("")
        self._results = []
        self.save_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        existing = self._existing_hosts()
        self._task = DiscoverTask(
            self.http, title, author="",
            limit=self.limit_box.value(), engine=self.engine_box.currentData() or "",
            existing_hosts=existing)
        self._task.signals.progress.connect(self.log.appendPlainText)
        self._task.signals.finished.connect(self._on_finished)
        self._task.signals.failed.connect(self._on_failed)
        self.task_manager.start(self._task)

    def _existing_hosts(self):
        from urllib.parse import urlparse

        from ..sources import all_rules
        hosts = []
        for rule in all_rules(self.config):
            host = urlparse(str(rule.get("base_url", ""))).netloc
            if host:
                hosts.append(host)
        return hosts

    def on_cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self.log.appendPlainText("已取消。")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_failed(self, message: str, detail: str) -> None:
        self._reset_buttons()
        self.log.appendPlainText(f"查找失败：{message}")
        if detail:
            self.log.appendPlainText(f"  {detail}")

    def _on_finished(self, outcome: DiscoveryOutcome) -> None:
        self._reset_buttons()
        self.outcome = outcome
        self._results = outcome.results
        self.result_list.clear()

        for result in outcome.results:
            candidate = result.candidate
            if result.ok:
                probe = result.outcome
                label = (f"✓ {candidate.host}　·　{probe.template}　·　"
                         f"目录 {probe.chapter_count} 章　·　正文 {probe.char_count} 字")
            else:
                label = f"✗ {candidate.host}　·　{result.reason}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, result)
            if result.ok:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
            else:
                item.setForeground(Qt.gray)
            self.result_list.addItem(item)

        # 被过滤掉的候选也列出来，说明原因
        skipped = [c for c in outcome.candidates if c.skipped]
        for candidate in skipped[:10]:
            item = QListWidgetItem(f"— {candidate.host}　·　{candidate.skipped}")
            item.setForeground(Qt.gray)
            item.setData(Qt.UserRole, None)
            self.result_list.addItem(item)

        usable = len(outcome.usable)
        if outcome.error:
            self.preview.setText(f"<b>没有找到可用站点</b>：{outcome.error}")
        elif usable:
            self.preview.setText(
                f"<b>找到 {usable} 个可用站点</b>，勾选后点「保存选中书源」即可启用。")
        else:
            self.preview.setText(
                "<b>没有可用站点</b>：候选站点要么探测未通过，要么没收录这本书。")
        self.save_button.setEnabled(usable > 0)
        if usable:
            self.result_list.setCurrentRow(0)

    # ------------------------------------------------------------------ 交互
    def _on_selection_changed(self) -> None:
        item = self.result_list.currentItem()
        result = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(result, DiscoveredSource) or not result.ok:
            return
        probe = result.outcome
        preview = probe.preview
        self.preview.setText(
            f"<b>{result.candidate.host}</b>　模板 {probe.template}<br>"
            f"书名：{preview.get('title', '')}"
            + (f"　作者：{preview.get('author', '')}" if preview.get("author") else "")
            + f"<br>前三章：{' / '.join(preview.get('chapters') or [])}"
            + f"<br>正文开头：{(preview.get('snippet') or '')[:120]}")

    def _set_all_checked(self, checked: bool) -> None:
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            result = item.data(Qt.UserRole)
            if isinstance(result, DiscoveredSource) and result.ok:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    @guard_slot
    def on_save(self) -> None:
        rules = []
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            result = item.data(Qt.UserRole)
            if not isinstance(result, DiscoveredSource) or not result.ok:
                continue
            if item.checkState() == Qt.Checked and result.outcome.rule:
                rules.append(result.outcome.rule)
        if not rules:
            self.preview.setText("没有勾选任何可用书源。")
            return
        count = self.config.add_custom_sources(rules)
        for rule in rules:
            self.config.set_source_enabled(rule["key"], True)
        self.preview.setText(
            f"<b>✓ 已保存并启用 {count} 个书源</b>："
            + "、".join(r.get("name", r["key"]) for r in rules)
            + "<br>回到主界面搜索一次即可看到结果。")
        self.save_button.setEnabled(False)
        self.sources_changed.emit()
