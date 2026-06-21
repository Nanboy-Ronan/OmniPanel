"""Tests for the topic-keyword diagnostics tab added to XHS/Zhihu pages.

`match_article_topics()` is a platform-agnostic pure function (already used
by the WeChat page's equivalent tab) — these tests exercise it directly
against XHS/Zhihu-shaped title strings, since the new tab code in
`app/ui/pages/xhs_upload.py` / `zhihu_upload.py` just calls this same
function on the `title` field already present in their posts-list response.
"""
from __future__ import annotations

from app.views.media.analysis import match_article_topics


class TestTopicMatchingAgainstXhsStyleTitles:
    def test_xhs_title_matches_keyword_topic(self):
        topics = {"新品": ["新品", "上市"], "促销": ["折扣", "特惠"]}
        assert match_article_topics("人参精华新品上市啦", topics) == ["新品"]

    def test_xhs_title_matches_multiple_topics(self):
        topics = {"新品": ["新品"], "促销": ["特惠"]}
        result = match_article_topics("新品限时特惠开抢", topics)
        assert set(result) == {"新品", "促销"}

    def test_xhs_title_no_match_falls_back_to_other(self):
        topics = {"新品": ["新品", "上市"]}
        assert match_article_topics("今日穿搭分享", topics) == ["其他"]


class TestTopicMatchingAgainstZhihuStyleTitles:
    def test_zhihu_article_title_matches_topic(self):
        topics = {"功效科普": ["功效", "成分"]}
        assert match_article_topics("人参的功效与作用全解析", topics) == ["功效科普"]

    def test_zhihu_qa_title_no_match_falls_to_other(self):
        topics = {"新品": ["新品"]}
        assert match_article_topics("如何挑选保健品", topics) == ["其他"]

    def test_empty_topic_map_returns_other(self):
        assert match_article_topics("任意标题", {}) == ["其他"]
