"""
Database Models Configuration.
"""
import time
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, Integer, Text

Base = declarative_base()

class Config(Base):
    __tablename__ = 'config'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

class PhoneStatus(Base):
    __tablename__ = 'phone_status'
    phone: Mapped[str] = mapped_column(String, primary_key=True)
    account_status: Mapped[str] = mapped_column(String, default='Unknown')
    contact_status: Mapped[str] = mapped_column(String, default='Unknown')
    spam_status: Mapped[str] = mapped_column(String, default='Unknown')
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

class Admin(Base):
    __tablename__ = 'admins'
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String, default='admin')
    added_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

class ActionLog(Base):
    __tablename__ = 'action_log'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=True)
    ts: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

class Setup(Base):
    __tablename__ = 'setup'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    proxy: Mapped[str] = mapped_column(Text, nullable=True)
    password: Mapped[str] = mapped_column(Text, nullable=True)
    setup_type: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

class Scheduler(Base):
    __tablename__ = 'scheduler'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

class WebToken(Base):
    __tablename__ = 'web_tokens'
    token: Mapped[str] = mapped_column(String, primary_key=True)
    # Telegram user the token was issued to. Without this the resulting web
    # session had no identity, so the panel could not enforce roles at all.
    # 0 means "legacy row, unattributable" and is always rejected at login.
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0)

class WebSession(Base):
    __tablename__ = 'web_sessions'
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Carried over from the token that created this session. Drives role
    # checks and audit attribution. 0 = legacy row -> rejected by require_auth.
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, nullable=True)

class TwoFactor(Base):
    __tablename__ = 'two_factor'
    phone: Mapped[str] = mapped_column(String, primary_key=True)
    password: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

