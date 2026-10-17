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
    result = client.post('/collector/repair/requests/' + pair['id'], headers=auth(tokens[role]), json={"platform":"xhs", "account_id":acc['id'], "user_code":pair['user_code']})
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
    assert client.post(path, headers=auth(tokens['viewer']), json={"platform":"xhs","account_id":acc['id'],"user_code":pair['user_code']}).status_code == 403
    assert client.put('/collector/repair/owner', headers=auth(tokens['analyst']), json={"platform":"xhs","account_id":acc['id'],"user_id":None}).status_code == 403
    details = client.get(path,headers=auth(tokens['analyst'])).text
    assert pair['secret'] not in details and pair['user_code'] not in details
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


def test_busy_collector_keeps_state(client,tokens,monkeypatch):
    from app.collector.session_lock import session_lock
    monkeypatch.setattr('app.views.collector_repair.INSTALL_LOCK_WAIT_SECONDS', 0.2)
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


def audit_rows(action):
    import asyncio
    from sqlalchemy import select
    from app import db
    from app.db.models import OperationLog
    async def read():
        async with db.AsyncSessionLocal() as session:
            return [json.loads(r.detail) for r in (await session.execute(
                select(OperationLog).where(OperationLog.action == action).order_by(OperationLog.id))).scalars()]
    return asyncio.run(read())


def test_wrong_pairing_code_is_rejected_then_locked(client,tokens):
    acc=setup(client,tokens)
    pair=client.post('/collector/repair/pair', json={}).json()
    assert len(pair['user_code']) == 9 and pair['user_code'][4] == '-'
    path='/collector/repair/requests/'+pair['id']
    body={"platform":"xhs","account_id":acc['id']}
    for _ in range(5):
        assert client.post(path,headers=auth(tokens['analyst']),json={**body,"user_code":"AAAA-AAAA"}).status_code == 422
    assert client.post(path,headers=auth(tokens['analyst']),json={**body,"user_code":pair['user_code']}).status_code == 429
    other=client.post('/collector/repair/pair', json={}).json()
    # Case and separators do not matter for the real code.
    code=other['user_code'].lower().replace('-',' ')
    assert client.post('/collector/repair/requests/'+other['id'],headers=auth(tokens['analyst']),json={**body,"user_code":code}).status_code == 200


def test_verification_error_is_logged_and_audited(client,tokens,caplog):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    with patch('app.collector.repair.verify_candidate',side_effect=RuntimeError("Executable doesn't exist")):
        response=upload(client,pair)
    assert response.json()['status'] == 'verification_failed'
    assert any('verification error' in r.message and 'RuntimeError' in r.message for r in caplog.records)
    assert audit_rows('collector_repair_finish')[-1]['status'] == 'verification_failed'


def test_success_is_recorded_as_repair_run_and_shown_on_targets(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    with patch('app.collector.repair.verify_candidate',return_value=True):
        assert upload(client,pair).json()['status'] == 'success'
    rows=client.get('/collector/repair/targets', headers=auth(tokens['analyst'])).json()
    assert rows[0]['verify_status'] == 'success' and rows[0]['verified_at'] and rows[0]['login_missing'] is False
    finish=audit_rows('collector_repair_finish')[-1]
    assert finish['status'] == 'success' and 'ip' in finish
    assert 'ip' in audit_rows('collector_repair_approve')[-1]


def test_installation_waits_for_running_collect(client,tokens,monkeypatch):
    import threading
    from app.collector.session_lock import acquire_lock
    monkeypatch.setattr('app.views.collector_repair.INSTALL_LOCK_WAIT_SECONDS', 10)
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    path=session_path('xhs',acc['id']); path.write_text('original')
    held=acquire_lock(path, 0)
    threading.Timer(0.5, os.close, args=(held,)).start()
    with patch('app.collector.repair.verify_candidate',return_value=True):
        response=upload(client,pair)
    assert response.json()['status'] == 'success'
    assert path.read_text() != 'original'


def test_slow_verification_past_expiry_still_installs(client,tokens):
    import asyncio
    from app import db
    from app.db.models import CollectorRepair
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    def slow(*args):
        async def expire():
            async with db.AsyncSessionLocal() as session:
                from datetime import datetime, timezone
                (await session.get(CollectorRepair, pair['id'])).expires_at = datetime.now(timezone.utc)
                await session.commit()
        asyncio.run(expire())
        return True
    with patch('app.collector.repair.verify_candidate',side_effect=slow):
        assert upload(client,pair).json()['status'] == 'success'


def test_revocation_during_verification_is_audited(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    def verify(*args):
        client.put('/collector/repair/owner',headers=auth(tokens['admin']),json={"platform":"xhs","account_id":acc['id'],"user_id":None})
        return True
    with patch('app.collector.repair.verify_candidate',side_effect=verify):
        assert upload(client,pair).status_code == 403
    assert audit_rows('collector_repair_finish')[-1]['status'] == 'revoked'


def test_admin_session_delete_waits_for_running_collector(client,tokens):
    from app.collector.session_lock import session_lock
    acc=_create_xhs_account(client,tokens['admin'])
    path=session_path('xhs',acc['id']); path.write_text('{}')
    url=f"/admin/collector/sessions?platform=xhs&account_id={acc['id']}"
    with session_lock(path):
        assert client.delete(url,headers=auth(tokens['admin'])).status_code == 409
    assert path.exists()
    assert client.delete(url,headers=auth(tokens['admin'])).status_code == 204
    assert not path.exists()


def test_deleting_account_drops_its_owners(client,tokens):
    acc=setup(client,tokens)
    assert client.delete(f"/media/xhs/accounts/{acc['id']}",headers=auth(tokens['admin'])).status_code == 204
    import asyncio
    from sqlalchemy import select
    from app import db
    from app.db.models import CollectorOwner
    async def owners():
        async with db.AsyncSessionLocal() as session:
            return (await session.execute(select(CollectorOwner))).scalars().all()
    assert asyncio.run(owners()) == []


def test_readiness_is_admin_only_and_reports_browser(client,tokens,monkeypatch):
    monkeypatch.setattr('app.views.collector_repair._browser_ready', lambda: False)
    assert client.get('/collector/repair/readiness',headers=auth(tokens['analyst'])).status_code == 403
    body=client.get('/collector/repair/readiness',headers=auth(tokens['admin'])).json()
    assert body['ready'] is False and body['browser_ready'] is False and body['sessions_writable'] is True
    monkeypatch.setattr('app.views.collector_repair._browser_ready', lambda: True)
    assert client.get('/collector/repair/readiness',headers=auth(tokens['admin'])).json()['ready'] is True


def test_download_serves_only_the_current_release(client,tmp_path,monkeypatch):
    from app.views import collector_repair
    monkeypatch.setattr(collector_repair, 'HELPER_DIST', tmp_path)
    wheel = 'omnipanel_collector_login-1.0.1-py3-none-any.whl'
    assert collector_repair.helper_files() == set()
    assert client.get('/collector/repair/download/' + wheel).status_code == 404
    (tmp_path / wheel).write_bytes(b'wheel')
    (tmp_path / 'omnipanel_collector_login-1.0.0-py3-none-any.whl').write_bytes(b'old')
    (tmp_path / 'release.json').write_text(json.dumps({'version': '1.0.1', 'wheel': wheel, 'sha256': 'x'}))
    assert client.get('/collector/repair/download/' + wheel).status_code == 200
    assert client.get('/collector/repair/download/omnipanel_collector_login-1.0.0-py3-none-any.whl').status_code == 404
    assert client.get('/collector/repair/download/release.json').status_code == 404


def test_recheck_uses_the_portal_display_name(client,tokens):
    acc=setup(client,tokens)
    admin=auth(tokens['admin'])
    assert client.patch(f"/media/xhs/accounts/{acc['id']}",json={"portal_name":"阳光小铺","pgy_portal_name":"阳光小铺品牌"},headers=admin).status_code == 200
    pair=approved(client,tokens,acc)
    assert client.get('/collector/repair/local/'+pair['id'],headers=auth(pair['secret'])).json()['name'] == '阳光小铺'
    with patch('app.collector.repair.verify_candidate',return_value=True) as verify:
        assert upload(client,pair).json()['status'] == 'success'
    assert verify.call_args.args[2] == '阳光小铺'
    # The internal name can carry notes without breaking re-checks; "" clears the override.
    listed=client.get('/media/xhs/accounts',headers=admin).json()
    row=next(r for r in listed if r['id']==acc['id'])
    assert row['portal_name']=='阳光小铺' and row['pgy_portal_name']=='阳光小铺品牌'
    assert client.patch(f"/media/xhs/accounts/{acc['id']}",json={"portal_name":""},headers=admin).json()['portal_name'] is None


def test_portal_name_change_during_verification_is_revoked(client,tokens):
    acc=setup(client,tokens); pair=approved(client,tokens,acc)
    path=session_path('xhs',acc['id']); path.write_text('original')
    def verify(*args):
        assert client.patch(f"/media/xhs/accounts/{acc['id']}",json={"portal_name":"别的账号"},headers=auth(tokens['admin'])).status_code == 200
        return True
    with patch('app.collector.repair.verify_candidate',side_effect=verify):
        assert upload(client,pair).status_code == 403
    assert path.read_text() == 'original'
