import os
from celery import Celery
from celery.schedules import crontab
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gymbackend.settings')
app = Celery('gymbackend')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Optional: Celery Beat Schedulers
app.conf.beat_schedule = {
    'trigger-next-week-workout-generation-daily': {
        'task': 'workout.tasks.schedule_next_workout_week_generation', # We will create this task
        'schedule': crontab(hour=1, minute=0),  # Run daily at 1:00 AM
    },
    'activate-upcoming-workout-plans-daily': {
        'task': 'workout.tasks.activate_upcoming_workout_plans', # We will create this task
        'schedule': crontab(hour=2, minute=0), # Run daily at 2:00 AM
    },
    # Add other scheduled tasks here (e.g., subscription status updates, reminders)
     'update-expired-subscriptions-daily': {
        'task': 'subscription.tasks.update_expired_subscriptions_status', # We'll need to create this
        'schedule': crontab(hour=3, minute=0), # Run daily at 3:00 AM
    },
}

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')