from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class UserBase(BaseModel):
    username: str
    real_name: str
    department: Optional[str] = "放射科"
    role: Optional[str] = "DOCTOR"
    is_active: Optional[bool] = True

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    real_name: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None

class PasswordReset(BaseModel):
    new_password: str

class UserOut(UserBase):
    id: int
    last_login_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
