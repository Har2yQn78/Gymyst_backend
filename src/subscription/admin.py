from django.contrib import admin

# Register your models here.

from .models import PlanTier, UserSubscription, PaymentTransaction


@admin.register(PlanTier)
class PlanTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'currency', 'duration_days', 'max_requests', 'is_active')
    list_filter = ('is_active', 'currency')
    search_fields = ('name',)


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
    'user', 'plan_tier', 'status', 'start_date', 'expire_date', 'is_active', 'latest_payment_transaction_id')
    list_filter = ('status', 'plan_tier__name')
    search_fields = ('user__email', 'latest_payment_transaction_id')
    raw_id_fields = ('user', 'plan_tier')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['check_and_update_status']

    def check_and_update_status(self, request, queryset):
        updated_count = 0
        for sub in queryset:
            old_status = sub.status
            new_status = sub.update_status()
            if old_status != new_status:
                updated_count += 1
        self.message_user(request, f"{updated_count} subscriptions had their status updated.")

    check_and_update_status.short_description = "Check and Update Expiry Status"


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
    'gateway_transaction_id', 'user', 'plan_tier_purchased', 'amount_display', 'status', 'request_timestamp',
    'verification_timestamp')
    list_filter = ('status', 'payment_gateway', 'currency', 'request_timestamp')
    search_fields = ('user__email', 'gateway_transaction_id', 'plan_tier_purchased__name')
    raw_id_fields = ('user', 'plan_tier_purchased', 'user_subscription_updated')
    readonly_fields = (
    'request_timestamp', 'verification_timestamp', 'gateway_response_on_request', 'gateway_response_on_verify')

    fieldsets = (
        (None, {'fields': ('user', 'plan_tier_purchased', 'user_subscription_updated')}),
        ('Gateway & Payment Details',
         {'fields': ('payment_gateway', 'gateway_transaction_id', 'amount', 'currency', 'status', 'description')}),
        ('Timestamps', {'fields': ('request_timestamp', 'verification_timestamp')}),
        ('Gateway Raw Responses',
         {'classes': ('collapse',), 'fields': ('gateway_response_on_request', 'gateway_response_on_verify')}),
    )

    def amount_display(self, obj):
        return f"{obj.amount} {obj.currency}"

    amount_display.short_description = "Amount"