"""Business tables (docs/design/stage4-app-design.md §3).

Money is integer fen, quantities integer shares, timestamps aware UTC,
trading days are dates.  Events, fills, approvals and the audit log are
append-only: corrections are new rows (reversals), never updates or deletes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime, utc_now

# -- identity and access (API in P2) ---------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    invitation_id: Mapped[int | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Invitation(Base):
    """An invitation code created by an administrator.  Users register with it
    and get its roles.  Only the code's hash is stored; ``hint`` holds its last
    characters, so the code can be recognised in the list."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(16))
    roles: Mapped[list[str]] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    group_name: Mapped[str] = mapped_column(String(32))


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code", ondelete="CASCADE"),
                                                 primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(64))  # username, or "system" / "cli"
    action: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(96))
    before: Mapped[Any | None] = mapped_column(JSON)
    after: Mapped[Any | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))


# -- accounts and ledger ------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("mode IN ('manual', 'paper')", name="ck_accounts_mode"),)

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    strategy_config: Mapped[str] = mapped_column(String(255))  # repo-relative path
    initial_cash_fen: Mapped[int] = mapped_column(BigInteger)
    start_date: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    holdings_confirmed_date: Mapped[date | None] = mapped_column(Date)  # manual accounts, gate G4
    paper_through: Mapped[date | None] = mapped_column(Date)  # paper accounts: last simulated session
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PositionEvent(Base):
    """Replayable ledger event; the account state is the replay of these rows
    ordered by (trade_date, seq)."""

    __tablename__ = "position_events"
    __table_args__ = (Index("ix_position_events_account_date", "account_id", "trade_date", "seq"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    trade_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(24))  # deposit|fill|corporate_action|delisting|adjustment|cash|reversal
    symbol: Mapped[str | None] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)  # fields of the ledger event
    ref_id: Mapped[str | None] = mapped_column(String(128))  # fill_id, reversed event_id, ...
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash_fen: Mapped[int] = mapped_column(BigInteger)
    market_value_fen: Mapped[int] = mapped_column(BigInteger)
    nav_fen: Mapped[int] = mapped_column(BigInteger)
    positions: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    qty: Mapped[int] = mapped_column(BigInteger)
    mark_fen: Mapped[int] = mapped_column(BigInteger)
    value_fen: Mapped[int] = mapped_column(BigInteger)
    cost_fen: Mapped[int] = mapped_column(BigInteger)


class ImportBatch(Base):
    __tablename__ = "imports"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    kind: Mapped[str] = mapped_column(String(16))  # holdings | fills
    filename: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # previewed | committed | discarded
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


# -- decisions ------------------------------------------------------------------------


class DecisionRun(Base):
    __tablename__ = "decision_runs"
    __table_args__ = (Index("ix_decision_runs_account_date", "account_id", "trade_date"),)

    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    trade_date: Mapped[date] = mapped_column(Date)
    next_session: Mapped[date | None] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))  # rebalance | monitor | forced
    status: Mapped[str] = mapped_column(String(16))  # complete | blocked | failed | superseded
    gates: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    ingest_run_id: Mapped[str | None] = mapped_column(String(64))
    data_version: Mapped[str | None] = mapped_column(String(64))
    code_version: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    strategy_version: Mapped[str | None] = mapped_column(String(32))
    nav_fen: Mapped[int | None] = mapped_column(BigInteger)
    cash_fen: Mapped[int | None] = mapped_column(BigInteger)
    positions: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    report_dir: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)  # forced-run reason or failure message
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class TargetPosition(Base):
    __tablename__ = "target_positions"
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_target_positions_run_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("decision_runs.run_id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(16))
    target_weight: Mapped[float] = mapped_column(Float)
    target_qty: Mapped[int | None] = mapped_column(BigInteger)
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON)


class OrderIntent(Base):
    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="ck_order_intents_side"),
        Index("ix_order_intents_account_status", "account_id", "status"),
    )

    intent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("decision_runs.run_id", ondelete="CASCADE"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    trade_date: Mapped[date] = mapped_column(Date)  # decision session T
    execute_on: Mapped[date] = mapped_column(Date)  # T+1
    seq: Mapped[int] = mapped_column(Integer)  # sells first, then buys by rank (size_orders order)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(BigInteger)  # current quantity (after modifications)
    proposed_qty: Mapped[int] = mapped_column(BigInteger)
    ref_price_fen: Mapped[int] = mapped_column(BigInteger)
    limit_up_fen: Mapped[int | None] = mapped_column(BigInteger)
    limit_down_fen: Mapped[int | None] = mapped_column(BigInteger)
    est_notional_fen: Mapped[int] = mapped_column(BigInteger)
    est_fees_fen: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32))  # exit | rebalance
    rank: Mapped[int | None] = mapped_column(Integer)
    risk: Mapped[str] = mapped_column(String(8))  # worst check: pass | warn | reject
    status: Mapped[str] = mapped_column(String(24))
    valid_until: Mapped[datetime] = mapped_column(UTCDateTime)
    filled_qty: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    execution: Mapped[str | None] = mapped_column(String(16))  # None/partial (open) | filled | unfilled | partial_closed
    execution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RiskCheck(Base):
    __tablename__ = "risk_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("decision_runs.run_id", ondelete="CASCADE"), index=True)
    intent_id: Mapped[str | None] = mapped_column(ForeignKey("order_intents.intent_id", ondelete="CASCADE"))
    rule_id: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(8))  # pass | warn | reject
    actual: Mapped[str | None] = mapped_column(String(64))
    limit_value: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("order_intents.intent_id"), index=True)
    action: Mapped[str] = mapped_column(String(16))  # approve|modify|reject|override|expire|supersede
    qty_before: Mapped[int | None] = mapped_column(BigInteger)
    qty_after: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (CheckConstraint("side IN ('buy', 'sell')", name="ck_fills_side"),)

    fill_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    intent_id: Mapped[str | None] = mapped_column(ForeignKey("order_intents.intent_id"))
    trade_date: Mapped[date] = mapped_column(Date)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(BigInteger)
    price_fen: Mapped[int] = mapped_column(BigInteger)
    commission_fen: Mapped[int] = mapped_column(BigInteger)
    stamp_duty_fen: Mapped[int] = mapped_column(BigInteger)
    transfer_fee_fen: Mapped[int] = mapped_column(BigInteger)
    fees_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))  # manual | import | paper
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("imports.batch_id"))
    reversed_by: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


# -- notifications ----------------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    level: Mapped[str] = mapped_column(String(8))  # info | warning | critical
    category: Mapped[str] = mapped_column(String(16))  # data | decision | risk | account | system
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(80))
    trade_date: Mapped[date | None] = mapped_column(Date)
    account_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(16))
    action_hint: Mapped[str | None] = mapped_column(Text)


class EventRead(Base):
    __tablename__ = "event_reads"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class ChartDrawing(Base):
    """A user's drawings (KLineChart overlays: name, points by timestamp and
    value, styles) on one security's chart."""

    __tablename__ = "chart_drawings"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_chart_drawings_user_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(16))
    overlays: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class EventPush(Base):
    """Delivery of an event to an external channel (app/notify.py): one row per
    event and channel, so a failing channel does not resend on the others."""

    __tablename__ = "event_pushes"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(8))  # sending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
