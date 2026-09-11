# -*- coding: utf-8 -*-
"""书源注册表。

``build_sources`` 负责把内置规则 + 用户自定义规则实例化成书源对象，
并按配置过滤掉被禁用的书源。
"""
from __future__ import annotations

from typing import Dict, List

from ..config import AppConfig
from ..localbooks import LocalBooks
from ..net import HttpSession
from .base import BaseSource
from .builtin import BUILTIN_RULES
from .discover import (Candidate, DiscoveredSource, DiscoveryOutcome,
                       discover_sources)
from .importer import ParsedRule, parse_payload
from .local import LocalSource
from .probe import ProbeOutcome, probe_domain
from .rule_source import RuleSource
from .stats import SourceStats

__all__ = ["BaseSource", "RuleSource", "LocalSource", "SourceStats", "ParsedRule",
           "parse_payload", "ProbeOutcome", "probe_domain", "Candidate",
           "DiscoveredSource", "DiscoveryOutcome", "discover_sources",
           "all_rules", "build_sources", "all_sources"]


def all_rules(config: AppConfig) -> List[Dict]:
    """内置规则 + 用户自定义规则（同名 key 时以用户自定义为准）。"""
    merged: Dict[str, Dict] = {}
    for rule in BUILTIN_RULES:
        merged[rule["key"]] = dict(rule)
    for rule in config.custom_sources():
        base = dict(merged.get(rule["key"], {}))
        base.update(rule)
        merged[rule["key"]] = base
    return list(merged.values())


def _source_options(config: AppConfig) -> Dict:
    """把与书源行为相关的全局设置传给书源。"""
    return {"strict_ad_filter": bool(config.get("strict_ad_filter"))}


def local_source(config: AppConfig, http: HttpSession) -> LocalSource:
    """本地书源（导入的 TXT / EPUB）。

    它不联网，因此**不受"书源健康度自动禁用"影响**；只要库里没书，
    :meth:`LocalSource.search` 自然返回空，不会干扰网络搜索。
    """
    return LocalSource(http, LocalBooks(), strict=bool(config.get("strict_ad_filter")))


def all_sources(config: AppConfig, http: HttpSession) -> List[BaseSource]:
    """所有书源（含被禁用的），用于书源管理界面。"""
    options = _source_options(config)
    return [RuleSource(http, rule, options) for rule in all_rules(config)]


def build_sources(config: AppConfig, http: HttpSession,
                  enabled_only: bool = True) -> List[BaseSource]:
    """构造书源列表；默认只返回已启用的书源（本地书源排在最前面）。"""
    options = _source_options(config)
    sources: List[BaseSource] = []
    local = local_source(config, http)
    if not enabled_only or config.is_source_enabled(local.key, True):
        sources.append(local)
    for rule in all_rules(config):
        source = RuleSource(http, rule, options)
        if not enabled_only:
            sources.append(source)
            continue
        if config.is_source_enabled(rule["key"], rule.get("enabled_by_default", True)):
            sources.append(source)
    return sources
