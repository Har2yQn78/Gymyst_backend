from ninja import Schema
from pydantic import EmailStr, Field, BaseModel as PydanticBaseModel
from typing import Optional
from datetime import date, datetime
from .models import SexChoices, GoalChoices, FitnessLevelChoices

class ErrorDetail(Schema):
    detail: str = Field

class UserCreateSchemaIn(Schema):
    email: EmailStr
    username: str
    name: str
    family_name: str
    password: str

class UserSchemaOut(Schema):
    id: int
    email: EmailStr
    username: str
    name: str
    family_name: str
    is_active: bool
    date_joined: datetime

class TokenSchema(Schema):
    access: str
    refresh: str

class AuthResponseSchema(Schema):
    user: UserSchemaOut
    tokens: TokenSchema

class LoginPayload(PydanticBaseModel):
    email: EmailStr
    password: str

class ProfileUpdateSchemaIn(Schema):
    city: Optional[str] = None
    birthday_date: Optional[date] = None
    sex: Optional[SexChoices] = None
    goal: Optional[GoalChoices] = None
    fitness_level: Optional[FitnessLevelChoices] = None
    height: Optional[float] = None
    weight: Optional[float] = None

class ProfileSchemaOut(Schema):
    city: Optional[str] = None
    birthday_date: Optional[date] = None
    sex: Optional[str] = None
    goal: Optional[str] = None
    fitness_level: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    age: Optional[int] = None

class UserWithProfileResponse(Schema):
    user: UserSchemaOut
    profile: ProfileSchemaOut