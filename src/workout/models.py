from django.db import models

# Create your models here.

from django.conf import settings
from django.utils import timezone
from enum import Enum

class WorkoutRequestStatus(Enum):
    PENDING = "pending"
    GENERATING_WEEK = "generating_week"
    COMPLETED_WEEK = "completed_week"
    ALL_WEEKS_COMPLETED = "all_weeks_completed"
    FAILED_GENERATION = "failed_generation"
    USER_CANCELLED = "user_cancelled"


class WorkoutPlanVisibility(Enum):
    VISIBLE = "visible"
    UPCOMING = "upcoming"
    ARCHIVED = "archived"


class DayOfWeek(Enum):
    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class WorkoutRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workout_requests')
    requested_at = models.DateTimeField(default=timezone.now)
    duration_weeks = models.PositiveIntegerField(default=4)
    days_per_week_preference = models.PositiveIntegerField(default=4)
    fitness_level_at_request = models.CharField(max_length=50, blank=True, null=True)
    primary_goal_at_request = models.CharField(max_length=100, blank=True, null=True)
    specific_focus_areas = models.TextField(blank=True)
    status = models.CharField( max_length=30,  choices=[(tag.name, tag.value) for tag in WorkoutRequestStatus], default=WorkoutRequestStatus.PENDING.name)
    current_week_being_generated = models.PositiveIntegerField(null=True, blank=True,
                                                               help_text="Which week number is currently in process (1-indexed)")
    error_message = models.TextField(blank=True, null=True)
    next_generation_due_date = models.DateField(null=True, blank=True,
                                                help_text="When the next week's plan should be triggered for generation")

    def __str__(self):
        return f"Workout request for {self.user.email} ({self.duration_weeks} weeks, {self.days_per_week_preference} days/week) at {self.requested_at.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        ordering = ['-requested_at']


class WorkoutPlan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workout_plans')
    source_request = models.ForeignKey(WorkoutRequest, on_delete=models.CASCADE, related_name='generated_plans')
    week_number = models.PositiveIntegerField(help_text="e.g., Week 1, Week 2 of the overall request (1-indexed)")
    plan_active_date = models.DateField(help_text="The date this weekly plan becomes active for the user")
    visibility = models.CharField(max_length=10, choices=[(tag.name, tag.value) for tag in WorkoutPlanVisibility], default=WorkoutPlanVisibility.UPCOMING.name)
    weekly_theme = models.CharField(max_length=255, blank=True, null=True,
                                    help_text="e.g., 'Hypertrophy Focus - Phase 1' or 'Deload Week'")
    generation_prompt = models.TextField(blank=True, null=True,
                                         help_text="The actual prompt sent to the LLM for this week (for debugging/auditing)")
    llm_response_raw = models.JSONField(null=True, blank=True,
                                        help_text="Raw response from LLM for this week (for debugging/auditing)")
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Week {self.week_number} plan for {self.user.email} (Active: {self.plan_active_date})"

    class Meta:
        unique_together = ('source_request', 'week_number')
        ordering = ['user', 'plan_active_date', 'week_number']


class WorkoutDay(models.Model):
    workout_plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name='days')
    day_sequence_number = models.PositiveIntegerField(
        help_text="Order of this workout day within the plan's active days (e.g., 1st workout, 2nd workout)")
    calendar_date = models.DateField(null=True, blank=True)

    day_title = models.CharField(max_length=100, blank=True,
                                 help_text="e.g., 'Push Day - Chest, Shoulders, Triceps' or 'Full Body A'")
    is_rest_day = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.workout_plan} - Workout Day {self.day_sequence_number} ({self.day_title or 'Rest'})"

    class Meta:
        unique_together = ('workout_plan', 'day_sequence_number')
        ordering = ['workout_plan', 'day_sequence_number']


class Exercise(models.Model):
    workout_day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name='exercises')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True, help_text="How to perform the exercise, tips from AI")
    target_sets = models.CharField(max_length=20, blank=True, null=True)
    target_reps = models.CharField(max_length=20, blank=True, null=True)
    target_rest_seconds = models.PositiveIntegerField(null=True, blank=True)
    target_weight_or_intensity = models.CharField(max_length=50, blank=True, null=True,
                                                  help_text="e.g., RPE 8, 70% 1RM, bodyweight")
    media_url = models.URLField(blank=True, null=True)
    order = models.PositiveIntegerField(default=0, help_text="Order of exercise within the day's workout")

    def __str__(self):
        return f"{self.name} ({self.target_sets}x{self.target_reps}) for {self.workout_day}"

    class Meta:
        ordering = ['workout_day', 'order']


class ExerciseLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exercise_logs')
    planned_exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='logs')
    exercise_name_logged = models.CharField(max_length=100,
                                            help_text="Name of the exercise performed (if not from plan or overriding)")
    sets_data = models.JSONField(default=list,
                                 help_text="List of dicts, e.g., [{'reps': 10, 'weight': 50, 'set_number': 1}, ...]")

    log_date = models.DateField(default=timezone.now)
    workout_day_completed = models.ForeignKey(WorkoutDay, on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name="completed_logs")
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        exercise_display = self.planned_exercise.name if self.planned_exercise and not self.exercise_name_logged else self.exercise_name_logged
        return f"Log for {exercise_display} by {self.user.email} on {self.log_date}"

    class Meta:
        ordering = ['-log_date', '-created_at']

    @property
    def total_reps_done(self):
        if isinstance(self.sets_data, list):
            return sum(
                s.get('reps', 0) for s in self.sets_data if isinstance(s, dict) and isinstance(s.get('reps'), int))
        return 0

    @property
    def total_sets_done(self):
        if isinstance(self.sets_data, list):
            return len(self.sets_data)
        return 0

    def save(self, *args, **kwargs):
        if self.planned_exercise and not self.exercise_name_logged:
            self.exercise_name_logged = self.planned_exercise.name
        super().save(*args, **kwargs)