from django.contrib import admin

# Register your models here.

from .models import WorkoutRequest, WorkoutPlan, WorkoutDay, Exercise, ExerciseLog

@admin.register(WorkoutRequest)
class WorkoutRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'requested_at', 'duration_weeks', 'days_per_week_preference', 'status', 'current_week_being_generated', 'next_generation_due_date')
    list_filter = ('status', 'days_per_week_preference')
    search_fields = ('user__email',)

@admin.register(WorkoutPlan)
class WorkoutPlanAdmin(admin.ModelAdmin):
    list_display = ('user', 'source_request', 'week_number', 'plan_active_date', 'visibility', 'generated_at')
    list_filter = ('visibility', 'user__email')
    search_fields = ('source_request__id',)

@admin.register(WorkoutDay)
class WorkoutDayAdmin(admin.ModelAdmin):
    list_display = ('workout_plan', 'day_sequence_number', 'calendar_date', 'day_title', 'is_rest_day')
    list_filter = ('is_rest_day',)
    search_fields = ('workout_plan__source_request__id', 'day_title')

@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ('workout_day', 'name', 'target_sets', 'target_reps', 'order')
    search_fields = ('name', 'workout_day__day_title')

@admin.register(ExerciseLog)
class ExerciseLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'exercise_name_logged', 'log_date', 'total_sets_done_admin', 'total_reps_done_admin')
    list_filter = ('log_date',)
    search_fields = ('user__email', 'exercise_name_logged')

    def total_sets_done_admin(self, obj):
        return obj.total_sets_done
    total_sets_done_admin.short_description = 'Sets Done'

    def total_reps_done_admin(self, obj):
        return obj.total_reps_done
    total_reps_done_admin.short_description = 'Total Reps'
