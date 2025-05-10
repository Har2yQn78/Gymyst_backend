from ninja import Schema
from pydantic import Field
from typing import Optional, List
from datetime import date, datetime

class PlanTierSchema(Schema):
    id: int
    name: str
    price: float
    currency: str
    duration_days: int
    max_requests: int
    description: Optional[str] = None
    is_active: bool

class UserSubscriptionSchema(Schema):
    id: int
    plan_tier: Optional[PlanTierSchema] = None
    status: str
    start_date: Optional[datetime] = None
    expire_date: Optional[datetime] = None
    is_active: bool
    latest_payment_transaction_id: Optional[str] = None

class PaymentInitiationRequestSchema(Schema):
    plan_tier_id: int

class PaymentInitiationResponseSchema(Schema):
    payment_url: str
    authority: str

class ErrorDetailSchema(Schema):
    detail: str

class MessageResponseSchema(Schema):
    message: str

class PaymentVerificationResponseSchema(Schema):
    message: str
    ref_id: Optional[str] = None
    subscription_active_until: Optional[datetime] = None