from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db, require_admin
from app.db.models import User
from app.core.security import get_password_hash
from app.schemas.user import UserOut, UserCreate, PasswordReset

router = APIRouter()

@router.get("/users", response_model=List[UserOut])
async def list_users(
    role: Optional[str] = Query(None, description="按角色筛选 (ADMIN / DOCTOR)"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    query = select(User).order_by(User.id.asc())
    if role:
        query = query.where(User.role == role)
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/users", response_model=UserOut)
async def create_doctor(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    # Check if username exists
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"用户名 '{payload.username}' 已存在"
        )
    
    new_user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        real_name=payload.real_name,
        role=payload.role or "DOCTOR",
        department=payload.department or "放射科",
        is_active=payload.is_active if payload.is_active is not None else True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.put("/users/{user_id}/status", response_model=UserOut)
async def toggle_user_status(
    user_id: int,
    is_active: bool = Query(..., description="启用(true)/禁用(false)"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能禁用当前管理员自身账号")
    
    user.is_active = is_active
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    payload: PasswordReset,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    
    user.hashed_password = get_password_hash(payload.new_password)
    await db.commit()
    return {"message": f"用户 '{user.username}' 密码重置成功"}
