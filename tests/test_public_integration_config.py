"""Public deployment configuration must not depend on a particular tenant."""
from pathlib import Path
import importlib
import pytest
from app.config import Settings

@pytest.mark.parametrize('url', ['https://demo.example.test', 'https://demo.example.test/console/'])
def test_origin_normalizes_legacy_console_urls(url):
    assert Settings(public_base_url=url, _env_file=None).public_origin == 'https://demo.example.test'

@pytest.mark.parametrize('value', ['http://demo.example.test', 'https://user:password@demo.example.test',
    'https://demo.example.test/console', 'https://demo.example.test?next=bad', 'https://dashboard.example.com'])
def test_helper_rejects_untrusted_or_placeholder_origin(value, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'integrations/collector-login'))
    helper = importlib.import_module('rpa_collector_login.cli')
    with pytest.raises(ValueError):
        helper.validate_origin(value)

def test_helper_accepts_configured_https_origin(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'integrations/collector-login'))
    helper = importlib.import_module('rpa_collector_login.cli')
    assert helper.validate_origin('https://demo.example.test') == 'https://demo.example.test'
