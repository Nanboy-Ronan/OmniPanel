"""Production-shaped data, privacy boundaries, complete periods and audit isolation."""
import asyncio
import json
from datetime import date, timedelta
import pytest
from sqlalchemy import create_engine, text
from test_workbuddy import client, tokens, enabled, issue, call, rpc, jwt_headers


@pytest.fixture
def business_data(client):
    from app import db
    from app.db.models import MediaAccount, MediaPost, MediaPostMetricDaily, XhsAccount, XhsPost, XhsAccountDailyMetric, XhsAudienceSourceDaily, PgyNote, ZhihuPost, WeeklyReportRun
    async def seed():
        async with db.AsyncSessionLocal() as s:
            w = MediaAccount(platform='wechat_official', name='wechat', app_id='private-app-id', app_secret='private-secret')
            x = XhsAccount(name='xhs')
            off = XhsAccount(name='off', is_active=False)
            s.add_all([w,x,off]); await s.flush()
            p = MediaPost(account_id=w.id, platform='wechat_official', external_id='p', title='old article', publish_date=date(2026,9,1))
            n = MediaPost(account_id=w.id, platform='wechat_official', external_id='n', title='new article', publish_date=date(2026,10,7))
            s.add_all([p,n]); await s.flush()
            s.add_all([
                MediaPostMetricDaily(post_id=p.id, metric_date=date(2026,10,4), read_user_count=100),
                MediaPostMetricDaily(post_id=p.id, metric_date=date(2026,10,11), read_user_count=125, read_avg_time=80, read_finish_rate=.2,
                    read_user_source=[{'scene_desc':'搜索','user_count':12,'secret':'private-source-extra'}],raw_payload={'secret':'private-payload'}),
                MediaPostMetricDaily(post_id=n.id, metric_date=date(2026,10,11), read_user_count=10),
                *[XhsPost(account_id=x.id,title=f'note-{i}',publish_date=date(2026,10,7),views=i,avg_watch_time=12) for i in range(3)],
                XhsPost(account_id=off.id,title='private-inactive',publish_date=date(2026,10,7)),
                *[XhsAccountDailyMetric(account_id=x.id,metric_date=date(2026,10,5)+timedelta(days=i),rise_fans_count=2,loss_fans_count=1,net_rise_fans_count=1,view_count=10) for i in range(7)],
                XhsAudienceSourceDaily(account_id=x.id,snapshot_date=date(2026,10,10),window_label='seven',source_type=1,title='推荐',value_pct=75),
                XhsAudienceSourceDaily(account_id=x.id,snapshot_date=date(2026,10,13),window_label='seven',source_type=1,title='推荐',value_pct=99),
                PgyNote(account_id=x.id,note_id='pgy',note_title='campaign',publish_date=date(2026,10,7),reads=200,blogger_quote=999,order_account='private-order'),
                ZhihuPost(content_type='article',title='zhihu',publish_date=date(2026,10,7),reads=70),
                WeeklyReportRun(week_start=date(2026,10,5),week_end=date(2026,10,11),status='success',html_content='A'*30000+'END',narrative='report',notification_text='summary'),
            ])
            await s.commit()
            return {'wechat':w.id,'xhs':x.id,'off':off.id,'old':p.id}
    return asyncio.run(seed())


def result(client, token, tool, **args):
    out=call(client,token,tool,args)
    assert not out.get('isError'), out
    return out['structuredContent']


def test_detail_projection_pagination_and_new_sources(client,tokens,business_data):
    token=issue(client,tokens)['token']
    ids=business_data
    accounts=result(client,token,'list_media_accounts')
    # Deactivated accounts stay listed (flagged) so details add up to query_media_metrics totals.
    assert {(r['source'],r['name'],r.get('is_active')) for r in accounts['data'] if r['source']=='xhs'}=={('xhs','xhs',True),('xhs','off',False)}
    # 蒲公英 only for accounts with it enabled or with existing notes.
    assert [r['account_id'] for r in accounts['data'] if r['source']=='pgy']==[ids['xhs']]
    assert 'private' not in json.dumps(accounts)
    args=dict(start_date='2026-10-05',end_date='2026-10-11')
    page=result(client,token,'query_media_posts',source='xhs',limit=2,**args)
    assert page['total']==4 and page['next_offset']==2
    totals=result(client,token,'query_media_metrics',source='xhs',**args)
    assert totals['data']['posts']==page['total']
    assert result(client,token,'query_media_posts',source='xhs',offset=2,limit=2,**args)['next_offset'] is None
    page=result(client,token,'query_media_posts',source='xhs',account_id=ids['xhs'],**args)
    assert page['total']==3
    for source,expected in [('pgy',200),('zhihu',70)]:
        out=result(client,token,'query_media_posts',source=source,**args)
        assert out['data'][0]['reads']==expected
        assert 'private' not in json.dumps(out) and 'blogger_quote' not in json.dumps(out)
    assert result(client,token,'query_media_posts',source='xhs',account_id=ids['off'],**args)['total']==1
    assert call(client,token,'query_media_posts',{**args,'source':'xhs','account_id':999999})['isError']
    assert call(client,token,'query_media_posts',{**args,'source':'zhihu','account_id':1})['isError']
    out=result(client,token,'query_media_posts',source='wechat',**args)
    assert len(out['data'])==1 and out['data'][0]['title']=='new article'


def test_daily_completeness_and_source_time_boundary(client,tokens,business_data):
    token=issue(client,tokens)['token']; aid=business_data['xhs']
    args=dict(account_id=aid,start_date='2026-10-05',end_date='2026-10-11')
    out=result(client,token,'query_xhs_daily_metrics',**args)
    assert out['accounts'][0]['totals']['net_rise_fans_count']==7
    assert out['accounts'][0]['totals']['view_count']==70
    assert out['accounts'][0]['totals']['home_view_count'] is None
    out=result(client,token,'query_xhs_daily_metrics',**{**args,'end_date':'2026-10-12'})
    assert not out['accounts'][0]['complete_dates']
    assert out['accounts'][0]['totals']['view_count'] is None
    out=result(client,token,'query_xhs_audience_sources',account_id=aid,as_of='2026-10-11')
    assert out['snapshot_date']=='2026-10-10' and out['data'][0]['value_pct']==75
    out=result(client,token,'query_xhs_audience_sources',account_id=aid,as_of='2026-10-09')
    assert out['data']==[]


def test_wechat_exact_boundaries_and_no_raw_payload(client,tokens,business_data,pg_sync_url):
    token=issue(client,tokens)['token']
    args=dict(start_date='2026-10-05',end_date='2026-10-11',account_id=business_data['wechat'])
    out=result(client,token,'query_wechat_period',**args)
    old=next(r for r in out['data'] if r['id']==business_data['old'])
    assert old['period_delta']['read_user_count']==25
    assert old['end_snapshot']['read_avg_time']==80
    assert old['end_snapshot']['read_sources']==[{'scene':'搜索','user_count':12}]
    assert 'private' not in json.dumps(out) and 'raw_payload' not in json.dumps(out)
    assert next(r for r in out['data'] if r['is_new_in_period'])['period_delta']['read_user_count']==10
    engine=create_engine(pg_sync_url)
    with engine.begin() as c:
        c.execute(text("UPDATE media_post_metrics_daily SET metric_date='2026-10-03' WHERE metric_date='2026-10-04'"))
    out=result(client,token,'query_wechat_period',**args)
    old=next(r for r in out['data'] if r['id']==business_data['old'])
    assert old['period_delta']['read_user_count'] is None and not old['complete']
    engine.dispose()


def test_new_tools_scope_denial_and_audit_visibility(client,tokens,business_data,pg_sync_url):
    token=issue(client,tokens,['metadata:read'])['token']
    cases={
        'list_media_accounts':{},
        'query_media_posts':{'source':'xhs','start_date':'2026-10-05','end_date':'2026-10-11'},
        'query_xhs_daily_metrics':{'account_id':business_data['xhs'],'start_date':'2026-10-05','end_date':'2026-10-11'},
        'query_xhs_audience_sources':{'account_id':business_data['xhs'],'as_of':'2026-10-11'},
        'query_wechat_period':{'account_id':business_data['wechat'],'start_date':'2026-10-05','end_date':'2026-10-11'},
        'get_weekly_report':{'full_content':True},
    }
    for name,args in cases.items():assert call(client,token,name,args)['isError']
    own=client.get('/integrations/workbuddy/queries',headers=jwt_headers(tokens))
    assert own.status_code==200 and own.headers['cache-control']=='no-store'
    assert len(own.json())==6 and all(r['outcome']=='denied' for r in own.json())
    assert all(r['duration_ms'] is not None for r in own.json())
    assert client.get('/integrations/workbuddy/queries',headers=jwt_headers(tokens,'admin')).json()==[]
    assert client.get('/integrations/workbuddy/queries',headers=jwt_headers(tokens,'viewer')).status_code==403
    assert token not in own.text


def test_full_report_and_invalid_date_audited(client,tokens,business_data):
    token=issue(client,tokens)['token']
    out=result(client,token,'get_weekly_report',full_content=True)
    assert len(out['data']['content_html'])==30000 and out['data']['next_offset']==30000
    assert result(client,token,'get_weekly_report',full_content=True,offset=30000)['data']['content_html']=='END'
    assert call(client,token,'query_media_posts',{'source':'xhs','start_date':'2026-10-11','end_date':'2026-10-05'})['isError']
    rows=client.get('/integrations/workbuddy/queries',headers=jwt_headers(tokens)).json()
    assert rows[0]['outcome']=='error'


def test_wechat_article_without_publish_date_is_included(client,tokens,business_data):
    from app import db
    from app.db.models import MediaPost, MediaPostMetricDaily
    async def seed():
        async with db.AsyncSessionLocal() as s:
            post=MediaPost(account_id=business_data['wechat'],platform='wechat_official',external_id='undated',title='undated article')
            s.add(post); await s.flush()
            s.add(MediaPostMetricDaily(post_id=post.id,metric_date=date(2026,10,11),read_user_count=5))
            await s.commit()
    asyncio.run(seed())
    token=issue(client,tokens)['token']
    out=result(client,token,'query_wechat_period',account_id=business_data['wechat'],start_date='2026-10-05',end_date='2026-10-11')
    undated=next(r for r in out['data'] if r['title']=='undated article')
    assert undated['is_new_in_period'] is False and undated['complete'] is False
