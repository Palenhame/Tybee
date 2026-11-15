from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from support_bot.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_COLUMN_PATCHES = (
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS telegram_id BIGINT",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS tg_id BIGINT",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS display_name VARCHAR(80)",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()",
    "ALTER TABLE staff ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()",
    """DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'staff' AND column_name = 'id'
        ) THEN
            UPDATE staff SET telegram_id = id WHERE telegram_id IS NULL;
        END IF;
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'staff' AND column_name = 'name'
        ) THEN
            UPDATE staff SET display_name = name WHERE display_name IS NULL;
        END IF;
        UPDATE staff SET display_name = COALESCE(NULLIF(display_name, ''), 'Сотрудник');
        UPDATE staff SET is_active = TRUE WHERE is_active IS NULL;
        UPDATE staff SET tg_id = telegram_id WHERE tg_id IS NULL;
        DELETE FROM staff a USING staff b
        WHERE a.telegram_id = b.telegram_id AND a.ctid > b.ctid;
    END
    $$""",
    """CREATE OR REPLACE FUNCTION sync_staff_ids() RETURNS trigger AS $$
    BEGIN
        IF NEW.telegram_id IS NULL THEN
            NEW.telegram_id = NEW.tg_id;
        END IF;
        IF NEW.tg_id IS NULL THEN
            NEW.tg_id = NEW.telegram_id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS sync_staff_ids ON staff",
    """CREATE TRIGGER sync_staff_ids
    BEFORE INSERT OR UPDATE ON staff
    FOR EACH ROW EXECUTE FUNCTION sync_staff_ids()""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_staff_telegram_id ON staff (telegram_id)",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'direct'",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS channel VARCHAR(16) NOT NULL DEFAULT 'telegram'",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source_conversation_id BIGINT",
    "ALTER TABLE support_requests ADD COLUMN IF NOT EXISTS source_order_id BIGINT",
    "ALTER TABLE support_messages ADD COLUMN IF NOT EXISTS source_event_id BIGINT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_support_requests_source_conversation ON support_requests (source_conversation_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_support_messages_source_event ON support_messages (source_event_id)",
)


async def init_models() -> None:
    from support_bot import models  # noqa: F401

    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                for statement in _COLUMN_PATCHES:
                    await conn.execute(text(statement))
            return
        except Exception:
            logger.warning("DB not ready (attempt %d/10), retrying in 2s...", attempt + 1)
            await asyncio.sleep(2)
    raise RuntimeError("Could not connect to database after 10 attempts")
