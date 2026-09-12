# rap/app/db/models.py

from datetime import date
import enum
import uuid
from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Date,
    Numeric,
    ForeignKey,
    UniqueConstraint,
    Enum,
    DateTime,
    Text,
    JSON,
    Boolean,
    func,
)
from sqlalchemy.orm import relationship
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy.dialects.postgresql import UUID

from . import Base  # now available
from .raw_headers import YOUZAN_RAW_HEADERS, JD_RAW_HEADERS, TMALL_RAW_HEADERS  # noqa: F401 — re-exported for ETL

class User(SQLAlchemyBaseUserTableUUID, Base):
    """
    FastAPI-Users’ built-in user model. It expects Base to come from .db/__init__.py
    """

    __tablename__ = "user"  # or "users" depending on your config

    role = Column(
        Enum("viewer", "analyst", "admin", name="role"),
        nullable=False,
        server_default="viewer",
    )
    wecom_userid = Column(String(128), nullable=True, unique=True, index=True)
    display_name = Column(String(200), nullable=True)
    wecom_alert_enabled = Column(Boolean, nullable=False, server_default="false")

    # The mixin SQLAlchemyBaseUserTableUUID supplies columns:
    #   id UUID primary key, email, hashed_password, is_active, is_superuser, is_verified
    # Adding "role" allows differentiating between viewer, analyst and admin users.


class Customer(Base):
    __tablename__ = "customers"
    customer_key = Column(String(500), primary_key=True)
    platform = Column(String(16), nullable=False, server_default="youzan")
    first_order_date = Column(Date, nullable=False)
    orders = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), nullable=False)
    order_date = Column(Date, nullable=False)
    customer_key = Column(String(500), ForeignKey("customers.customer_key", ondelete="CASCADE"), nullable=False)
    platform = Column(String(16), nullable=False, server_default="youzan")
    sku = Column(String(200), nullable=True)
    quantity = Column(Integer, nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    receiver = Column(String(200), nullable=True)
    receiver_phone = Column(String(64), nullable=True)
    province = Column(String(64), nullable=True)
    area = Column(String(64), nullable=True)
    full_address = Column(Text, nullable=True)
    buyer_nick = Column(String(200), nullable=True)
    coupon_name = Column(String(200), nullable=True)
    distributor = Column(String(200), nullable=True)

    customer = relationship("Customer", back_populates="orders")

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "order_date",
            "customer_key",
            "sku",
            "quantity",
            "price",
            name="uq_orders_all_fields",
        ),
        Index("ix_orders_order_date", "order_date"),
        Index("ix_orders_platform", "platform"),
        Index("ix_orders_customer_key", "customer_key"),
        Index("ix_orders_date_platform", "order_date", "platform"),
    )


class UploadBatch(Base):
    """One user-uploaded file and its ingest summary."""

    __tablename__ = "upload_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    platform = Column(String(16), nullable=False)
    uploaded_by = Column(UUID, nullable=True)
    uploaded_at = Column(DateTime, nullable=False, server_default=func.now())
    file_sha256 = Column(String(64), nullable=False)
    row_count = Column(Integer, nullable=False, server_default="0")
    inserted_orders = Column(Integer, nullable=False, server_default="0")
    raw_rows_inserted = Column(Integer, nullable=False, server_default="0")
    duplicate_rows = Column(Integer, nullable=False, server_default="0")
    invalid_rows = Column(Integer, nullable=False, server_default="0")
    status = Column(String(32), nullable=False, server_default="completed")
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_upload_batches_platform", "platform"),
        Index("ix_upload_batches_uploaded_at", "uploaded_at"),
    )


class UploadRejectedRow(Base):
    """Raw upload row that could not become a valid platform/order row."""

    __tablename__ = "upload_rejected_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(16), nullable=False)
    source_row_number = Column(Integer, nullable=False)
    raw_payload = Column(JSON, nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_upload_rejected_rows_batch_id", "batch_id"),
        Index("ix_upload_rejected_rows_platform", "platform"),
    )


class MediaAccount(Base):
    """Owned social media account connected to the analytics platform."""

    __tablename__ = "media_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False)
    name = Column(String(200), nullable=False)
    app_id = Column(String(128), nullable=True)
    app_secret = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    posts = relationship("MediaPost", back_populates="account", cascade="all, delete-orphan")
    sync_runs = relationship("MediaSyncRun", back_populates="account", cascade="all, delete-orphan")
    article_traffic = relationship("MediaArticleTraffic", back_populates="account", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("platform", "app_id", name="uq_media_accounts_platform_app_id"),
        Index("ix_media_accounts_platform", "platform"),
    )


class MediaPost(Base):
    """One published article or post from an owned media account."""

    __tablename__ = "media_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(32), nullable=False)
    external_id = Column(String(256), nullable=False)
    title = Column(String(500), nullable=False)
    url = Column(Text, nullable=True)
    publish_date = Column(Date, nullable=True)
    author = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    account = relationship("MediaAccount", back_populates="posts")
    metrics = relationship("MediaPostMetricDaily", back_populates="post", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_media_posts_account_external_id"),
        Index("ix_media_posts_account_id", "account_id"),
        Index("ix_media_posts_publish_date", "publish_date"),
        Index("ix_media_posts_platform", "platform"),
    )


class MediaPostMetricDaily(Base):
    """Daily metrics snapshot for a media post."""

    __tablename__ = "media_post_metrics_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("media_posts.id", ondelete="CASCADE"), nullable=False)
    metric_date = Column(Date, nullable=False)
    read_count = Column(Integer, nullable=False, server_default="0")
    read_user_count = Column(Integer, nullable=False, server_default="0")
    share_count = Column(Integer, nullable=False, server_default="0")
    share_user_count = Column(Integer, nullable=False, server_default="0")
    add_to_fav_count = Column(Integer, nullable=False, server_default="0")
    target_user = Column(Integer, nullable=True)
    int_page_read_count = Column(Integer, nullable=True)
    ori_page_read_count = Column(Integer, nullable=True)
    # Fields from getarticletotaldetail (new API, richer than legacy getarticletotal)
    publish_type = Column(Integer, nullable=True)
    like_user = Column(Integer, nullable=True)
    comment_count = Column(Integer, nullable=True)
    collection_user = Column(Integer, nullable=True)
    read_avg_time = Column(Float, nullable=True)
    read_user_source = Column(JSON, nullable=True)
    zaikan_user = Column(Integer, nullable=True)
    read_subscribe_user = Column(Integer, nullable=True)
    read_delivery_rate = Column(Float, nullable=True)
    praise_money = Column(Integer, nullable=True)
    read_jump_position = Column(JSON, nullable=True)
    read_finish_rate = Column(Float, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    post = relationship("MediaPost", back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("post_id", "metric_date", name="uq_media_post_metrics_post_date"),
        Index("ix_media_post_metrics_metric_date", "metric_date"),
        Index("ix_media_post_metrics_post_id", "post_id"),
    )


class MediaArticleTraffic(Base):
    """Cumulative article traffic metrics from a manually-uploaded WeChat xlsx.

    One row per article per account (upserted on re-upload).
    Unlike MediaPostMetricDaily (per-day API snapshots), this table stores the
    cumulative totals that the WeChat backend exports give you.
    """

    __tablename__ = "media_article_traffic"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="CASCADE"), nullable=False)
    # sha256(f"{account_id}:{title}")[:32] — stable dedup key across re-uploads
    external_id = Column(String(64), nullable=False)
    title = Column(String(500), nullable=False)
    publish_date = Column(Date, nullable=True)
    read_user_count = Column(Integer, nullable=False, server_default="0")
    read_count = Column(Integer, nullable=False, server_default="0")
    like_user = Column(Integer, nullable=False, server_default="0")
    share_user_count = Column(Integer, nullable=False, server_default="0")
    comment_count = Column(Integer, nullable=False, server_default="0")
    collection_user = Column(Integer, nullable=False, server_default="0")
    read_avg_time = Column(Float, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    account = relationship("MediaAccount", back_populates="article_traffic")

    __table_args__ = (
        UniqueConstraint(
            "account_id", "external_id",
            name="uq_media_article_traffic_account_external",
        ),
        Index("ix_media_article_traffic_account_id", "account_id"),
        Index("ix_media_article_traffic_publish_date", "publish_date"),
    )


class MediaSyncRun(Base):
    """One media data sync attempt and its result summary."""

    __tablename__ = "media_sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("media_accounts.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    posts_upserted = Column(Integer, nullable=False, server_default="0")
    metrics_upserted = Column(Integer, nullable=False, server_default="0")
    rejected = Column(Integer, nullable=False, server_default="0")
    error_message = Column(Text, nullable=True)
    # Manual xlsx upload fields
    source = Column(String(16), nullable=False, server_default="api")
    filename = Column(String(500), nullable=True)

    account = relationship("MediaAccount", back_populates="sync_runs")

    __table_args__ = (
        Index("ix_media_sync_runs_account_id", "account_id"),
        Index("ix_media_sync_runs_started_at", "started_at"),
        Index("ix_media_sync_runs_status", "status"),
    )


class CollectorRun(Base):
    """One creator-portal export-agent run and its result summary.

    account_id/content_type have no FK: a run record for an account that was
    later deleted (or a platform with no account concept) must still survive.
    """

    __tablename__ = "collector_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(16), nullable=False)          # "xhs" | "zhihu"
    account_id = Column(Integer, nullable=True)             # xhs_accounts.id; NULL for zhihu
    content_type = Column(String(16), nullable=True)        # zhihu: "article" | "qa"
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    # running | success | session_expired | download_failed | upload_failed | error
    status = Column(String(32), nullable=False)
    rows_upserted = Column(Integer, nullable=False, server_default="0")
    filename = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    triggered_by = Column(String(16), nullable=False, server_default="schedule")  # schedule|manual

    __table_args__ = (
        Index("ix_collector_runs_started_at", "started_at"),
        Index("ix_collector_runs_status", "status"),
        Index("ix_collector_runs_platform", "platform"),
    )


def _raw_system_columns():
    return {
        "id": Column(Integer, primary_key=True, autoincrement=True),
        "batch_id": Column(Integer, ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False),
        "source_row_number": Column(Integer, nullable=False),
        "order_id": Column(String(128), nullable=True),
        "normalized_order_id": Column(String(128), nullable=True),
        "ingest_status": Column(String(32), nullable=False),
        "ingest_message": Column(Text, nullable=True),
        "row_hash": Column(String(64), nullable=False),
        "created_at": Column(DateTime, nullable=False, server_default=func.now()),
    }


def _platform_order_model(name: str, table_name: str, headers: list[str]):
    attrs = {
        "__tablename__": table_name,
        **_raw_system_columns(),
        "__table_args__": (
            UniqueConstraint("row_hash", name=f"uq_{table_name}_row_hash"),
            Index(f"ix_{table_name}_batch_id", "batch_id"),
            Index(f"ix_{table_name}_order_id", "order_id"),
            Index(f"ix_{table_name}_normalized_order_id", "normalized_order_id"),
        ),
    }
    for idx, header in enumerate(headers):
        attrs[f"raw_col_{idx}"] = Column(header, Text, nullable=True)
    return type(name, (Base,), attrs)


YouzanOrder = _platform_order_model("YouzanOrder", "youzan_orders", YOUZAN_RAW_HEADERS)
JdOrder = _platform_order_model("JdOrder", "jd_orders", JD_RAW_HEADERS)
TmallOrder = _platform_order_model("TmallOrder", "tmall_orders", TMALL_RAW_HEADERS)


class OperationLog(Base):
    """Record of user actions."""

    __tablename__ = "operation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(UUID, nullable=False)
    action = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, server_default=func.now())
    detail = Column(Text, nullable=True)


class XhsAccount(Base):
    """A Xiaohongshu (小红书) professional account managed by this platform.

    Unlike WeChat accounts there are no API credentials — data is imported
    manually via xlsx exports.
    """

    __tablename__ = "xhs_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    account_type = Column(String(20), nullable=False, server_default="company")
    is_active = Column(Boolean, nullable=False, server_default="true")
    pgy_enabled = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    posts = relationship("XhsPost", back_populates="account", cascade="all, delete-orphan")
    pgy_notes = relationship('PgyNote', back_populates='account', cascade='all, delete-orphan')


class XhsPost(Base):
    """One Xiaohongshu post with its traffic metrics.

    Dedup key: (account_id, title, publish_date).  Numeric metrics are
    overwritten on each upsert; posts absent from the current upload are kept.
    """

    __tablename__ = "xhs_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("xhs_accounts.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    publish_date = Column(Date, nullable=False)
    genre = Column(String(32), nullable=True)
    # ── traffic metrics (overwritten on each upsert) ──────────────────────
    impressions = Column(Integer, nullable=True)
    views = Column(Integer, nullable=True)
    cover_click_rate = Column(Float, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    collects = Column(Integer, nullable=True)
    new_followers = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    avg_watch_time = Column(Float, nullable=True)
    danmu = Column(Integer, nullable=True)
    # ── housekeeping ──────────────────────────────────────────────────────
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    account = relationship("XhsAccount", back_populates="posts")

    __table_args__ = (
        UniqueConstraint("account_id", "title", "publish_date", name="uq_xhs_posts_account_title_date"),
        Index("ix_xhs_posts_account_id", "account_id"),
        Index("ix_xhs_posts_publish_date", "publish_date"),
    )


class XhsAccountDailyMetric(Base):
    """One account-level daily snapshot from XHS's "数据概览" page
    (`/statistics/account/v2`), collected via collect_xhs_overview().

    Unlike XhsPost (overwritten, no history), this is a true daily
    time series: each collection run re-submits the last 30 days from the
    platform's own "thirty"-window daily lists (verified to be genuine
    per-day values, not a running total — see collect_xhs_overview), and
    each day is upserted independently. A single missed collection day
    self-heals the next time this runs, as long as the gap is under 30 days.

    view_time_total_seconds / avg_view_time_seconds are seconds;
    cover_click_rate / video_full_view_rate are percentages (0-100).
    """

    __tablename__ = "xhs_account_daily_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("xhs_accounts.id", ondelete="CASCADE"), nullable=False)
    metric_date = Column(Date, nullable=False)
    rise_fans_count = Column(Integer, nullable=True)
    loss_fans_count = Column(Integer, nullable=True)
    net_rise_fans_count = Column(Integer, nullable=True)
    view_count = Column(Integer, nullable=True)
    view_time_total_seconds = Column(Integer, nullable=True)
    avg_view_time_seconds = Column(Float, nullable=True)
    home_view_count = Column(Integer, nullable=True)
    like_count = Column(Integer, nullable=True)
    collect_count = Column(Integer, nullable=True)
    comment_count = Column(Integer, nullable=True)
    share_count = Column(Integer, nullable=True)
    danmaku_count = Column(Integer, nullable=True)
    cover_click_rate = Column(Float, nullable=True)
    video_full_view_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    account = relationship("XhsAccount")

    __table_args__ = (
        UniqueConstraint("account_id", "metric_date", name="uq_xhs_account_daily_metrics_account_date"),
        Index("ix_xhs_account_daily_metrics_account_id", "account_id"),
        Index("ix_xhs_account_daily_metrics_metric_date", "metric_date"),
    )


class XhsAudienceSourceDaily(Base):
    """One day's snapshot of XHS's 观众来源/涨粉来源 channel breakdown
    (`audience/source/account`), for both the 7-day and 30-day windows the
    platform itself computes. Unlike XhsAccountDailyMetric's fields, the
    platform does not expose a daily list for this — each entry here is a
    percentage share (0-100, summing to ~100 within one account/window/
    snapshot_date) "as of" the day it was collected, not a per-day delta.
    """

    __tablename__ = "xhs_audience_source_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("xhs_accounts.id", ondelete="CASCADE"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    # Named window_label, not window — `window` is a reserved word in
    # Postgres (window functions) and fails at DDL time even quoted-free in
    # most contexts; caught live running this migration locally.
    window_label = Column(String(8), nullable=False)  # "seven" | "thirty"
    source_type = Column(Integer, nullable=False)
    title = Column(String(64), nullable=True)
    value_pct = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    account = relationship("XhsAccount")

    __table_args__ = (
        UniqueConstraint(
            "account_id", "snapshot_date", "window_label", "source_type",
            name="uq_xhs_audience_source_daily_account_date_window_source",
        ),
        Index("ix_xhs_audience_source_daily_account_id", "account_id"),
    )


class ZhihuPost(Base):
    """One Zhihu post (article or Q&A) with its traffic metrics.

    Dedup key: (content_type, title, publish_date).  Numeric metrics are
    overwritten on each upsert; posts absent from the current upload are kept.
    """

    __tablename__ = "zhihu_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_type = Column(String(10), nullable=False)   # "article" | "qa"
    title = Column(String(500), nullable=False)
    publish_date = Column(Date, nullable=False)
    url = Column(String(500), nullable=True)
    # ── traffic metrics (overwritten on each upsert) ──────────────────────
    reads = Column(Integer, nullable=True)
    plays = Column(Integer, nullable=True)      # QA only (播放)
    likes = Column(Integer, nullable=True)
    favorites = Column(Integer, nullable=True)  # 喜欢
    comments = Column(Integer, nullable=True)
    collects = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    # ── housekeeping ──────────────────────────────────────────────────────
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("content_type", "title", "publish_date",
                         name="uq_zhihu_posts_type_title_date"),
        Index("ix_zhihu_posts_content_type", "content_type"),
        Index("ix_zhihu_posts_publish_date", "publish_date"),
    )


class WxChannelsAccount(Base):
    """A WeChat Channels (视频号) account managed by this platform.

    Like XhsAccount there is no public analytics API — data comes from the
    视频号助手 (channels.weixin.qq.com/platform) creator backend, either via
    the automated collector (QR-scan session) or a manual xlsx/csv upload.
    """

    __tablename__ = "wx_channels_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    posts = relationship("WxChannelsPost", back_populates="account", cascade="all, delete-orphan")


class WxChannelsPost(Base):
    """One WeChat Channels (视频号) video with its traffic metrics.

    Schema verified live 2026-08-13 against a real 视频号 account, via
    视频号助手 → 数据中心 → 视频数据 → 单篇视频 tab → 下载表格 (real button
    label; NOT "导出" — see app/collector/channels.py). Sample export kept
    at data/channels_example.csv (mirrors data/pgy_example.xlsx's role).

    Dedup key: video_id, the real 视频ID column in the export (e.g.
    "export/UzFfBgAAxOOgWDk7Rwy...") — a stable ID, unlike XhsPost's
    (account_id, title, publish_date) composite. No synthetic hash needed;
    this is the same shape as PgyNote.note_id. Numeric metrics are
    overwritten on each upsert; posts absent from the current upload are kept.

    The export has no 曝光/收藏 columns (unlike XHS/PGY) — 视频号 exposes a
    different metric vocabulary, including two distinct "like" signals
    (推荐/likes here vs 喜欢/likes_thumb) and a set of WeCom-integration
    engagement actions (设为铃声/状态/朋友圈封面, 企微链接点击, 添加到通讯录)
    that have no equivalent on the other collected platforms.
    """

    __tablename__ = "wx_channels_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("wx_channels_accounts.id", ondelete="CASCADE"), nullable=False)
    video_id = Column(String(255), nullable=False)      # 视频ID
    title = Column(Text, nullable=False)                # 视频描述 (can be long, multi-line w/ hashtags)
    publish_date = Column(Date, nullable=True)           # 发布时间
    # ── traffic metrics (overwritten on each upsert; verified column set) ──
    plays = Column(Integer, nullable=True)                     # 播放量
    recommends = Column(Integer, nullable=True)                # 推荐 (♡ icon in the UI)
    likes_thumb = Column(Integer, nullable=True)                # 喜欢 (👍 icon in the UI — distinct from 推荐)
    comments = Column(Integer, nullable=True)                   # 评论量
    shares = Column(Integer, nullable=True)                     # 分享量
    new_fans = Column(Integer, nullable=True)                   # 关注量
    forwards_chat_moments = Column(Integer, nullable=True)      # 转发聊天和朋友圈
    set_as_ringtone = Column(Integer, nullable=True)            # 设为铃声
    set_as_status = Column(Integer, nullable=True)              # 设为状态
    set_as_moments_cover = Column(Integer, nullable=True)       # 设为朋友圈封面
    wecom_link_clicks = Column(Integer, nullable=True)          # 企微链接点击次数
    wecom_link_click_users = Column(Integer, nullable=True)     # 企微链接点击人数
    added_to_contacts = Column(Integer, nullable=True)          # 添加到通讯录次数
    added_to_contacts_users = Column(Integer, nullable=True)    # 添加到通讯录人数
    avg_watch_duration = Column(Float, nullable=True)           # 平均播放时长（秒，导出为"14.73秒"字符串）
    completion_rate = Column(Float, nullable=True)              # 完播率（导出为"4.41%"，存为 0.0441）
    raw_payload = Column(JSON, nullable=True)
    # ── housekeeping ──────────────────────────────────────────────────────
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    account = relationship("WxChannelsAccount", back_populates="posts")

    __table_args__ = (
        UniqueConstraint("account_id", "video_id", name="uq_wx_channels_posts_account_video_id"),
        Index("ix_wx_channels_posts_account_id", "account_id"),
        Index("ix_wx_channels_posts_publish_date", "publish_date"),
    )


class SavedQuery(Base):
    """A saved analysis filter set created by a user."""

    __tablename__ = "saved_query"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    filters_json = Column(JSON, nullable=False, server_default="{}")
    is_shared = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_saved_query_user_id", "user_id"),
        Index("ix_saved_query_shared", "is_shared"),
    )


class PgyNote(Base):
    __tablename__ = "pgy_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('xhs_accounts.id', ondelete='CASCADE'), nullable=False)
    
    # Blogger fields
    blogger_nickname = Column(String(200), nullable=True)
    blogger_url = Column(String(500), nullable=True)
    blogger_fans = Column(Integer, nullable=True)
    blogger_health = Column(String(50), nullable=True)
    
    # Note fields
    note_title = Column(String(500), nullable=True)
    note_url = Column(String(500), nullable=True)
    note_type = Column(String(32), nullable=True)
    publish_date = Column(Date, nullable=True)
    note_source = Column(String(50), nullable=True)
    note_id = Column(String(100), nullable=False)
    content_tag = Column(String(100), nullable=True)
    
    # Order fields
    order_id = Column(String(100), nullable=True)
    cooperation_name = Column(String(500), nullable=True)
    report_brand = Column(String(200), nullable=True)
    order_account = Column(String(200), nullable=True)
    blogger_quote = Column(Float, nullable=True)
    service_fee = Column(Float, nullable=True)
    is_premium_mode = Column(String(10), nullable=True)
    
    # SPU
    spu_name = Column(String(200), nullable=True)
    
    # Traffic
    impressions = Column(Integer, nullable=True)
    reads = Column(Integer, nullable=True)
    read_uv = Column(Integer, nullable=True)
    play_rate_5s = Column(String(20), nullable=True)
    read_rate_3s = Column(String(20), nullable=True)
    video_duration = Column(Float, nullable=True)
    avg_view_duration = Column(Float, nullable=True)
    video_completion_rate = Column(String(20), nullable=True)
    
    # Engagement
    interactions = Column(Integer, nullable=True)
    interaction_rate = Column(String(20), nullable=True)
    likes = Column(Integer, nullable=True)
    collects = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    follows = Column(Integer, nullable=True)
    
    # Organic/paid
    organic_impressions = Column(Integer, nullable=True)
    organic_reads = Column(Integer, nullable=True)
    paid_impressions = Column(Integer, nullable=True)
    paid_reads = Column(Integer, nullable=True)
    boosted_impressions = Column(Integer, nullable=True)
    boosted_reads = Column(Integer, nullable=True)
    
    # Cost
    cost_per_read = Column(Float, nullable=True)
    cost_per_interaction = Column(Float, nullable=True)
    
    # Audience
    fan_ratio = Column(String(20), nullable=True)
    female_ratio = Column(String(20), nullable=True)
    male_ratio = Column(String(20), nullable=True)
    
    # JSON blobs
    audience_json = Column(Text, nullable=True)
    component_json = Column(Text, nullable=True)
    
    # Timestamps
    data_date = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    account = relationship('XhsAccount', back_populates='pgy_notes')

    __table_args__ = (
        UniqueConstraint('account_id', 'note_id', name='uq_pgy_notes_account_note_id'),
        Index('ix_pgy_notes_account_id', 'account_id'),
        Index('ix_pgy_notes_publish_date', 'publish_date'),
    )


class WeeklyReportRun(Base):
    """One generated 公众号+小红书周报 (weekly media report) and its content.

    One row per ISO week (Monday-Sunday), upserted — a retry after a
    transient failure (e.g. WeChat API hiccup) overwrites the same row
    rather than accumulating duplicates. html_content is NULL when
    status='error' and rendering never got far enough to produce one.
    """

    __tablename__ = "weekly_report_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_start = Column(Date, nullable=False, unique=True)
    week_end = Column(Date, nullable=False)
    generated_at = Column(DateTime, nullable=False, server_default=func.now())
    status = Column(String(32), nullable=False)  # success | partial | error
    html_content = Column(Text, nullable=True)
    narrative = Column(Text, nullable=True)
    wecom_sent = Column(Boolean, nullable=False, server_default="false")
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_weekly_report_runs_week_start", "week_start"),
    )
