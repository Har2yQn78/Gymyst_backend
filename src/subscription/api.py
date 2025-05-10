from ninja_extra import api_controller, route
from ninja_extra.permissions import IsAuthenticated
from django.http import HttpRequest
from typing import List

from .schemas import (
    PlanTierSchema,
    UserSubscriptionSchema,
    PaymentInitiationRequestSchema,
    PaymentInitiationResponseSchema,
    ErrorDetailSchema,
    MessageResponseSchema,
    PaymentVerificationResponseSchema
)
from . import services
from .models import PlanTier


@api_controller("/subscription", tags=["Subscription"])
class SubscriptionController:
    @route.get("/tiers", response={200: List[PlanTierSchema], 403: ErrorDetailSchema}, permissions=[IsAuthenticated])
    def list_tiers(self, request: HttpRequest):
        user = request.auth
        if not user or not (hasattr(user, 'is_authenticated') and user.is_authenticated):
            return 403, {"detail": "User not properly authenticated."}

        return PlanTier.objects.filter(is_active=True)

    @route.post(
        "/initiate-payment",
        permissions=[IsAuthenticated],
        response={
            200: PaymentInitiationResponseSchema,
            403: ErrorDetailSchema,
            404: ErrorDetailSchema,
            500: ErrorDetailSchema,
            503: ErrorDetailSchema}
    )
    def initiate_payment(self, request: HttpRequest, payload: PaymentInitiationRequestSchema):
        user = request.auth
        if not user or not (hasattr(user, 'is_authenticated') and user.is_authenticated):
            return 403, {"detail": "User not properly authenticated."}

        try:
            result = services.initiate_zarinpal_payment(user, payload.plan_tier_id)
            return 200, PaymentInitiationResponseSchema(payment_url=result.get("payment_url"), authority=result.get("authority"))
        except PlanTier.DoesNotExist:
            return 404, {"detail": "Plan tier not found or inactive."}
        except (ConnectionError, ValueError) as e:
            return 503, {"detail": str(e)}
        except Exception as e:
            return 500, {"detail": "An unexpected error occurred."}

    @route.get("/status", permissions=[IsAuthenticated], response={200: UserSubscriptionSchema, 403: ErrorDetailSchema})
    def get_subscription_status(self, request: HttpRequest):
        user = request.auth
        if not user or not (hasattr(user, 'is_authenticated') and user.is_authenticated):
            return 403, {"detail": "User not properly authenticated."}

        subscription = services.get_user_subscription_details(user)
        if not subscription:
            return 200, UserSubscriptionSchema(id=-1, status="none", is_active=False)
        return 200, subscription

    @route.post("/cancel-immediately", permissions=[IsAuthenticated], response={200: MessageResponseSchema, 400: ErrorDetailSchema, 403: ErrorDetailSchema})
    def cancel_subscription_now(self, request: HttpRequest):
        user = request.auth
        if not user or not (hasattr(user, 'is_authenticated') and user.is_authenticated):
            return 403, {"detail": "User not properly authenticated."}

        if services.cancel_user_subscription_immediately(user):
            return 200, {"message": "Subscription has been cancelled immediately."}
        else:
            return 400, {"detail": "No active subscription found to cancel or already cancelled."}


@api_controller("/payment", tags=["Payment Callback"])
class PaymentCallbackController:

    @route.get("/callback", auth=None, url_name="payment_callback", response={
            200: PaymentVerificationResponseSchema,
            400: ErrorDetailSchema,
            500: ErrorDetailSchema
        }
    )
    def payment_gateway_callback(self, request: HttpRequest):
        authority = request.GET.get('Authority')
        status_from_callback = request.GET.get('Status')
        if not authority or not status_from_callback:
            return 400, {"detail": "Missing payment authority or status from gateway."}

        try:
            verification_result = services.verify_zarinpal_payment(authority, status_from_callback)
        except Exception as e:
            return 500, {"detail": "An error occurred while verifying payment. Please contact support."}

        if verification_result.get("success"):
            return 200, {
                "message": verification_result.get("message"),
                "ref_id": verification_result.get("ref_id"),
                "subscription_active_until": verification_result.get("subscription_active_until")
            }
        else:
            return 400, {"detail": verification_result.get("message", "Payment verification failed.")}