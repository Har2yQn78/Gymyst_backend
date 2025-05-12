from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from unittest.mock import patch, MagicMock
from datetime import date, timedelta

from accounts.models import UserProfile, SexChoices, GoalChoices, FitnessLevelChoices
from .models import WorkoutRequest, WorkoutPlan, WorkoutDay, Exercise, WorkoutRequestStatus, WorkoutPlanVisibility, \
    ExerciseLog
from .schemas import WorkoutRequestCreateSchemaIn  # For payload creation
from .services import generate_workout_week_via_llm  # We will mock this
from .tasks import generate_workout_for_specific_week, schedule_next_workout_week_generation, \
    activate_upcoming_workout_plans

User = get_user_model()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class WorkoutFlowTests(TestCase):

    def setUp(self):
        # Create a test user and profile
        self.user = User.objects.create_user(
            email="testuser@example.com",
            username="testuser",
            name="Test",
            family_name="User",
            password="password123"
        )
        # UserProfile is created by signal, let's update it
        self.user_profile = UserProfile.objects.get(user=self.user)
        self.user_profile.sex = SexChoices.MALE.name
        self.user_profile.goal = GoalChoices.MUSCLE_GAIN.name
        self.user_profile.fitness_level = FitnessLevelChoices.INTERMEDIATE.name
        self.user_profile.birthday_date = date(1990, 1, 1)
        self.user_profile.save()

        # Ensure client is authenticated for API tests
        self.client.login(email="testuser@example.com", password="password123")  # If using session auth
        # If using JWT, you'd obtain a token and set it in headers for self.client

    @patch('workout.tasks.generate_workout_week_via_llm')  # Patch the service LLM call within the task
    def test_request_workout_plan_api_and_initial_task_trigger(self, mock_llm_service):
        """
        Test the API endpoint for requesting a workout plan and ensure the
        first week's generation task is triggered.
        """
        # Mock the LLM service to return successful structured data
        mock_llm_service.return_value = {
            "prompt_used": "Test prompt",
            "raw_llm_response": {"id": "fake_resp_id"},
            "parsed_plan": {
                "weekly_theme": "Test Week 1 Theme",
                "days": [
                    {
                        "day_number": 1, "day_title": "Push Day", "is_rest_day": False,
                        "exercises": [{"name": "Bench Press", "target_sets": "3", "target_reps": "10", "order": 1}]
                    },
                    # ... (add more days as needed for a full week, or mock a simpler structure)
                    {"day_number": 2, "day_title": "Rest", "is_rest_day": True, "exercises": []},
                    {"day_number": 3, "day_title": "Pull Day", "is_rest_day": False,
                     "exercises": [{"name": "Pull Ups", "target_sets": "3", "target_reps": "AMRAP", "order": 1}]}
                    # ... up to 7 days
                ]
            }
        }

        payload = {
            "duration_weeks": 4,
            "days_per_week_preference": 3,
            "specific_focus_areas": "Chest and Back"
        }

        # Use self.client to make API call if testing full API endpoint
        # For now, let's test the direct task triggering logic often found in API views

        # Simulate API endpoint logic (or call API endpoint using self.client)
        request_data = WorkoutRequestCreateSchemaIn(**payload)

        # API creates WorkoutRequest and triggers task
        # This part mimics what your API view does
        workout_req = WorkoutRequest.objects.create(
            user=self.user,
            duration_weeks=request_data.duration_weeks,
            days_per_week_preference=request_data.days_per_week_preference,
            fitness_level_at_request=self.user_profile.get_fitness_level_display(),
            primary_goal_at_request=self.user_profile.get_goal_display(),
            specific_focus_areas=request_data.specific_focus_areas or "",
            status=WorkoutRequestStatus.PENDING.name,
            next_generation_due_date=timezone.now().date()
        )

        # Manually call the task with .delay() which will run synchronously due to override_settings
        # In the actual API, this delay is called.
        generate_workout_for_specific_week.delay(workout_request_id=workout_req.id, week_number=1)

        workout_req.refresh_from_db()

        self.assertEqual(workout_req.status, WorkoutRequestStatus.COMPLETED_WEEK.name)
        self.assertEqual(WorkoutPlan.objects.filter(source_request=workout_req, week_number=1).count(), 1)

        week1_plan = WorkoutPlan.objects.get(source_request=workout_req, week_number=1)
        self.assertEqual(week1_plan.visibility, WorkoutPlanVisibility.VISIBLE.name)  # Assuming it starts today
        self.assertEqual(week1_plan.weekly_theme, "Test Week 1 Theme")
        self.assertTrue(WorkoutDay.objects.filter(workout_plan=week1_plan).exists())
        self.assertTrue(Exercise.objects.filter(workout_day__workout_plan=week1_plan).exists())

        # Verify the mock was called
        mock_llm_service.assert_called_once_with(workout_req, 1)

    @patch('workout.tasks.generate_workout_week_via_llm')
    def test_schedule_next_week_generation_beat_task(self, mock_llm_service):
        """
        Test the Celery Beat task that schedules subsequent week generations.
        """
        # --- Setup: Create a request with Week 1 already completed ---
        workout_req = WorkoutRequest.objects.create(
            user=self.user,
            duration_weeks=2,  # Only 2 weeks for this test
            days_per_week_preference=3,
            status=WorkoutRequestStatus.COMPLETED_WEEK.name,  # Week 1 is done
            # Week 2 generation is due today
            next_generation_due_date=timezone.now().date(),
            current_week_being_generated=None  # Week 1 processing finished
        )
        # Create a dummy WorkoutPlan for week 1
        WorkoutPlan.objects.create(
            source_request=workout_req, user=self.user, week_number=1,
            plan_active_date=timezone.now().date() - timedelta(days=7),  # Week 1 started last week
            visibility=WorkoutPlanVisibility.ARCHIVED.name  # Week 1 is now old
        )

        # Mock LLM service for Week 2
        mock_llm_service.return_value = {
            "prompt_used": "Test prompt week 2",
            "raw_llm_response": {"id": "fake_resp_id_w2"},
            "parsed_plan": {"weekly_theme": "Test Week 2 Theme", "days": [{"day_number": 1, "exercises": []}]}
            # Simplified
        }

        # Call the beat task function directly (it's a regular Python function)
        schedule_next_workout_week_generation()

        workout_req.refresh_from_db()
        # The generate_workout_for_specific_week task (for week 2) should have run due to CELERY_TASK_ALWAYS_EAGER
        self.assertEqual(workout_req.status,
                         WorkoutRequestStatus.ALL_WEEKS_COMPLETED.name)  # Because duration was 2 weeks
        self.assertIsNone(workout_req.next_generation_due_date)

        self.assertEqual(WorkoutPlan.objects.filter(source_request=workout_req, week_number=2).count(), 1)
        week2_plan = WorkoutPlan.objects.get(source_request=workout_req, week_number=2)
        # Week 2 is upcoming as its plan_active_date will be in the future
        self.assertEqual(week2_plan.visibility, WorkoutPlanVisibility.UPCOMING.name)

        # mock_llm_service should have been called for week 2
        # The call stack is: schedule_next_... -> generate_workout_for_specific_week.delay -> (mocked service)
        mock_llm_service.assert_called_once_with(workout_req, 2)

    def test_activate_upcoming_workout_plans_beat_task(self):
        """
        Test the Celery Beat task that activates upcoming plans and archives old ones.
        """
        # --- Setup ---
        workout_req = WorkoutRequest.objects.create(user=self.user, duration_weeks=2, days_per_week_preference=3)

        # Plan 1: Should become VISIBLE
        plan1_active_date = timezone.now().date() - timedelta(days=1)  # Started yesterday
        WorkoutPlan.objects.create(
            source_request=workout_req, user=self.user, week_number=1,
            plan_active_date=plan1_active_date,
            visibility=WorkoutPlanVisibility.UPCOMING.name
        )

        # Plan 2: Should remain UPCOMING
        plan2_active_date = timezone.now().date() + timedelta(days=3)  # Starts in 3 days
        WorkoutPlan.objects.create(
            source_request=workout_req, user=self.user, week_number=2,
            plan_active_date=plan2_active_date,
            visibility=WorkoutPlanVisibility.UPCOMING.name
        )

        # Plan 3: Was VISIBLE, should become ARCHIVED
        # Assuming WORKOUT_PLAN_ACTIVE_DURATION_DAYS is 7 (default in task)
        plan3_active_date = timezone.now().date() - timedelta(days=10)  # Started 10 days ago
        WorkoutPlan.objects.create(
            source_request=workout_req, user=self.user, week_number=0,  # Dummy old plan
            plan_active_date=plan3_active_date,
            visibility=WorkoutPlanVisibility.VISIBLE.name
        )

        # Call the beat task function
        activate_upcoming_workout_plans()

        plan1_updated = WorkoutPlan.objects.get(week_number=1, source_request=workout_req)
        plan2_updated = WorkoutPlan.objects.get(week_number=2, source_request=workout_req)
        plan3_updated = WorkoutPlan.objects.get(week_number=0, source_request=workout_req)

        self.assertEqual(plan1_updated.visibility, WorkoutPlanVisibility.VISIBLE.name)
        self.assertEqual(plan2_updated.visibility, WorkoutPlanVisibility.UPCOMING.name)
        self.assertEqual(plan3_updated.visibility, WorkoutPlanVisibility.ARCHIVED.name)

    @patch('workout.services.openai.ChatCompletion.create')  # More specific patch for actual OpenAI call
    def test_generate_workout_task_llm_failure(self, mock_openai_create):
        """
        Test the generate_workout_for_specific_week task when the LLM service fails.
        """
        mock_openai_create.side_effect = Exception("OpenAI API Error")  # Simulate an API error

        workout_req = WorkoutRequest.objects.create(
            user=self.user, duration_weeks=1, days_per_week_preference=3,
            status=WorkoutRequestStatus.PENDING.name,
            next_generation_due_date=timezone.now().date()
        )

        generate_workout_for_specific_week.delay(workout_request_id=workout_req.id, week_number=1)

        workout_req.refresh_from_db()
        self.assertEqual(workout_req.status, WorkoutRequestStatus.FAILED_GENERATION.name)
        self.assertIn("OpenAI API Error", workout_req.error_message)  # Or whatever error message your service sets
        self.assertEqual(WorkoutPlan.objects.filter(source_request=workout_req).count(), 0)

    def test_exercise_logging_api(self):  # Example of an API test
        """ Test creating an exercise log via API """
        # First, create a planned exercise for the user
        workout_req = WorkoutRequest.objects.create(user=self.user, duration_weeks=1, days_per_week_preference=1)
        plan = WorkoutPlan.objects.create(source_request=workout_req, user=self.user, week_number=1,
                                          plan_active_date=date.today())
        day = WorkoutDay.objects.create(workout_plan=plan, day_sequence_number=1, calendar_date=date.today())
        exercise = Exercise.objects.create(workout_day=day, name="Test Squats", target_sets="3", target_reps="5")

        log_payload = {
            "planned_exercise_id": exercise.id,
            "sets_data": [
                {"set_number": 1, "reps": 5, "weight": 100.0},
                {"set_number": 2, "reps": 5, "weight": 100.0},
                {"set_number": 3, "reps": 4, "weight": 100.0}
            ],
            "log_date": str(date.today())  # Ensure string format for JSON
        }

        # Assuming your main API is at /api/ and workout controller at /workout/
        # Make sure you have client authenticated if needed (self.client.login or JWT header)
        response = self.client.post("/api/workout/log-exercise", data=log_payload, content_type="application/json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(ExerciseLog.objects.filter(user=self.user, planned_exercise=exercise).exists())
        log_entry = ExerciseLog.objects.get(user=self.user, planned_exercise=exercise)
        self.assertEqual(log_entry.total_sets_done, 3)
        self.assertEqual(log_entry.total_reps_done, 14)
