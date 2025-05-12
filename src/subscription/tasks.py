from celery import shared_task
from django.utils import timezone
from .models import UserSubscription


@shared_task(name="subscription.tasks.update_expired_subscriptions_status")
def update_expired_subscriptions_status():
    print("CELERY BEAT: Running update_expired_subscriptions_status")
    expired_subs_updated_count = 0
    subscriptions_to_check = UserSubscription.objects.filter(
        status=UserSubscription.SubscriptionStatus.ACTIVE,
        expire_date__lt=timezone.now()
    )
    for sub in subscriptions_to_check:
        old_status = sub.status
        new_status = sub.update_status()
        if old_status != new_status:
            expired_subs_updated_count += 1
            print(f"Subscription for {sub.user.email} updated from {old_status} to {new_status}")

    print(f"CELERY BEAT: Checked and updated status for {expired_subs_updated_count} subscriptions.")
    return f"Updated {expired_subs_updated_count} subscriptions."