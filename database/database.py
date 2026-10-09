"""
Database facade — exports all engine, models, and crud features.
Maintains backward compatibility with all imports of `database.database as db`.
"""

from .engine import engine, SessionLocal, init_db, close_db
from .models import (
    Base, Config, PhoneStatus, Admin,
    ActionLog, Setup, Scheduler, WebToken, WebSession, TwoFactor
)
from .crud import *
