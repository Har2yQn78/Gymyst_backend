import requests
import json
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from typing import Optional, Dict, Any

from .models import PlanTier, UserSubscription, PaymentTransaction

User = get_user_model()

# Zarinpal Specific URLs (move to settings or constants if preferred)
ZARINPAL_API_REQUEST_URL = 'https://api.zarinpal.com/pg/v4/payment/request.json'
ZARINPAL_API_VERIFY_URL = 'https://api.zarinpal.com/pg/v4/payment/verify.json'
ZARINPAL_STARTPAY_URL_TEMPLATE = 'https://www.zarinpal.com/pg/StartPay/{}'


def initiate_zarinpal_payment(user: User, plan_tier_id: int) -> dict:
    plan = get_object_or_404(PlanTier, id=plan_tier_id, is_active=True)
    callback_url = settings.PAYMENT_CALLBACK_DOMAIN + reverse('api-1.0.0:payment_callback')
    description = f"Purchase of {plan.name} for user {user.email}"
    amount_in_rial = int(plan.price)
    if plan.currency == 'IRT':
        amount_in_rial = int(plan.price * 10)

    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": amount_in_rial,
        "currency": "IRT",
        "callback_url": callback_url,
        "description": description,
        "metadata": {
            "user_id": str(user.id),
            "plan_tier_id": str(plan.id),
            "email": user.email
        }
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }

    try:
        response = requests.post(ZARINPAL_API_REQUEST_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        response_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Zarinpal request error: {e}")
        raise ConnectionError(f"Failed to connect to payment gateway: {e}")
    except json.JSONDecodeError as e:
        print(f"Zarinpal JSON decode error: {e} - Response was: {response.text}")
        raise ValueError(f"Invalid response from payment gateway: {e}")

    if response_data.get("data") and response_data["data"].get("authority"):
        authority = response_data["data"]["authority"]
        transaction = PaymentTransaction.objects.create(
            user=user,
            plan_tier_purchased=plan,
            gateway_transaction_id=authority,
            amount=plan.price,
            currency=plan.currency,
            status=PaymentTransaction.TransactionStatus.PENDING,
            payment_gateway="zarinpal",
            gateway_response_on_request=response_data,
            description=description
        )
        payment_url = ZARINPAL_STARTPAY_URL_TEMPLATE.format(authority)
        return {"payment_url": payment_url, "authority": authority, "transaction_db_id": transaction.id}
    else:
        error_message = response_data.get("errors", {}).get("message", "Unknown error from Zarinpal.")
        print(f"Zarinpal payment initiation failed: {error_message} - Full response: {response_data}")
        PaymentTransaction.objects.create(
            user=user, plan_tier_purchased=plan, amount=plan.price, currency=plan.currency,
            status=PaymentTransaction.TransactionStatus.FAILED, payment_gateway="zarinpal",
            gateway_response_on_request=response_data, description=f"Zarinpal init failed: {error_message}"
        )
        raise ValueError(f"Zarinpal payment initiation failed: {error_message}")


def verify_zarinpal_payment(authority: str, status_from_callback: str, user_from_session_or_metadata=None):
    transaction = PaymentTransaction.objects.filter(gateway_transaction_id=authority).first()

    if not transaction:
        print(f"Verification Error: No transaction found for authority {authority}")
        return {"success": False, "message": "Transaction not found.", "transaction_status": "error"}

    if transaction.status in [PaymentTransaction.TransactionStatus.VERIFIED,
                              PaymentTransaction.TransactionStatus.SUCCESSFUL]:
        print(f"Verification Info: Transaction {authority} already processed with status {transaction.status}.")
        user_sub = UserSubscription.objects.filter(user=transaction.user).first()
        return {
            "success": True,
            "message": "Transaction already verified.",
            "transaction_status": transaction.status,
            "subscription_active_until": user_sub.expire_date if user_sub else None
        }

    if status_from_callback != "OK":
        transaction.status = PaymentTransaction.TransactionStatus.FAILED
        transaction.gateway_response_on_verify = {"status_from_callback": status_from_callback,
                                                  "message": "User cancelled or payment failed on gateway."}
        transaction.verification_timestamp = timezone.now()
        transaction.save()
        print(f"Payment for authority {authority} was not successful on gateway (Status: {status_from_callback}).")
        return {"success": False, "message": "Payment was not completed successfully.",
                "transaction_status": transaction.status}

    plan = transaction.plan_tier_purchased
    if not plan:
        transaction.status = PaymentTransaction.TransactionStatus.FAILED
        transaction.gateway_response_on_verify = {"error": "Plan tier missing from transaction record."}
        transaction.verification_timestamp = timezone.now()
        transaction.save()
        return {"success": False, "message": "Internal error: Plan details missing.",
                "transaction_status": transaction.status}

    amount_in_rial = int(plan.price)
    if plan.currency == 'IRT':
        amount_in_rial = int(plan.price * 10)

    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": amount_in_rial,
        "authority": authority
    }
    headers = {"accept": "application/json", "content-type": "application/json"}

    try:
        response = requests.post(ZARINPAL_API_VERIFY_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        verification_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Zarinpal verification API error for {authority}: {e}")
        transaction.gateway_response_on_verify = {"error": f"Gateway verification API error: {e}"}
        transaction.save()
        return {"success": False,
                "message": "Could not verify payment with gateway at this time. Please contact support.",
                "transaction_status": transaction.status}

    transaction.gateway_response_on_verify = verification_data
    transaction.verification_timestamp = timezone.now()

    if verification_data.get("data") and verification_data["data"].get("code") == 100:  # Code 100: Verified
        transaction.status = PaymentTransaction.TransactionStatus.VERIFIED
        user_subscription, created = UserSubscription.objects.get_or_create(user=transaction.user)
        user_subscription.plan_tier = plan
        user_subscription.status = UserSubscription.SubscriptionStatus.ACTIVE

        now = timezone.now()
        if user_subscription.expire_date and user_subscription.expire_date > now and user_subscription.plan_tier == plan:
            user_subscription.start_date = user_subscription.expire_date
        else:
            user_subscription.start_date = now

        user_subscription.expire_date = user_subscription.start_date + timedelta(days=plan.duration_days)
        user_subscription.latest_payment_transaction_id = transaction.gateway_transaction_id
        user_subscription.save()

        transaction.user_subscription_updated = user_subscription
        transaction.save()

        ref_id = verification_data["data"].get("ref_id", "N/A")
        print(
            f"Payment for authority {authority} (Ref ID: {ref_id}) VERIFIED. Subscription for {transaction.user.email} updated.")
        return {
            "success": True,
            "message": "Payment verified and subscription activated.",
            "ref_id": ref_id,
            "transaction_status": transaction.status,
            "subscription_active_until": user_subscription.expire_date
        }
    elif verification_data.get("data") and verification_data["data"].get(
            "code") == 101:  # Code 101: Verified but submitted before (idempotency)
        transaction.status = PaymentTransaction.TransactionStatus.VERIFIED  # Already processed
        transaction.save()
        user_sub = UserSubscription.objects.filter(user=transaction.user,
                                                   latest_payment_transaction_id=transaction.gateway_transaction_id).first()
        if not user_sub:
            user_subscription, created = UserSubscription.objects.get_or_create(user=transaction.user)
            print(
                f"Payment for authority {authority} was already verified (code 101), re-checked/granted subscription.")
        else:
            print(f"Payment for authority {authority} was already verified (code 101), subscription already active.")

        return {
            "success": True,
            "message": "Payment was already verified.",
            "ref_id": verification_data["data"].get("ref_id", "N/A"),
            "transaction_status": transaction.status,
            "subscription_active_until": user_sub.expire_date if user_sub else None
        }
    else:
        error_message = verification_data.get("errors", {}).get("message", "Verification failed.")
        error_code = verification_data.get("errors", {}).get("code", "Unknown")
        transaction.status = PaymentTransaction.TransactionStatus.FAILED
        transaction.save()
        print(f"Zarinpal verification failed for {authority}: {error_message} (Code: {error_code})")
        return {"success": False, "message": f"Payment verification failed: {error_message}",
                "transaction_status": transaction.status}


def get_user_subscription_details(user: User) -> Optional[UserSubscription]:
    try:
        subscription = UserSubscription.objects.select_related('plan_tier').get(user=user)
        subscription.update_status()  # Check if expired and update
        return subscription
    except UserSubscription.DoesNotExist:
        return None

def cancel_user_subscription_immediately(user: User):
    sub = UserSubscription.objects.filter(user=user).first()
    if sub and sub.is_active:
        sub.status = UserSubscription.SubscriptionStatus.CANCELED
        sub.expire_date = timezone.now()
        sub.save()
        print(f"Subscription for user {user.email} cancelled immediately.")
        return True
    return False