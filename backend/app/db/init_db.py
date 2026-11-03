import asyncio
from sqlalchemy import select
from app.db.session import engine, AsyncSessionLocal
from app.db.base import Base
from app.db.models import User
from app.core.security import get_password_hash

async def init_database():
    async with engine.begin() as conn:
        print("[*] Creating database tables if not exist...")
        await conn.run_sync(Base.metadata.create_all)
        print("[+] Tables created successfully.")

    async with AsyncSessionLocal() as session:
        # Check super admin
        result = await session.execute(select(User).where(User.username == "admin"))
        admin_user = result.scalar_one_or_none()
        if not admin_user:
            print("[*] Initializing default super admin: admin / admin123")
            admin_user = User(
                username="admin",
                hashed_password=get_password_hash("admin123"),
                real_name="系统管理员",
                role="ADMIN",
                department="信息中心",
                is_active=True
            )
            session.add(admin_user)
        
        # Check default demo doctor
        result = await session.execute(select(User).where(User.username == "doctor"))
        doctor_user = result.scalar_one_or_none()
        if not doctor_user:
            print("[*] Initializing default doctor: doctor / doctor123")
            doctor_user = User(
                username="doctor",
                hashed_password=get_password_hash("doctor123"),
                real_name="张医生",
                role="DOCTOR",
                department="胸部放射科",
                is_active=True
            )
            session.add(doctor_user)
            
        await session.commit()
        print("[+] Initial users initialized.")

if __name__ == "__main__":
    asyncio.run(init_database())
