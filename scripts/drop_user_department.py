"""Remove the retired user field after backing it up; run with --apply."""
import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
from app.db.session import engine

def digest(rows):
    return hashlib.sha256(json.dumps([dict(row) for row in rows], ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()

async def migrate():
    try:
        async with engine.connect() as conn:
            columns = list((await conn.execute(text("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users' ORDER BY ORDINAL_POSITION"))).scalars())
            if "department" not in columns:
                print("Already applied: retired column is absent.")
                return
            dependencies = []
            for query in [
                "SELECT TRIGGER_NAME AS name, ACTION_STATEMENT AS body FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()",
                "SELECT TABLE_NAME AS name, VIEW_DEFINITION AS body FROM information_schema.VIEWS WHERE TABLE_SCHEMA=DATABASE()",
                "SELECT ROUTINE_NAME AS name, ROUTINE_DEFINITION AS body FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE()",
                "SELECT EVENT_NAME AS name, EVENT_DEFINITION AS body FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()",
            ]:
                for row in (await conn.execute(text(query))).mappings():
                    if "department" in str(row["body"]).lower():
                        dependencies.append(row["name"])
            if dependencies:
                raise RuntimeError(f"Database dependencies need review: {dependencies}")
            ddl = (await conn.execute(text("SHOW CREATE TABLE users"))).one()[1]
            values = [dict(row) for row in (await conn.execute(text("SELECT id, username, department FROM users ORDER BY id"))).mappings()]
            remaining = [name for name in columns if name != "department"]
            projection = ", ".join(chr(96) + name.replace(chr(96), chr(96)*2) + chr(96) for name in remaining)
            query = text("SELECT " + projection + " FROM users ORDER BY id")
            before_rows = (await conn.execute(query)).mappings().all()
            before = digest(before_rows)
            backup = Path("/data/medimaging-analysis/.deployment") / ("remove-user-department-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            backup.mkdir(parents=True, mode=0o700)
            (backup / "users-schema.sql").write_text(ddl + ";\n")
            (backup / "user-departments.json").write_text(json.dumps(values, ensure_ascii=False, indent=2))
            (backup / "verification.json").write_text(json.dumps(dict(rows=len(before_rows), remaining_columns=remaining, sha256=before), indent=2))
            for path in backup.iterdir():
                os.chmod(path, 0o600)
            await conn.commit()
            await conn.execute(text("SET SESSION lock_wait_timeout = 10"))
            await conn.execute(text("ALTER TABLE users DROP COLUMN department"))
            await conn.commit()
            after_rows = (await conn.execute(query)).mappings().all()
            assert digest(after_rows) == before, "Remaining user data changed; inspect backup"
            assert len(after_rows) == len(before_rows)
            print(f"Migration applied; {len(after_rows)} users retained; other fields match.")
            print(f"Backup: {backup}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(migrate())
