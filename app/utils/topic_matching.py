# rap/app/utils/topic_matching.py
"""Keyword-based topic matching for content titles.

Pure function, no I/O — shared by the WeChat/XHS/Zhihu content-analysis UI
pages directly (no HTTP round-trip needed for a string match) and by the
WeChat topic × traffic-source cross-aggregation in app/views/media/analysis.py.
"""
from __future__ import annotations


def match_article_topics(title: str, topic_keywords: dict[str, list[str]]) -> list[str]:
    """Return the list of topic names whose keywords appear in *title*.

    Matching is case-insensitive. Returns ["其他"] when nothing matches or the
    topic_keywords map is empty.
    """
    if not title or not topic_keywords:
        return ["其他"]
    matched = [
        topic
        for topic, keywords in topic_keywords.items()
        if any(kw.lower() in title.lower() for kw in keywords)
    ]
    return matched or ["其他"]
