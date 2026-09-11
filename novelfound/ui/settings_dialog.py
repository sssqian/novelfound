# -*- coding: utf-8 -*-
"""设置对话框：阅读偏好、网络与缓存、书源管理。"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget)

from ..cache import Cache
from ..config import AppConfig
from ..net import HttpSession
from ..sources import BaseSource, all_rules, build_sources
from ..tasks import HealthTask, TaskManager
from .source_discover_dialog import SourceDiscoverDialog
from .source_import_dialog import SourceImportDialog
from .source_probe_dialog import SourceProbeDialog

CUSTOM_TEMPLATE = """{
  "key": "my_source",
  "name": "我的书源",
  "base_url": "https://example.com",
  "search_url": "/search.php?keyword={q}",
  "search_items": ["table.grid tr"],
  "book_title": ["td:nth-of-type(1) a"],
  "book_link": ["td:nth-of-type(1) a"],
  "book_author": ["td:nth-of-type(3)"],
  "catalog_groups": ["#list dl"],
  "catalog_items": ["dd a"],
  "content": ["#content"],
  "chapter_title": ["div.bookname h1"]
}"""


class SettingsDialog(QDialog):
    """设置窗口。"""

    sources_changed = pyqtSignal()
    reader_settings_changed = pyqtSignal()

    def __init__(self, config: AppConfig, http: HttpSession, cache: Cache,
                 sources: List[BaseSource], task_manager: TaskManager,
                 parent: Optional[QWidget] = None, stats=None):
        super().__init__(parent)
        self.config = config
        self.http = http
        self.cache = cache
        self.sources = sources
        self.task_manager = task_manager
        self.stats = stats
        self.setWindowTitle("设置")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._build_appearance_tab(), "外观与其它")
        tabs.addTab(self._build_network_tab(), "网络与缓存")
        tabs.addTab(self._build_source_tab(), "书源")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---------------------------------------------------------- 外观与其它
    def _build_appearance_tab(self) -> QWidget:
        """P2 起阅读相关项都搬到了阅读器的 `Aa` 浮层，这里只留全局项。

        以前设置里也有一份字号/主题/排版，和阅读器里的控件形成"两处真相"，
        容易出现"设置里改了但阅读器没生效"的困惑——现在只剩一份。
        """
        page = QWidget(self)
        layout = QVBoxLayout(page)

        note = QLabel(
            "阅读相关的字号 / 字体 / 行距 / 段距 / 首行缩进 / 正文宽度 /\n"
            "背景主题 / 翻页方式 / 单双页，请在阅读器里点右上角「Aa」调整，\n"
            "改完即时生效。", page)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        box = QGroupBox("其它", page)
        form = QFormLayout(box)

        self.strict_filter = QCheckBox("严格广告过滤（会丢弃含外链、推广词的整行）", box)
        self.strict_filter.setChecked(bool(self.config.get("strict_ad_filter")))
        form.addRow("", self.strict_filter)

        self.auto_cover = QCheckBox("自动加载封面图片", box)
        self.auto_cover.setChecked(bool(self.config.get("auto_load_cover")))
        form.addRow("", self.auto_cover)
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ 网络与缓存
    def _build_network_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        box = QGroupBox("网络", page)
        form = QFormLayout(box)
        self.timeout = QDoubleSpinBox(box)
        self.timeout.setRange(3, 60)
        self.timeout.setSingleStep(1)
        self.timeout.setValue(float(self.config.get("timeout")))
        form.addRow("请求超时（秒）", self.timeout)

        self.retries = QSpinBox(box)
        self.retries.setRange(0, 5)
        self.retries.setValue(int(self.config.get("retries")))
        form.addRow("失败重试次数", self.retries)

        self.limit = QSpinBox(box)
        self.limit.setRange(5, 100)
        self.limit.setValue(int(self.config.get("search_limit")))
        form.addRow("每个书源最多返回", self.limit)

        self.request_interval = QDoubleSpinBox(box)
        self.request_interval.setRange(0.0, 3.0)
        self.request_interval.setSingleStep(0.1)
        self.request_interval.setSuffix(" 秒")
        self.request_interval.setValue(float(self.config.get("request_interval")))
        self.request_interval.setToolTip(
            "同一个站点两次请求之间的最小间隔（带随机抖动）。调大可降低被限流的概率。")
        form.addRow("同站请求间隔", self.request_interval)

        self.cover_concurrent = QSpinBox(box)
        self.cover_concurrent.setRange(1, 5)
        self.cover_concurrent.setValue(int(self.config.get("cover_max_concurrent")))
        self.cover_concurrent.setToolTip(
            "封面同时下载的数量上限；卡片滚到可见区域才开始下载。")
        form.addRow("封面并发数", self.cover_concurrent)
        layout.addWidget(box)

        cache_box = QGroupBox("本地缓存", page)
        cache_form = QFormLayout(cache_box)
        self.cache_enabled = QCheckBox("启用章节正文 / 目录缓存（支持离线重读）", cache_box)
        self.cache_enabled.setChecked(bool(self.config.get("cache_enabled")))
        cache_form.addRow("", self.cache_enabled)

        self.cache_days = QSpinBox(cache_box)
        self.cache_days.setRange(1, 365)
        self.cache_days.setValue(int(self.config.get("cache_days")))
        cache_form.addRow("缓存有效期（天）", self.cache_days)

        stats = self.cache.stats()
        info = QLabel(
            f"缓存位置：{stats.get('path', '不可用')}\n"
            f"已缓存章节 {stats.get('chapters', 0)} 条 / 目录 {stats.get('books', 0)} 本 / "
            f"封面 {stats.get('covers', 0)} 张，占用 {stats.get('size', 0) / 1024 / 1024:.1f} MB",
            cache_box)
        info.setObjectName("muted")
        info.setWordWrap(True)
        cache_form.addRow(info)

        row = QHBoxLayout()
        for label, what in (("清空章节缓存", "chapter"), ("清空目录缓存", "detail"),
                            ("清空封面缓存", "cover"), ("清空全部缓存", "all")):
            button = QPushButton(label, cache_box)
            button.clicked.connect(lambda _=False, w=what: self._clear_cache(w))
            row.addWidget(button)
        row.addStretch(1)
        cache_form.addRow(row)
        layout.addWidget(cache_box)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- 书源页
    def _build_source_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        hint = QLabel("勾选启用书源；搜索时会同时在所有启用的书源上查找，"
                      "某个源失败不影响其它源。", page)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.source_list = QListWidget(page)
        self.source_list.setMinimumHeight(180)
        layout.addWidget(self.source_list, 1)
        self._reload_sources()

        row = QHBoxLayout()
        import_button = QPushButton("导入书源 / 订阅…", page)
        import_button.setObjectName("primary")
        import_button.clicked.connect(self._open_import_dialog)
        row.addWidget(import_button)
        probe_button = QPushButton("探测新书源…", page)
        probe_button.setToolTip("给一个站点域名，自动试出可用的搜索入口与解析模板")
        probe_button.clicked.connect(self._open_probe_dialog)
        row.addWidget(probe_button)
        discover_button = QPushButton("网络找书源…", page)
        discover_button.setToolTip("用搜索引擎找候选站点，再自动验证哪些真的能用")
        discover_button.clicked.connect(self._open_discover_dialog)
        row.addWidget(discover_button)
        check_button = QPushButton("检测可用性", page)
        check_button.clicked.connect(self._check_sources)
        row.addWidget(check_button)
        add_button = QPushButton("添加 / 修改自定义书源", page)
        add_button.clicked.connect(self._edit_custom_source)
        row.addWidget(add_button)
        remove_button = QPushButton("删除自定义书源", page)
        remove_button.clicked.connect(self._remove_custom_source)
        row.addWidget(remove_button)
        reset_button = QPushButton("重置评分", page)
        reset_button.setToolTip("清空成功率 / 连续失败记录（自动禁用的书源也会解锁）")
        reset_button.clicked.connect(self._reset_stats)
        row.addWidget(reset_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.health_label = QLabel("", page)
        self.health_label.setObjectName("muted")
        self.health_label.setWordWrap(True)
        layout.addWidget(self.health_label)
        return page

    def _reload_sources(self) -> None:
        self.source_list.clear()
        for rule in all_rules(self.config):
            key = rule["key"]
            source = self._find_source(key)
            name = rule.get("name") or key
            base = rule.get("base_url", "")
            note = rule.get("note", "")
            enabled = self.config.is_source_enabled(
                key, rule.get("enabled_by_default", True))
            status = ""
            if self.stats is not None:
                status = self.stats.describe(key)
            lines = [f"{name}    {base}"]
            if status and status != "未使用":
                lines.append(f"    {status}")
            if note:
                lines.append(f"    {note}")
            item = QListWidgetItem("\n".join(lines))
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            if source is None:
                item.setToolTip("自定义 / 导入的书源")
            self.source_list.addItem(item)

    def _find_source(self, key: str) -> Optional[BaseSource]:
        for source in self.sources:
            if source.key == key:
                return source
        return None

    def _open_import_dialog(self) -> None:
        """打开导入 / 订阅对话框。"""
        dialog = SourceImportDialog(self.config, self.http, self.task_manager, self)
        dialog.sources_changed.connect(self._on_sources_imported)
        dialog.exec_()

    def _on_sources_imported(self) -> None:
        """导入完成后刷新列表（并把新源加入内存中的书源集合）。"""
        self.sources = build_sources(self.config, self.http, enabled_only=False)
        self._reload_sources()
        self.sources_changed.emit()

    def _open_probe_dialog(self) -> None:
        """打开「探测新书源」对话框。"""
        dialog = SourceProbeDialog(self.config, self.http, self.task_manager, self)
        dialog.sources_changed.connect(self._on_sources_imported)
        dialog.exec_()

    def _open_discover_dialog(self) -> None:
        """打开「网络找书源」对话框（方案 C）。"""
        dialog = SourceDiscoverDialog(self.config, self.http, self.task_manager,
                                      parent=self)
        dialog.sources_changed.connect(self._on_sources_imported)
        dialog.exec_()

    def _reset_stats(self) -> None:
        if self.stats is not None:
            self.stats.reset()
        self._reload_sources()
        self.health_label.setText("已清空书源评分记录。")

    def _check_sources(self) -> None:
        if self.task_manager is None:
            return
        self.health_label.setText("正在检测…")
        task = HealthTask(self.sources)
        task.signals.partial.connect(self._on_health_partial)
        task.signals.finished.connect(self._on_health_finished)
        task.signals.failed.connect(
            lambda msg, detail: self.health_label.setText(f"检测失败：{msg}"))
        self.task_manager.start(task)

    def _on_health_partial(self, result: dict) -> None:
        text = f"{'✓' if result['ok'] else '✗'} {result['name']}：{result['message']}"
        old = self.health_label.text()
        if old in ("", "正在检测…"):
            old = ""
        self.health_label.setText((old + "\n" + text).strip())
        if self.stats is not None:
            self.stats.record(result["key"], ok=bool(result["ok"]),
                              elapsed=float(result.get("elapsed", 0)),
                              error="" if result["ok"] else result["message"])

    def _on_health_finished(self, results: List[dict]) -> None:
        ok = sum(1 for r in results if r["ok"])
        self.health_label.setText(
            self.health_label.text() + f"\n共 {len(results)} 个书源，可用 {ok} 个。")

    def _edit_custom_source(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("自定义书源（JSON 规则）")
        dialog.resize(620, 520)
        layout = QVBoxLayout(dialog)
        tip = QLabel("规则字段与内置书源一致，可参考下面模板；"
                     "``{q}`` 会被替换为搜索关键词。", dialog)
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        editor = QPlainTextEdit(dialog)
        editor.setPlainText(CUSTOM_TEMPLATE)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        layout.addWidget(buttons)

        def save() -> None:
            try:
                rule: Dict = json.loads(editor.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(dialog, "JSON 格式错误", f"无法解析：{exc}")
                return
            missing = [k for k in ("key", "name", "base_url") if not rule.get(k)]
            if missing:
                QMessageBox.warning(dialog, "缺少字段", "必须包含：" + "、".join(missing))
                return
            self.config.add_custom_source(rule)
            self.config.set_source_enabled(rule["key"], True)
            self._reload_sources()
            self.sources_changed.emit()
            dialog.accept()

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def _remove_custom_source(self) -> None:
        item = self.source_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if not any(r["key"] == key for r in self.config.custom_sources()):
            QMessageBox.information(self, "提示", "内置书源不能删除，只能取消勾选。")
            return
        self.config.remove_custom_source(key)
        self._reload_sources()
        self.sources_changed.emit()

    def _clear_cache(self, what: str) -> None:
        self.cache.clear(what)
        QMessageBox.information(self, "完成", "缓存已清空。")

    # ------------------------------------------------------------------ 保存
    def _on_accept(self) -> None:
        self.config.update({
            "strict_ad_filter": self.strict_filter.isChecked(),
            "auto_load_cover": self.auto_cover.isChecked(),
            "timeout": self.timeout.value(),
            "retries": self.retries.value(),
            "search_limit": self.limit.value(),
            "request_interval": round(self.request_interval.value(), 2),
            "cover_max_concurrent": self.cover_concurrent.value(),
            "cache_enabled": self.cache_enabled.isChecked(),
            "cache_days": self.cache_days.value(),
        })
        enabled_map = dict(self.config.get("enabled_sources") or {})
        for i in range(self.source_list.count()):
            item = self.source_list.item(i)
            enabled_map[item.data(Qt.UserRole)] = (item.checkState() == Qt.Checked)
        self.config.set("enabled_sources", enabled_map, autosave=False)
        self.config.save()
        self.reader_settings_changed.emit()
        self.sources_changed.emit()
        self.accept()
