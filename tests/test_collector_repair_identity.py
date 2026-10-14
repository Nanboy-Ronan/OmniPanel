from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from app.collector.repair import verify_candidate

@pytest.mark.parametrize('url,text,visible,expected',[
    ('https://pro.xiaohongshu.com/enterprise/home','企业概览', True, True),
    ('https://pro.xiaohongshu.com/login','短信登录', True, False),
    ('https://pro.xiaohongshu.com/enterprise/home','企业概览', False, False),
])
def test_requires_authenticated_page_and_exact_visible_identity(monkeypatch,url,text,visible,expected):
    page=MagicMock();page.url=url
    page.get_by_text.return_value.count.return_value=1
    page.get_by_text.return_value.nth.return_value.is_visible.return_value=visible
    @contextmanager
    def context(*args,**kwargs): yield page
    monkeypatch.setattr('app.collector.repair.open_context',context)
    monkeypatch.setattr('app.collector.repair.visible_text',lambda _:text)
    assert verify_candidate('xhs',Path('candidate'),'示例品牌') is expected
    if '/login' not in url:
        page.get_by_text.assert_called_with('示例品牌',exact=True)


def test_helper_does_not_export_unrelated_browser_credentials(monkeypatch):
    import importlib, sys
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'integrations/collector-login'))
    module = importlib.import_module('rpa_collector_login.cli')
    context=MagicMock()
    context.storage_state.return_value={"cookies":[{"domain":".xiaohongshu.com","value":"a"},{"domain":"evilxiaohongshu.com","value":"b"},{"domain":"dashboard.example.com","value":"platform-token"}],"origins":[{"origin":"https://pro.xiaohongshu.com"},{"origin":"https://other.example.com"}]}
    result=module.restricted_state(context,'xhs')
    assert result == {"cookies":[{"domain":".xiaohongshu.com","value":"a"}],"origins":[{"origin":"https://pro.xiaohongshu.com"}]}
