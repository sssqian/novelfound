# -*- coding: utf-8 -*-
"""探测新书源对话框（方案 B）。

流程：填一个站点域名 → 开始探测 → 实时看到四关进度 → 预览抓到的书与正文
→ 满意就保存为书源（自动启用），不满意可以改规则 JSON 再保存或直接放弃。

探测出来的规则和内置书源同构，所以后续也能用同一套方式重新探测覆盖。
"""
from __future__ import annotations

import json
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..errors import guard_slot
from ..net import HttpSession
from ..sources.probe import ProbeOutcome
from ..tasks import ProbeTask, TaskManager


class SourceProbeDialog(QDialog):
    """自动探测新站点并生成书源规则。"""

    sources_changed = pyqtSignal()

    def __init__(self, config: AppConfig, http: HttpSession, task_manager: TaskManager,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.http = http
        self.task_manager = task_manager
        self.outcome: Optional[ProbeOutcome] = None
        self._task: Optional[ProbeTask] = None

        self.setWindowTitle("探测新书源")
        self.resize(720, 620)

        layout = QVBoxLayout(self)

        tip = QLabel(
            "给程序一个小说站的首页地址，它会自动尝试常见的搜索入口与页面结构，"
            "并验证「搜索结果 → 目录 → 正文」三关是否都能解析。\n"
            "探测只读取公开页面，不执行站点脚本、不处理登录与人机验证。", self)
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("站点地址", self))
        self.url_edit = QLineEdit(self)
        self.url_edit.setObjectName("plain")
        self.url_edit.setPlaceholderText("https://www.example.com")
        self.url_edit.returnPressed.connect(self.on_probe)
        url_row.addWidget(self.url_edit, 1)
        layout.addLayout(url_row)

        keyword_row = QHBoxLayout()
        keyword_row.addWidget(QLabel("测试书名", self))
        self.keyword_edit = QLineEdit(self)
        self.keyword_edit.setObjectName("plain")
        self.keyword_edit.setText(self.config.get("last_search") or "斗罗大陆")
        keyword_row.addWidget(self.keyword_edit, 1)
        self.deep_box = QCheckBox("深度探测（多试几套模板，耗时更长）", self)
        keyword_row.addWidget(self.deep_box)
        layout.addLayout(keyword_row)

        buttons = QHBoxLayout()
        self.probe_button = QPushButton("开始探测", self)
        self.probe_button.setObjectName("primary")
        self.probe_button.clicked.connect(self.on_probe)
        buttons.addWidget(self.probe_button)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.on_cancel)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("探测进度会显示在这里…")
        self.log.setMinimumHeight(140)
        layout.addWidget(self.log)

        self.preview_label = QLabel("", self)
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.preview_label)

        self.editor = QPlainTextEdit(self)
        self.editor.setPlaceholderText("保存前可以在这里直接修改规则 JSON")
        self.editor.setVisible(False)
        layout.addWidget(self.editor, 1)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("保存为书源", self)
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save)
        action_row.addWidget(self.save_button)
        self.edit_button = QPushButton("编辑规则 JSON", self)
        self.edit_button.setCheckable(True)
        self.edit_button.setEnabled(False)
        self.edit_button.toggled.connect(self.on_toggle_editor)
        action_row.addWidget(self.edit_button)
        action_row.addStretch(1)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.reject)
        action_row.addWidget(close_button)
        layout.addLayout(action_row)

    # ------------------------------------------------------------------ 探测
    @guard_slot
    def on_probe(self) -> None:
        url = self.url_edit.text().strip()
        keyword = self.keyword_edit.text().strip() or "斗罗大陆"
        if not url:
            self.log.setPlainText("请先填写站点地址，例如 https://www.example.com")
            return
        self.log.clear()
        self.preview_label.setText("")
        self.editor.setVisible(False)
        self.edit_button.setChecked(False)
        self.save_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.probe_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._task = ProbeTask(self.http, url, keyword, deep=self.deep_box.isChecked())
        self._task.signals.progress.connect(self._append_log)
        self._task.signals.finished.connect(self._on_finished)
        self._task.signals.failed.connect(self._on_failed)
        self.task_manager.start(self._task)

    def on_cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._append_log("已取消探测。")
        self._reset_buttons()

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _reset_buttons(self) -> None:
        self.probe_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_failed(self, message: str, detail: str) -> None:
        self._reset_buttons()
        self._append_log(f"探测失败：{message}")
        if detail:
            self._append_log(f"  {detail}")

    def _on_finished(self, outcome: ProbeOutcome) -> None:
        self._reset_buttons()
        self.outcome = outcome
        if outcome.ok:
            self._show_success(outcome)
        else:
            self._show_failure(outcome)

    def _show_success(self, outcome: ProbeOutcome) -> None:
        preview = outcome.preview
        lines = [
            f"<b>✓ 匹配模板：{outcome.template}</b>",
            f"搜索结果 {outcome.books_found} 条　·　目录 {outcome.chapter_count} 章　·　"
            f"正文 {outcome.char_count} 字　·　耗时 {outcome.elapsed:.1f}s",
            "",
            f"书名：{preview.get('title', '')}"
            + (f"　作者：{preview.get('author', '')}" if preview.get("author") else ""),
        ]
        chapters = preview.get("chapters") or []
        if chapters:
            lines.append("前三章：" + " / ".join(chapters))
        snippet = preview.get("snippet")
        if snippet:
            lines.append("")
            lines.append("正文开头：" + snippet.replace("\n", " "))
        self.preview_label.setText("<br>".join(lines))
        self.editor.setPlainText(json.dumps(outcome.rule, ensure_ascii=False, indent=2))
        self.save_button.setEnabled(True)
        self.edit_button.setEnabled(True)

    def _show_failure(self, outcome: ProbeOutcome) -> None:
        self.preview_label.setText(
            f"<b>✗ 探测未通过</b>（卡在「{outcome.stage}」）<br>{outcome.error}")
        if outcome.partial_rule:
            # 只探到一半：把已经对上的部分给出来，用户补选择器即可
            self.preview_label.setText(
                self.preview_label.text()
                + "<br><br>下面是已经探测成功的部分规则，可点击「编辑规则 JSON」补全后保存：")
            self.editor.setPlainText(json.dumps(outcome.partial_rule, ensure_ascii=False,
                                                indent=2))
            self.edit_button.setEnabled(True)
            self.edit_button.setChecked(True)
            self.save_button.setEnabled(True)

    # ------------------------------------------------------------------ 保存
    def on_toggle_editor(self, checked: bool) -> None:
        self.editor.setVisible(checked)

    @guard_slot
    def on_save(self) -> None:
        if self.outcome is None:
            return
        try:
            rule = json.loads(self.editor.toPlainText() or "{}")
        except ValueError as exc:
            self.preview_label.setText(f"<b>规则 JSON 有误</b>：{exc}")
            return
        if not isinstance(rule, dict) or not rule.get("key") or not rule.get("base_url"):
            self.preview_label.setText("<b>规则不完整</b>：至少需要 key 与 base_url。")
            return
        self.config.add_custom_source(rule)
        self.config.set_source_enabled(rule["key"], True)
        self.preview_label.setText(
            f"<b>✓ 已保存并启用：{rule.get('name') or rule['key']}</b><br>"
            "回到主界面搜索一次即可看到这个书源的结果。")
        self.save_button.setEnabled(False)
        self.sources_changed.emit()
