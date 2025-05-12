from datetime import date

from ninja_extra import api_controller, route, ControllerBase
from ninja_extra.permissions import IsAuthenticated
from ninja_extra.pagination import paginate, PageNumberPagination  # For pagination
from django.shortcuts import get_object_or_404
from django.utils import timezone
from typing import List, Optional

from accounts.models import UserProfile
from .models import WorkoutRequest, WorkoutPlan, Exercise, ExerciseLog, WorkoutRequestStatus, WorkoutPlanVisibility
from .schemas import (
    WorkoutRequestCreateSchemaIn, WorkoutRequestSchemaOut,
    WorkoutPlanSchemaOut,
    ExerciseLogCreateSchemaIn, ExerciseLogSchemaOut,
    PaginatedWorkoutRequests, PaginatedWorkoutLogs,
    ErrorDetailSchema, MessageResponseSchema
)
from .tasks import generate_workout_for_specific_week  # Import the Celery task


@api_controller("/workout", permissions=[IsAuthenticated], tags=["Workout"])
class WorkoutController(ControllerBase):

    @route.post("/request-plan",
                response={201: WorkoutRequestSchemaOut, 400: ErrorDetailSchema, 403: ErrorDetailSchema})
    def request_workout_plan(self, request, payload: WorkoutRequestCreateSchemaIn):
        user = request.auth
        try:
            user_profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return 403, {"detail": "User profile not found or incomplete. Please update your profile."}

        # Check for existing active/pending requests to prevent spamming (optional, based on product decision)
        # existing_request = WorkoutRequest.objects.filter(user=user, status__in=[
        #     WorkoutRequestStatus.PENDING.name,
        #     WorkoutRequestStatus.GENERATING_WEEK.name,
        #     WorkoutRequestStatus.COMPLETED_WEEK.name
        # ]).exclude(status=WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name).first()
        # if existing_request:
        #     return 400, {"detail": "You already have an active workout plan request being processed or ongoing."}

        workout_req = WorkoutRequest.objects.create(
            user=user,
            duration_weeks=payload.duration_weeks,
            days_per_week_preference=payload.days_per_week_preference,
            fitness_level_at_request=payload.fitness_level_override or user_profile.get_fitness_level_display(),
            primary_goal_at_request=payload.primary_goal_override or user_profile.get_goal_display(),
            specific_focus_areas=payload.specific_focus_areas or "",
            status=WorkoutRequestStatus.PENDING.name,  # Will be picked up by beat or triggered directly
            # Set due date for Week 1 generation to be immediate or very soon
            next_generation_due_date=timezone.now().date()
        )

        # Trigger the generation of the first week immediately
        # This provides faster feedback to the user that something is happening.
        # The Celery Beat task `schedule_next_workout_week_generation` will handle subsequent weeks.
        print(f"API: Triggering immediate generation for Request ID {workout_req.id}, Week 1")
        generate_workout_for_specific_week.delay(workout_request_id=workout_req.id, week_number=1)

        # Update status to indicate it's processing the first week
        workout_req.status = WorkoutRequestStatus.GENERATING_WEEK.name
        workout_req.current_week_being_generated = 1
        workout_req.save()

        return 201, workout_req

    @route.get("/plan-requests", response={200: PaginatedWorkoutRequests, 403: ErrorDetailSchema})
    @paginate(PageNumberPagination, page_size=10)  # Add pagination
    def list_workout_plan_requests(self, request):
        user = request.auth
        # Order by most recent first
        return WorkoutRequest.objects.filter(user=user).order_by('-requested_at')

    @route.get("/plan-requests/{request_id}",
               response={200: WorkoutRequestSchemaOut, 404: ErrorDetailSchema, 403: ErrorDetailSchema})
    def get_workout_plan_request_details(self, request, request_id: int):
        user = request.auth
        workout_req = get_object_or_404(WorkoutRequest, id=request_id, user=user)
        return 200, workout_req

    @route.get("/active-plan", response={200: WorkoutPlanSchemaOut, 404: ErrorDetailSchema, 403: ErrorDetailSchema})
    def get_active_workout_plan(self, request):
        user = request.auth
        # Get the current visible workout plan for the user
        # The Celery beat task `activate_upcoming_workout_plans` handles setting visibility.
        active_plan = WorkoutPlan.objects.filter(
            user=user,
            visibility=WorkoutPlanVisibility.VISIBLE.name
        ).order_by('-plan_active_date', '-week_number').first()  # Get the latest active one

        if not active_plan:
            return 404, {"detail": "No active workout plan found. Request one or check back later."}

        return 200, active_plan

    @route.get("/plan-history", response={200: List[WorkoutPlanSchemaOut], 403: ErrorDetailSchema})  # Or paginated
    # @paginate(PageNumberPagination, page_size=5) # If you want pagination here too
    def get_workout_plan_history(self, request):
        user = request.auth
        # Get all plans (visible, upcoming, archived) for the user, ordered
        return WorkoutPlan.objects.filter(user=user).order_by('-plan_active_date', '-week_number')

    @route.post("/log-exercise", response={201: ExerciseLogSchemaOut, 400: ErrorDetailSchema, 404: ErrorDetailSchema,
                                           403: ErrorDetailSchema})
    def log_exercise_performance(self, request, payload: ExerciseLogCreateSchemaIn):
        user = request.auth

        planned_exercise_instance = None
        if payload.planned_exercise_id:
            # Ensure the planned exercise belongs to the user (indirectly via WorkoutPlan -> WorkoutDay)
            planned_exercise_instance = get_object_or_404(
                Exercise.objects.select_related('workout_day__workout_plan__user'),
                id=payload.planned_exercise_id
            )
            if planned_exercise_instance.workout_day.workout_plan.user != user:
                return 403, {"detail": "You can only log against your own planned exercises."}

        exercise_log = ExerciseLog.objects.create(
            user=user,
            planned_exercise=planned_exercise_instance,
            exercise_name_logged=payload.exercise_name_logged or (
                planned_exercise_instance.name if planned_exercise_instance else "Ad-hoc Exercise"),
            sets_data=[item.dict() for item in payload.sets_data],  # Convert Pydantic models to dicts
            log_date=payload.log_date,
            workout_day_completed_id=payload.workout_day_completed_id,  # Add validation if this ID belongs to user
            notes=payload.notes
        )
        return 201, exercise_log

    @route.get("/exercise-logs", response={200: PaginatedWorkoutLogs, 403: ErrorDetailSchema})
    @paginate(PageNumberPagination, page_size=20)
    def list_exercise_logs(self, request, start_date: Optional[date] = None, end_date: Optional[date] = None):
        user = request.auth
        logs = ExerciseLog.objects.filter(user=user).order_by('-log_date', '-created_at')
        if start_date:
            logs = logs.filter(log_date__gte=start_date)
        if end_date:
            logs = logs.filter(log_date__lte=end_date)
        return logs

    @route.get("/exercise-logs/{log_id}",
               response={200: ExerciseLogSchemaOut, 404: ErrorDetailSchema, 403: ErrorDetailSchema})
    def get_exercise_log_detail(self, request, log_id: int):
        user = request.auth
        log_entry = get_object_or_404(ExerciseLog, id=log_id, user=user)
        return 200, log_entry

    # TODO: Add PUT/PATCH for updating an ExerciseLog, DELETE for an ExerciseLog