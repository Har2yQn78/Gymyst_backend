from celery import shared_task
from django.utils import timezone
from django.db import transaction
import logging
from django.conf import settings
from .models import (
    WorkoutRequest, WorkoutPlan, WorkoutDay, Exercise,
    WorkoutRequestStatus, WorkoutPlanVisibility
)
from .services import generate_workout_week_via_llm  # Ensure this service is robust

# from accounts.models import UserProfile # UserProfile is fetched within the service if needed

logger = logging.getLogger(__name__)


@shared_task(name="workout.tasks.generate_workout_for_specific_week",
             bind=True,
             max_retries=3,
             default_retry_delay=5 * 60)  # 5 minutes
def generate_workout_for_specific_week(self, workout_request_id: int, week_number: int):
    """
    Generates a workout plan for a specific week of a WorkoutRequest.
    This task is responsible for:
    1. Calling the LLM service to get plan data.
    2. Creating/updating WorkoutPlan, WorkoutDay, and Exercise objects in the database.
    3. Updating the status of the parent WorkoutRequest.
    """
    logger.info(
        f"TASK [generate_workout_for_specific_week]: Starting for Request ID: {workout_request_id}, Week: {week_number}")
    try:
        workout_request = WorkoutRequest.objects.select_related('user').get(id=workout_request_id)
    except WorkoutRequest.DoesNotExist:
        logger.error(f"TASK [generate_workout_for_specific_week]: WorkoutRequest ID {workout_request_id} not found.")
        return f"WorkoutRequest ID {workout_request_id} not found."

    # Prevent re-processing if a plan for this week already exists and is considered complete/visible
    # This helps with idempotency, especially if a task is retried after partial success.
    existing_plan_for_week = WorkoutPlan.objects.filter(source_request=workout_request, week_number=week_number).first()
    if existing_plan_for_week and existing_plan_for_week.visibility == WorkoutPlanVisibility.VISIBLE.name:
        logger.warning(
            f"TASK [generate_workout_for_specific_week]: Plan for Request ID: {workout_request_id}, Week: {week_number} already exists and is VISIBLE. Skipping generation.")
        # Ensure WorkoutRequest status is consistent if this happens
        if workout_request.status == WorkoutRequestStatus.GENERATING_WEEK.name:
            workout_request.status = WorkoutRequestStatus.COMPLETED_WEEK.name
            if week_number >= workout_request.duration_weeks:
                workout_request.status = WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name
            workout_request.save(update_fields=['status'])
        return f"Plan already exists and is visible for Request {workout_request_id}, Week {week_number}."

    # Update WorkoutRequest status to indicate processing for this week
    workout_request.status = WorkoutRequestStatus.GENERATING_WEEK.name
    workout_request.current_week_being_generated = week_number
    workout_request.error_message = ""  # Clear any previous error message for this request
    workout_request.save(update_fields=['status', 'current_week_being_generated', 'error_message'])

    llm_result = None
    try:
        # Call the service to interact with LLM
        llm_result = generate_workout_week_via_llm(workout_request, week_number)

        if llm_result and llm_result.get("parsed_plan"):
            parsed_plan_data = llm_result["parsed_plan"]

            with transaction.atomic():  # Ensure all DB operations for the plan succeed or fail together
                # Determine plan_active_date
                if week_number == 1:
                    # Week 1 starts relative to the request date.
                    # For simplicity, let's make it start on the day of the request.
                    # More complex logic could be "next Monday" or user-defined start.
                    plan_active_dt = workout_request.requested_at.date()
                else:
                    previous_week_plan = WorkoutPlan.objects.filter(
                        source_request=workout_request,
                        week_number=week_number - 1
                    ).order_by('-generated_at').first()  # Get the most recent if multiple somehow exist
                    if previous_week_plan and previous_week_plan.plan_active_date:
                        plan_active_dt = previous_week_plan.plan_active_date + timezone.timedelta(days=7)
                    else:
                        logger.error(
                            f"TASK [generate_workout_for_specific_week]: Critical error. Previous week's plan (Week {week_number - 1}) not found for Request {workout_request_id} to determine start date for Week {week_number}.")
                        raise ValueError(f"Previous week's plan for week {week_number - 1} not found.")

                # Determine initial visibility
                plan_visibility = WorkoutPlanVisibility.UPCOMING.name
                if plan_active_dt <= timezone.now().date():  # If the plan's start date is today or in the past
                    plan_visibility = WorkoutPlanVisibility.VISIBLE.name

                # Create or update the WorkoutPlan for this week
                workout_plan, created_wp = WorkoutPlan.objects.update_or_create(
                    source_request=workout_request,
                    week_number=week_number,
                    user=workout_request.user,  # Denormalized for easier querying if needed
                    defaults={
                        'plan_active_date': plan_active_dt,
                        'visibility': plan_visibility,
                        'weekly_theme': parsed_plan_data.get('weekly_theme', ''),
                        'generation_prompt': llm_result.get("prompt_used", ""),
                        'llm_response_raw': llm_result.get("raw_llm_response", {})  # Ensure this is serializable
                    }
                )
                logger.info(
                    f"TASK [generate_workout_for_specific_week]: {'Created' if created_wp else 'Updated'} WorkoutPlan ID {workout_plan.id} for Request {workout_request_id}, Week {week_number}")

                # Clear existing days/exercises if we are updating a plan (idempotency)
                # This is important if the task retries or is run again for the same week.
                WorkoutDay.objects.filter(workout_plan=workout_plan).delete()  # This cascades to Exercises

                for day_data in parsed_plan_data.get('days', []):
                    day_number_from_llm = day_data.get('day_number')
                    if not isinstance(day_number_from_llm, int) or not (1 <= day_number_from_llm <= 7):
                        logger.warning(
                            f"TASK [generate_workout_for_specific_week]: Invalid 'day_number' ({day_number_from_llm}) from LLM for Request {workout_request_id}, Week {week_number}. Skipping day.")
                        continue

                    day_calendar_date = plan_active_dt + timezone.timedelta(days=day_number_from_llm - 1)

                    workout_day = WorkoutDay.objects.create(
                        workout_plan=workout_plan,
                        day_sequence_number=day_number_from_llm,
                        calendar_date=day_calendar_date,
                        day_title=day_data.get('day_title', 'Workout Day'),
                        is_rest_day=day_data.get('is_rest_day', False),
                        notes=day_data.get('notes', '')
                    )
                    for ex_order, ex_data in enumerate(day_data.get('exercises', []), 1):
                        # Use provided order if available, otherwise use enumerate
                        exercise_order = ex_data.get('order', ex_order)
                        Exercise.objects.create(
                            workout_day=workout_day,
                            name=ex_data.get('name', 'Unnamed Exercise'),
                            description=ex_data.get('description', ''),
                            target_sets=str(ex_data.get('target_sets', '')),
                            target_reps=str(ex_data.get('target_reps', '')),
                            target_rest_seconds=ex_data.get('target_rest_seconds'),
                            target_weight_or_intensity=ex_data.get('target_weight_or_intensity', ''),
                            order=exercise_order
                        )

            # Successfully created plan for the week
            workout_request.status = WorkoutRequestStatus.COMPLETED_WEEK.name
            logger.info(
                f"TASK [generate_workout_for_specific_week]: Successfully processed plan for Request ID: {workout_request.id}, Week: {week_number}")

            if week_number >= workout_request.duration_weeks:
                workout_request.status = WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name
                workout_request.next_generation_due_date = None  # Mark as fully done

        else:  # LLM generation failed or returned unusable data (service function should have logged details)
            workout_request.status = WorkoutRequestStatus.FAILED_GENERATION.name
            # error_message should have been set by the service function when it returned None
            if not workout_request.error_message:  # Fallback error message
                workout_request.error_message = f"LLM generation failed for week {week_number}. Service returned no plan data."
            logger.error(
                f"TASK [generate_workout_for_specific_week]: LLM generation failed for Request ID: {workout_request.id}, Week: {week_number}. Error: {workout_request.error_message}")
            # Consider if this specific failure should trigger a retry.
            # If it's an OpenAI capacity issue, retry is good. If it's a bad prompt/data, retry might not help.
            # raise self.retry(exc=Exception(workout_request.error_message), countdown=15*60) # Example: retry after 15 mins for LLM issues

    except ValueError as ve:  # Catch ValueErrors like missing previous week plan
        logger.error(
            f"TASK [generate_workout_for_specific_week]: ValueError for Request ID {workout_request_id}, Week {week_number}: {ve}")
        workout_request.status = WorkoutRequestStatus.FAILED_GENERATION.name
        workout_request.error_message = f"Data integrity error for week {week_number}: {str(ve)}"
        # Do not retry indefinitely for data integrity issues, investigate manually.
    except Exception as e:  # Catch any other unexpected error
        logger.exception(
            f"TASK [generate_workout_for_specific_week]: Unexpected error for Request ID {workout_request_id}, Week {week_number}: {e}")
        workout_request.status = WorkoutRequestStatus.FAILED_GENERATION.name
        workout_request.error_message = f"Unexpected system error processing week {week_number}: {str(e)[:255]}"  # Truncate if too long
        # Retry for unexpected errors
        try:
            self.retry(exc=e)
        except Exception as retry_exc:  # Handle cases where retry might fail (e.g. max_retries exceeded)
            logger.error(
                f"TASK [generate_workout_for_specific_week]: Retry failed or max retries exceeded for Request ID {workout_request_id}, Week {week_number}: {retry_exc}")
    finally:
        # Always clear current_week_being_generated and save the final status
        if 'workout_request' in locals() and workout_request.pk:  # Ensure workout_request was fetched
            workout_request.current_week_being_generated = None
            workout_request.save(
                update_fields=['status', 'current_week_being_generated', 'error_message', 'next_generation_due_date'])

    return f"Finished processing Request {workout_request_id}, Week {week_number}. Final Status: {workout_request.status}"


@shared_task(name="workout.tasks.schedule_next_workout_week_generation")
def schedule_next_workout_week_generation():
    """
    Celery Beat task to find WorkoutRequests that are due for their next week's plan generation
    and trigger the `generate_workout_for_specific_week` task.
    """
    logger.info("CELERY BEAT [schedule_next_workout_week_generation]: Starting run.")
    today = timezone.now().date()
    processed_requests_count = 0

    # Query for requests that:
    # 1. Are not fully completed (ALL_WEEKS_COMPLETED).
    # 2. Are not currently being generated (GENERATING_WEEK) or failed (FAILED_GENERATION).
    #    Allow PENDING for initial trigger if API didn't, or COMPLETED_WEEK for subsequent weeks.
    # 3. Have a next_generation_due_date that is today or in the past.
    requests_due = WorkoutRequest.objects.filter(
        next_generation_due_date__lte=today
    ).exclude(
        status__in=[
            WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name,
            WorkoutRequestStatus.FAILED_GENERATION.name,
            # Don't auto-retry failed from here, needs manual check or different retry task
            WorkoutRequestStatus.GENERATING_WEEK.name  # Avoid race conditions if a task is already running
        ]
    )

    for req in requests_due:
        processed_requests_count += 1
        # Determine the next week number to generate
        last_completed_plan = WorkoutPlan.objects.filter(source_request=req).order_by('-week_number').first()
        next_week_to_generate = (last_completed_plan.week_number + 1) if last_completed_plan else 1

        if next_week_to_generate <= req.duration_weeks:
            logger.info(
                f"CELERY BEAT [schedule_next_workout_week_generation]: Request ID {req.id} due for Week {next_week_to_generate} generation.")

            # Double check if a plan for this week somehow already exists and is fine
            # This can happen if a manual trigger occurred or a previous beat run was interrupted.
            existing_plan = WorkoutPlan.objects.filter(source_request=req, week_number=next_week_to_generate).first()
            if existing_plan and existing_plan.visibility == WorkoutPlanVisibility.VISIBLE.name:
                logger.info(
                    f"CELERY BEAT [schedule_next_workout_week_generation]: Plan for Request ID {req.id}, Week {next_week_to_generate} already exists and is visible. Updating request status if needed.")
                if req.status != WorkoutRequestStatus.COMPLETED_WEEK.name:  # Should be completed week
                    req.status = WorkoutRequestStatus.COMPLETED_WEEK.name
                if next_week_to_generate >= req.duration_weeks:
                    req.status = WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name
                    req.next_generation_due_date = None
                else:  # Set next due date for the following week
                    req.next_generation_due_date = today + timezone.timedelta(
                        days=settings.WORKOUT_PLAN_GENERATION_INTERVAL_DAYS or 7)
                req.save()
                continue  # Move to next request

            generate_workout_for_specific_week.delay(req.id, next_week_to_generate)

            # Update next_generation_due_date for the *following* week, if there is one.
            # The task generate_workout_for_specific_week will update status to COMPLETED_WEEK or FAILED.
            # This beat task's role is just to trigger and set the *next* due date.
            if (next_week_to_generate + 1) <= req.duration_weeks:
                # Use a setting for interval, default to 7 days
                interval_days = getattr(settings, 'WORKOUT_PLAN_GENERATION_INTERVAL_DAYS', 7)
                req.next_generation_due_date = today + timezone.timedelta(days=interval_days)
            else:  # This was the last week to be triggered for generation
                req.next_generation_due_date = None  # Will be marked ALL_WEEKS_COMPLETED by the generation task
            req.save(update_fields=['next_generation_due_date'])

        elif req.status != WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name:
            # This means next_week_to_generate > req.duration_weeks, so all weeks should have been triggered.
            # Mark request as fully completed if not already.
            logger.info(
                f"CELERY BEAT [schedule_next_workout_week_generation]: All weeks for Request ID {req.id} should have been triggered. Marking as ALL_WEEKS_COMPLETED.")
            req.status = WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name
            req.next_generation_due_date = None
            req.save(update_fields=['status', 'next_generation_due_date'])

    logger.info(
        f"CELERY BEAT [schedule_next_workout_week_generation]: Finished run. Processed {processed_requests_count} requests.")
    return f"Processed {processed_requests_count} workout requests for next week generation."


@shared_task(name="workout.tasks.activate_upcoming_workout_plans")
def activate_upcoming_workout_plans():
    """
    Celery Beat task to find WorkoutPlans marked as UPCOMING whose plan_active_date
    is today or in the past, and change their visibility to VISIBLE.
    Also archives old VISIBLE plans.
    """
    logger.info("CELERY BEAT [activate_upcoming_workout_plans]: Starting run.")
    today = timezone.now().date()
    activated_count = 0
    archived_count = 0

    # Activate upcoming plans
    # Using select_for_update to prevent race conditions if multiple workers somehow pick this up, though unlikely for beat tasks.
    with transaction.atomic():
        upcoming_plans_qs = WorkoutPlan.objects.select_for_update().filter(
            visibility=WorkoutPlanVisibility.UPCOMING.name,
            plan_active_date__lte=today
        )
        for plan in upcoming_plans_qs:
            logger.info(
                f"CELERY BEAT [activate_upcoming_workout_plans]: Activating plan ID {plan.id} (Week {plan.week_number} for request {plan.source_request_id})")
            plan.visibility = WorkoutPlanVisibility.VISIBLE.name
            plan.save(update_fields=['visibility'])
            activated_count += 1

    # Archive old visible plans
    # A plan is considered "old" if its active period has passed.
    # Assuming a plan is active for 7 days from its plan_active_date.
    archive_threshold_date = today - timezone.timedelta(days=getattr(settings, 'WORKOUT_PLAN_ACTIVE_DURATION_DAYS', 7))
    with transaction.atomic():
        old_visible_plans_qs = WorkoutPlan.objects.select_for_update().filter(
            visibility=WorkoutPlanVisibility.VISIBLE.name,
            plan_active_date__lt=archive_threshold_date
            # If plan started before the threshold (e.g., more than 7 days ago)
        )
        for plan in old_visible_plans_qs:
            logger.info(f"CELERY BEAT [activate_upcoming_workout_plans]: Archiving plan ID {plan.id}")
            plan.visibility = WorkoutPlanVisibility.ARCHIVED.name
            plan.save(update_fields=['visibility'])
            archived_count += 1

    logger.info(
        f"CELERY BEAT [activate_upcoming_workout_plans]: Finished run. Activated {activated_count} plans, Archived {archived_count} plans.")
    return f"Activated {activated_count} plans, Archived {archived_count} plans."