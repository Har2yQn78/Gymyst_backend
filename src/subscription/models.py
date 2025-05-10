from django.db import models

# Create your models here.

from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class PlanTier(models.Model):
    name = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=0)
    currency = models.CharField(max_length=3, default="IRT")
    duration_days = models.PositiveIntegerField(default=30)
    max_requests = models.PositiveIntegerField(default=10, help_text="Max AI generation requests for this tier during its duration")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} - {self.price} {self.currency} / {self.duration_days} days"


class UserSubscription(models.Model):
    class SubscriptionStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        EXPIRED = 'expired', 'Expired'
        PENDING_PAYMENT = 'pending_payment', 'Pending Payment'
        CANCELED = 'canceled', 'Canceled'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    plan_tier = models.ForeignKey(PlanTier, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=SubscriptionStatus.choices, default=SubscriptionStatus.EXPIRED)
    start_date = models.DateTimeField(null=True, blank=True)
    expire_date = models.DateTimeField(null=True, blank=True)
    latest_payment_transaction_id = models.CharField(max_length=100, blank=True, null=True,
                                                     help_text="Gateway's transaction ID for the latest payment")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.plan_tier.name if self.plan_tier else 'No Plan'} (Expires: {self.expire_date})"

    @property
    def is_active(self):
        return self.status == self.SubscriptionStatus.ACTIVE and self.expire_date and self.expire_date >= timezone.now()

    def update_status(self):
        if self.status == self.SubscriptionStatus.ACTIVE and self.expire_date and self.expire_date < timezone.now():
            self.status = self.SubscriptionStatus.EXPIRED
            self.save()
        return self.status


class PaymentTransaction(models.Model):
    class TransactionStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESSFUL = 'successful', 'Successful'
        FAILED = 'failed', 'Failed'
        VERIFIED = 'verified', 'Verified & Processed'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payment_transactions')
    plan_tier_purchased = models.ForeignKey(PlanTier, on_delete=models.SET_NULL, null=True,
                                            blank=True)
    user_subscription_updated = models.ForeignKey(UserSubscription, on_delete=models.SET_NULL, null=True, blank=True,
                                                  help_text="The subscription record this payment updated/created")
    gateway_transaction_id = models.CharField(max_length=100, unique=True,
                                              help_text="Transaction ID from payment gateway (e.g., Zarinpal Authority)")
    amount = models.DecimalField(max_digits=10, decimal_places=0)
    currency = models.CharField(max_length=3, default="IRR")
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING)
    payment_gateway = models.CharField(max_length=50, default="zarinpal")
    request_timestamp = models.DateTimeField(auto_now_add=True)
    verification_timestamp = models.DateTimeField(null=True, blank=True)
    gateway_response_on_request = models.JSONField(null=True, blank=True,
                                                   help_text="Full response from gateway on payment request")
    gateway_response_on_verify = models.JSONField(null=True, blank=True,
                                                  help_text="Full response from gateway on payment verification")
    description = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"Tx {self.gateway_transaction_id} for {self.user.email} - {self.status}"
