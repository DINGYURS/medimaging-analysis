import unittest
from unittest.mock import AsyncMock, Mock
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from app.api.v1.auth import register
from app.schemas.user import UserRegister, UserCreate, UserUpdate, UserOut
from app.core.security import verify_password

class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def payload(self, **overrides):
        return UserRegister(**(dict(username="test_doctor", password="test-password", real_name="测试医生") | overrides))

    def database(self, existing=None):
        db = Mock()
        db.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=existing)))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db.rollback = AsyncMock()
        return db

    async def test_success_hashes_password_and_fixes_role(self):
        db = self.database()
        user = await register(self.payload(), db)
        self.assertEqual(user.role, "DOCTOR")
        self.assertTrue(user.is_active)
        self.assertNotIn("department", user.__table__.columns)
        self.assertNotEqual(user.hashed_password, "test-password")
        self.assertTrue(verify_password("test-password", user.hashed_password))
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(user)

    async def test_existing_username_never_writes(self):
        db = self.database(existing=object())
        with self.assertRaises(HTTPException) as caught:
            await register(self.payload(), db)
        self.assertEqual(caught.exception.status_code, 409)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_concurrent_duplicate_rolls_back(self):
        db = self.database()
        db.commit.side_effect = IntegrityError("insert", {}, Exception(1062, "duplicate"))
        with self.assertRaises(HTTPException) as caught:
            await register(self.payload(), db)
        self.assertEqual(caught.exception.status_code, 409)
        db.rollback.assert_awaited_once()

    async def test_other_integrity_error_is_not_disguised(self):
        db = self.database()
        db.commit.side_effect = IntegrityError("insert", {}, Exception(1048, "null"))
        with self.assertRaises(IntegrityError):
            await register(self.payload(), db)
        db.rollback.assert_awaited_once()

    def test_validation_rejects_privilege_and_bad_inputs(self):
        for invalid in [dict(role="ADMIN"), dict(is_active=False), dict(username="ab"),
                        dict(username="invalid name"), dict(password="short"),
                        dict(password="中"*25), dict(password=" "*8),
                        dict(real_name=" "), dict(department="放射科"), dict(real_name="a"*65)]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    self.payload(**invalid)

    def test_registration_schema_has_no_department(self):
        for schema in [UserRegister, UserCreate, UserUpdate, UserOut]:
            self.assertNotIn("department", schema.model_json_schema()["properties"])

    def test_trimming_and_utf8_boundary(self):
        data = self.payload(username="  doctor_123 ", real_name=" 姓名 ", password="中"*24)
        self.assertEqual(data.username, "doctor_123")
        self.assertEqual(data.real_name, "姓名")
        self.assertEqual(len(data.password.encode()), 72)

if __name__ == "__main__":
    unittest.main()
