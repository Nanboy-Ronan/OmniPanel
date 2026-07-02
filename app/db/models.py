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
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    posts = relationship("XhsPost", back_populates="account", cascade="all, delete-orphan")


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
