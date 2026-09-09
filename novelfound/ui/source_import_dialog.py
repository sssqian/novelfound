# -*- coding: utf-8 -*-
"""书源导入 / 订阅管理对话框。

* **导入**：支持粘贴订阅地址、选择本地 JSON 文件，自动识别"本项目规则"与
  "开源阅读(Legado)书源"两种格式；解析结果先预览（名称、地址、是否可用及原因），
  由用户勾选后再写入配置。
* **订阅**：维护订阅地址列表，可一键更新、可设置启动时自动更新与更新间隔。

安全边界：只接受 URL 模板 + CSS 选择器这类**数据**；含 JS / XPath / JSONPath
规则的源会被标记为"不支持"且无法勾选，程序永远不会执行远程脚本。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..errors import guard_slot
from ..net import HttpSession
from ..sources.importer import ParsedRule
from ..tasks import ImportTask, SubscriptionTask, TaskManager


class SourceImportDialog(QDialog):
    """书源导入与订阅管理。"""

    sources_changed = pyqtSignal()

    def __init__(self, config: AppConfig, http: HttpSession,
                 task_manager: TaskManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.http = http
        self.task_manager = task_manager
        self._parsed: List[ParsedRule] = []

        self.setWindowTitle("导入书源 / 订阅管理")
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._build_import_tab(), "导入书源")
        tabs.addTab(self._build_subscription_tab(), "订阅管理")
        layout.addWidget(tabs, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

    # ------------------------------------------------------------------ 导入页
    def _build_import_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        tip = QLabel(
            "支持两种格式：\n"
            "① 本项目规则 JSON（含 key / base_url / 选择器）；\n"
            "② 开源「阅读」(Legado) 书源 JSON —— 自动转换，含 JS / XPath 规则的源会被跳过。",
            page)
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit(page)
        self.url_edit.setObjectName("plain")
        self.url_edit.setPlaceholderText("粘贴书源订阅地址（http/https 结尾为 .json 的链接）")
        self.url_edit.returnPressed.connect(self.on_fetch_url)
        url_row.addWidget(self.url_edit, 1)
        self.fetch_button = QPushButton("获取并解析", page)
        self.fetch_button.clicked.connect(self.on_fetch_url)
        url_row.addWidget(self.fetch_button)

        self.file_button = QPushButton("从文件导入…", page)
        self.file_button.clicked.connect(self.on_import_file)
        url_row.addWidget(self.file_button)
        layout.addLayout(url_row)

        self.status_label = QLabel("", page)
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.preview = QListWidget(page)
        self.preview.setMinimumHeight(240)
        layout.addWidget(self.preview, 1)

        buttons = QHBoxLayout()
        select_all = QPushButton("全选可用", page)
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        buttons.addWidget(select_all)
        select_none = QPushButton("全不选", page)
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        buttons.addWidget(select_none)
        buttons.addStretch(1)
        self.import_button = QPushButton("导入选中书源", page)
        self.import_button.setObjectName("primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.on_import_selected)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)
        return page

    @guard_slot
    def on_fetch_url(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self.status_label.setText("请先填写订阅地址。")
            return
        self.fetch_button.setEnabled(False)
        self.status_label.setText("正在下载…")
        task = ImportTask(self.http, url=url)
        task.signals.progress.connect(self.status_label.setText)
        task.signals.finished.connect(self._on_parsed)
        task.signals.failed.connect(self._on_import_failed)
        self.task_manager.start(task)

    def on_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择书源 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            self.status_label.setText(f"读取文件失败：{exc}")
            return
        task = ImportTask(self.http, text=text)
        task.signals.progress.connect(self.status_label.setText)
        task.signals.finished.connect(self._on_parsed)
        task.signals.failed.connect(self._on_import_failed)
        self.task_manager.start(task)

    def _on_import_failed(self, message: str, detail: str) -> None:
        self.fetch_button.setEnabled(True)
        self.status_label.setText(f"导入失败：{message}（{detail}）")

    def _on_parsed(self, parsed: List[ParsedRule]) -> None:
        self.fetch_button.setEnabled(True)
        self._parsed = parsed
        self.preview.clear()
        usable = 0
        for item in parsed:
            label = f"{item.name}"
            if item.key:
                label += f"    [{item.key}]"
            if item.base_url:
                label += f"    {item.base_url}"
            if not item.ok:
                label += f"\n    不可用：{item.reason}"
            widget = QListWidgetItem(label)
            widget.setData(Qt.UserRole, item)
            if item.ok:
                widget.setFlags(widget.flags() | Qt.ItemIsUserCheckable)
                widget.setCheckState(Qt.Checked)
                usable += 1
            else:
                widget.setFlags(widget.flags() & ~Qt.ItemIsUserCheckable)
                widget.setForeground(Qt.gray)
            self.preview.addItem(widget)
        self.import_button.setEnabled(usable > 0)
        self.status_label.setText(
            f"共解析 {len(parsed)} 条，其中可用 {usable} 条"
            + ("（不可用的多为 JS/XPath 规则，本程序不执行脚本）" if usable < len(parsed) else ""))

    def _set_all_checked(self, checked: bool) -> None:
        for i in range(self.preview.count()):
            item = self.preview.item(i)
            rule: ParsedRule = item.data(Qt.UserRole)
            if rule is not None and rule.ok:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    @guard_slot
    def on_import_selected(self) -> None:
        rules: List[Dict] = []
        for i in range(self.preview.count()):
            item = self.preview.item(i)
            rule: ParsedRule = item.data(Qt.UserRole)
            if rule is None or not rule.ok:
                continue
            if item.checkState() == Qt.Checked:
                rules.append(rule.rule)
        if not rules:
            self.status_label.setText("没有勾选任何可用书源。")
            return
        count = self.config.add_custom_sources(rules)
        for rule in rules:
            self.config.set_source_enabled(rule["key"], True)
        self.status_label.setText(
            f"已导入 {count} 个书源并自动启用。建议回到「设置 → 书源」点一次"
            "「检测可用性」，确认哪些真的能用。")
        self.sources_changed.emit()

    # ------------------------------------------------------------------ 订阅页
    def _build_subscription_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        tip = QLabel("订阅地址会定期拉取书源列表并更新到本地（只覆盖同名 key 的书源）。\n"
                     "可填入任何返回书源 JSON 的链接，例如开源书源仓库的 raw 地址。", page)
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        add_row = QHBoxLayout()
        self.sub_edit = QLineEdit(page)
        self.sub_edit.setObjectName("plain")
        self.sub_edit.setPlaceholderText("https://example.com/sources.json")
        add_row.addWidget(self.sub_edit, 1)
        add_button = QPushButton("添加订阅", page)
        add_button.clicked.connect(self.on_add_subscription)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        self.sub_list = QListWidget(page)
        self.sub_list.setMinimumHeight(200)
        layout.addWidget(self.sub_list, 1)

        options = QHBoxLayout()
        self.auto_update = QCheckBox("启动时自动更新订阅", page)
        self.auto_update.setChecked(bool(self.config.get("auto_update_sources")))
        self.auto_update.toggled.connect(
            lambda v: self.config.set("auto_update_sources", bool(v)))
        options.addWidget(self.auto_update)

        options.addWidget(QLabel("更新间隔", page))
        self.update_days = QSpinBox(page)
        self.update_days.setRange(1, 90)
        self.update_days.setSuffix(" 天")
        self.update_days.setValue(int(self.config.get("subscription_update_days")))
        self.update_days.valueChanged.connect(
            lambda v: self.config.set("subscription_update_days", int(v)))
        options.addWidget(self.update_days)
        options.addStretch(1)

        remove_button = QPushButton("删除选中订阅", page)
        remove_button.clicked.connect(self.on_remove_subscription)
        options.addWidget(remove_button)
        update_button = QPushButton("立即更新", page)
        update_button.setObjectName("primary")
        update_button.clicked.connect(lambda: self.update_subscriptions(manual=True))
        options.addWidget(update_button)
        layout.addLayout(options)

        self.sub_status = QLabel("", page)
        self.sub_status.setObjectName("muted")
        self.sub_status.setWordWrap(True)
        layout.addWidget(self.sub_status)

        self._reload_subscriptions()
        return page

    def _reload_subscriptions(self) -> None:
        self.sub_list.clear()
        for item in self.config.subscriptions():
            last = item.get("last_sync") or 0
            last_text = (time.strftime("%Y-%m-%d %H:%M", time.localtime(last))
                         if last else "从未更新")
            count = item.get("count", 0)
            widget = QListWidgetItem(f"{item.get('name') or item['url']}\n"
                                     f"    {item['url']}　·　上次更新：{last_text}"
                                     + (f"　·　{count} 个书源" if count else ""))
            widget.setData(Qt.UserRole, item["url"])
            self.sub_list.addItem(widget)

    def on_add_subscription(self) -> None:
        url = self.sub_edit.text().strip()
        if not url:
            return
        self.config.add_subscription(url)
        self.sub_edit.clear()
        self._reload_subscriptions()
        self.sub_status.setText("已添加订阅，点「立即更新」拉取一次。")

    def on_remove_subscription(self) -> None:
        item = self.sub_list.currentItem()
        if item is None:
            return
        self.config.remove_subscription(item.data(Qt.UserRole))
        self._reload_subscriptions()
        self.sub_status.setText("已删除订阅。")

    @guard_slot
    def update_subscriptions(self, manual: bool = True) -> None:
        """拉取所有订阅并写入配置。"""
        subs = self.config.subscriptions()
        if not subs:
            if manual:
                self.sub_status.setText("还没有添加任何订阅地址。")
            return
        self.sub_status.setText("正在更新订阅…")
        task = SubscriptionTask(self.http, subs)
        task.signals.progress.connect(self.sub_status.setText)
        task.signals.partial.connect(
            lambda info: self.sub_status.setText(
                f"已更新 {info['name']}（{info['count']} 个书源）…"))
        task.signals.finished.connect(self._on_subscriptions_updated)
        task.signals.failed.connect(
            lambda msg, detail: self.sub_status.setText(f"更新失败：{msg}"))
        self.task_manager.start(task)

    def _on_subscriptions_updated(self, result: tuple) -> None:
        parsed, failures = result
        rules = [item.rule for item in parsed]
        added = self.config.add_custom_sources(rules) if rules else 0
        now = time.time()
        for rule in rules:
            # 订阅来的书源默认启用；用户手动禁用的不会被自动打开
            if self.config.is_source_enabled(rule["key"], True):
                self.config.set_source_enabled(rule["key"], True)
        for item in self.config.subscriptions():
            count = sum(1 for p in parsed if p.rule.get("imported_from") == item["url"])
            self.config.update_subscription(item["url"], last_sync=now, count=count)
        self._reload_subscriptions()
        message = f"更新完成：{added} 个书源已写入本地。"
        if failures:
            message += "\n失败：" + "；".join(failures[:3])
        self.sub_status.setText(message)
        if rules:
            self.sources_changed.emit()
