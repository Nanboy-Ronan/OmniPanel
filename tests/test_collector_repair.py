"""Self-service login permissions, single-use uploads and non-destructive failure."""
import json
import os
from unittest.mock import patch
import pytest
from test_api_endpoints import client, tokens
from test_collector_sessions_api import _create_xhs_account
from app.config import settings
from app.collector.paths import session_path


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture(autouse=True)
def collector_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "collector_dir", str(tmp_path))


def setup(client, tokens, owner="analyst"):
    acc = _create_xhs_account(client, tokens["admin"])
    user = client.get('/auth/me', headers=auth(tokens[owner])).json()
    result = client.put('/collector/repair/owner', headers=auth(tokens['admin']), json={"platform":"xhs", "account_id":acc['id'], "user_id":user['id']})
    assert result.status_code == 200, result.text
    return acc


def approved(client, tokens, acc, role='analyst'):
    pair = client.post('/collector/repair/pair', json={}).json()
    result = client.post('/collector/repair/requests/' + pair['id'], headers=auth(tokens[role]), json={"platform":"xhs", "account_id":acc['id']})
    assert result.status_code == 200, result.text
    return pair


def upload(client, pair):
    return client.post('/collector/repair/local/' + pair['id'], headers=auth(pair['secret']), json={"cookies":[{"name":"test", "value":"secret", "domain":".xiaohongshu.com", "path":"/"}], "origins":[]})


def test_owner_only_and_no_secret_disclosure(client, tokens):
    acc = setup(client,tokens)
    assert client.get('/collector/repair/targets', headers=auth(tokens['viewer'])).json() == []
    rows = client.get('/collector/repair/targets', headers=auth(tokens['analyst'])).json()
    assert rows[0]['account_id'] == acc['id']
    pair = client.post('/collector/repair/pair', json={}).json()
    path = '/collector/repair/requests/' + pair['id']
    assert client.post(path, headers=auth(tokens['viewer']), json={"platform":"xhs","account_id":acc['id']}).status_code == 403
    assert client.put('/collector/repair/owner', headers=auth(tokens['analyst']), json={"platform":"xhs","account_id":acc['id'],"user_id":None}).status_code == 403
    assert pair['secret'] not in client.get(path,headers=auth(tokens['analyst'])).text
    assert client.get('/collector/repair/local/'+pair['id'],headers=auth('wrong')).status_code == 401


@pytest.mark.parametrize('valid,expected', [(True,'success'),(False,'verification_failed')])
def test_verify_before_replace_and_replay(client,tokens,valid,expected):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    path=session_path('xhs',acc['id']); path.write_text('original')
    with patch('app.collector.repair.verify_candidate',return_value=valid):
        response=upload(client,pair)
    assert response.status_code == 200,response.text
    assert response.json()['status'] == expected
    assert (path.read_text() != 'original') == valid
    assert upload(client,pair).status_code == 409
    if valid: assert os.stat(path).st_mode & 0o777 == 0o600
    assert 'secret' not in response.text


def test_owner_removed_after_approval(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    client.put('/collector/repair/owner',headers=auth(tokens['admin']),json={"platform":"xhs","account_id":acc['id'],"user_id":None})
    assert upload(client,pair).status_code == 403


def test_busy_collector_keeps_state(client,tokens):
    from app.collector.session_lock import session_lock
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    path=session_path('xhs',acc['id']); path.write_text('original')
    with session_lock(path),patch('app.collector.repair.verify_candidate',return_value=True):
        response=upload(client,pair)
    assert response.json()['status'] == 'collector_busy',response.text
    assert path.read_text() == 'original'


def test_unrelated_cookie_domain_rejected(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    result=client.post('/collector/repair/local/'+pair['id'],headers=auth(pair['secret']),json={"cookies":[{"domain":"evil.com", "value":"secret"}]})
    assert result.status_code == 422


def test_admin_can_repair_unassigned(client,tokens):
    acc=_create_xhs_account(client,tokens['admin'])
    approved(client,tokens,acc,'admin')


def test_viewer_owner_can_repair_without_analytics_role(client,tokens):
    acc=setup(client,tokens,'viewer')
    approved(client,tokens,acc,'viewer')


def test_permission_revoked_during_verification(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    path=session_path('xhs',acc['id']); path.write_text('original')
    def verify(*args):
        response=client.put('/collector/repair/owner',headers=auth(tokens['admin']),json={"platform":"xhs","account_id":acc['id'],"user_id":None})
        assert response.status_code == 200
        return True
    with patch('app.collector.repair.verify_candidate',side_effect=verify):
        response=upload(client,pair)
    assert response.status_code == 403
    assert path.read_text() == 'original'


def test_expired_pair_rejected(client,tokens):
    from datetime import timedelta
    from app.views.collector_repair import now
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    with patch('app.views.collector_repair.now',return_value=now()+timedelta(minutes=16)):
        assert upload(client,pair).status_code == 410


def test_oversized_upload_rejected(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    result=client.post('/collector/repair/local/'+pair['id'],headers=auth(pair['secret']),content=b'x'*(1024*1024+1))
    assert result.status_code == 413
