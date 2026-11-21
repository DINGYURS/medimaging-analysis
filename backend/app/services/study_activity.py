from datetime import datetime

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Study


async def record_study_operation(
    db: AsyncSession,
    study_id: int,
    doctor_id: int,
    occurred_at: datetime,
) -> None:
    """Advance the owner's timestamp within the caller's transaction."""
    await db.execute(
        update(Study)
        .where(
            Study.id == study_id,
            Study.doctor_id == doctor_id,
            or_(Study.last_operation_at.is_(None), Study.last_operation_at < occurred_at),
        )
        .values(last_operation_at=occurred_at)
        .execution_options(synchronize_session=False)
    )
