# -*- coding: utf-8 -*-
"""NovelFound —— PC 端小说搜索与阅读器。

模块划分：
    config.py    配置与数据目录
    net.py       网络访问（重试、超时、编码识别、友好异常）
    cleaner.py   正文清洗与广告过滤
    models.py    数据模型
    sources/     书源适配层（规则驱动，可扩展）
    cache.py     本地缓存（章节/目录/封面）
    library.py   书架与阅读进度
    tasks.py     异步任务（QThreadPool）
    ui/          界面（主窗口、搜索卡片、详情目录、内置阅读器）
"""

from .config import APP_NAME, APP_TITLE, APP_VERSION

__all__ = ["APP_NAME", "APP_TITLE", "APP_VERSION"]
__version__ = APP_VERSION
