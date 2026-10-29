from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator


class LoginRequest(BaseModel):
    code: str
    password: str


class UserInfo(BaseModel):
    code: str
    name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    user: UserInfo


class ChangePasswordRequest(BaseModel):
    target_code: Optional[str] = None
    old_password: Optional[str] = None
    new_password: str


class AuthMessage(BaseModel):
    message: str


class AuthStatus(BaseModel):
    user: UserInfo
    credential_type: Literal["session", "api_key"]
    credential_id: str
    label: Optional[str] = None
    expires_at: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    investor_code: Optional[str] = Field(default=None, min_length=1, max_length=20)
    scopes: list[Literal["ledger:read", "ledger:write"]] = Field(default=["ledger:read"], min_length=1, max_length=2)
    expires_at: Optional[AwareDatetime] = None

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("标签不能为空或含控制字符")
        return value


class ApiKeyInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investor_code: str
    label: str
    scopes: list[str]
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    # created_at 非空且必填：serializer 返回注解决定 OpenAPI 的 anyOf/required，
    # 与可空字段混用一个 Optional 注解会让 openapi.json 出现
    # "required 但 nullable" 的自相矛盾契约，并泄进 CLI 生成契约（str?）。
    @field_serializer("created_at")
    def created_at_iso(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()

    @field_serializer("expires_at", "revoked_at")
    def utc_timestamp(self, value: Optional[datetime]) -> Optional[str]:
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class ApiKeyCreated(BaseModel):
    key: ApiKeyInfo
    token: str
