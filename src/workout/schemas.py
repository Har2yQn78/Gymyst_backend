from ninja import Schema, ModelSchema
from pydantic import Field, validator, BaseModel
from typing import List, Optional, Dict, Any
from datetime import date, datetime

from .models import (WorkoutRequest, WorkoutPlan, WorkoutDay, Exercise, ExerciseLog)
from accounts.schemas import UserSchemaOut

class WorkoutRequestCreateSchemaIn(Schema):
    duration_weeks: int = Field(..., ge=1, le=12, description="Number of weeks for the workout plan (e.g., 4)")
    days_per_week_preference: int = Field(..., ge=1, le=7, description="Preferred number of workout days per week")
    fitness_level_override: Optional[str] = Field(None,
                                                  description="Override profile fitness level (e.g., Beginner, Intermediate)")
    primary_goal_override: Optional[str] = Field(None, description="Override profile primary goal (e.g., Muscle Gain)")
    specific_focus_areas: Optional[str] = Field(None, description="Specific areas or exercises to focus on")


class ExerciseLogSetDataSchema(BaseModel):  # Using pydantic.BaseModel for deeply nested non-Django model structures
    set_number: int = Field(..., ge=1)
    reps: int = Field(..., ge=0)
    weight: float = Field(..., ge=0)
    # rpe: Optional[int] = Field(None, ge=1, le=10) # Example of another per-set metric


class ExerciseLogCreateSchemaIn(Schema):
    planned_exercise_id: Optional[int] = None
    exercise_name_logged: Optional[str] = None

    sets_data: List[ExerciseLogSetDataSchema] = Field(..., min_items=1)
    log_date: date = Field(default_factory=date.today)
    # If you want to link to the specific WorkoutDay instance from the plan:
    workout_day_completed_id: Optional[int] = None
    notes: Optional[str] = None

    @validator('exercise_name_logged', always=True)
    def check_exercise_name_if_no_id(cls, v, values):
        if not values.get('planned_exercise_id') and not v:
            raise ValueError('exercise_name_logged is required if planned_exercise_id is not provided.')
        if values.get('planned_exercise_id') and v:
            # Optional: you might want to disallow both, or let exercise_name_logged override
            pass  # Or raise ValueError("Provide either planned_exercise_id or exercise_name_logged, not both.")
        return v


# --- Output Schemas (using ModelSchema for Django models) ---

class ExerciseSchemaOut(ModelSchema):
    class Config:
        model = Exercise
        model_fields = [
            'id', 'name', 'description', 'target_sets', 'target_reps',
            'target_rest_seconds', 'target_weight_or_intensity', 'media_url', 'order'
        ]


class WorkoutDaySchemaOut(ModelSchema):
    exercises: List[ExerciseSchemaOut]  # Nested list of exercises
    day_of_week_display: Optional[str] = None  # Custom resolved field

    class Config:
        model = WorkoutDay
        model_fields = [
            'id', 'day_sequence_number', 'calendar_date',
            'day_title', 'is_rest_day', 'notes'
            # 'exercises' is handled by the explicit declaration above
        ]

    @staticmethod  # Or @field_validator for Pydantic v2 style if you want to pre-calculate for schema instance
    def resolve_day_of_week_display(obj: WorkoutDay) -> Optional[str]:
        if obj.calendar_date:
            return obj.calendar_date.strftime("%A")  # e.g., "Monday"
        return None


class WorkoutPlanSchemaOut(ModelSchema):
    days: List[WorkoutDaySchemaOut]  # Nested list of workout days
    # To get Django's get_FOO_display for choice fields:
    visibility_display: str = Field(alias="get_visibility_display")

    class Config:
        model = WorkoutPlan
        model_fields = [
            'id', 'week_number', 'plan_active_date',
            'visibility',  # This will output the raw value (e.g., "VISIBLE")
            'weekly_theme'
            # 'days' handled by explicit declaration
            # 'visibility_display' handled by alias
        ]
        # Exclude sensitive or overly verbose fields like 'user', 'source_request',
        # 'generation_prompt', 'llm_response_raw' from default API output.
        # They are for internal use or specific admin/debug views.


class WorkoutRequestSchemaOut(ModelSchema):
    user: UserSchemaOut  # Nested user information
    status_display: str = Field(alias="get_status_display")

    # Optional: If you want to include a summary of generated plans directly here
    # generated_plans_summary: Optional[List[WorkoutPlanSchemaOut]] = None
    # This would require a resolver or custom handling if not a direct model relation.

    class Config:
        model = WorkoutRequest
        model_fields = [
            'id',
            # 'user', # Handled by explicit declaration for nesting
            'requested_at', 'duration_weeks', 'days_per_week_preference',
            'fitness_level_at_request', 'primary_goal_at_request', 'specific_focus_areas',
            'status',  # Raw status value
            # 'status_display', # Handled by alias
            'current_week_being_generated', 'error_message', 'next_generation_due_date'
        ]


class ExerciseLogSchemaOut(ModelSchema):
    user: UserSchemaOut  # Nested user details
    planned_exercise: Optional[ExerciseSchemaOut] = None  # Nested details of the planned exercise

    # Explicitly declare properties from the model that you want in the schema
    total_reps_done: int
    total_sets_done: int

    # If workout_day_completed should be a nested object:
    # workout_day_completed: Optional[WorkoutDaySchemaOut] = None
    # Otherwise, if you just want the ID, it will be workout_day_completed_id by default from the FK.

    class Config:
        model = ExerciseLog
        model_fields = [
            'id',
            # 'user', # Handled by explicit declaration
            # 'planned_exercise', # Handled by explicit declaration
            'exercise_name_logged',
            'sets_data',  # This is a JSONField on the model, will be output as is
            'log_date',
            'workout_day_completed',  # Refers to the ForeignKey field on the model
            # Ninja will output its ID by default if not nested.
            'notes',
            'created_at',
            # 'total_reps_done', # Handled by explicit field declaration above
            # 'total_sets_done'  # Handled by explicit field declaration above
        ]
        # To ensure that even if a property isn't explicitly listed in model_fields,
        # but is defined on the schema, it gets included, you can also rely on Pydantic behavior.
        # However, being explicit in model_fields for ModelSchema is usually clearer for direct model attributes.


# --- Schemas for Paginated Responses (Optional but good practice) ---

class PaginatedWorkoutRequests(Schema):
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[WorkoutRequestSchemaOut]


class PaginatedWorkoutLogs(Schema):
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[ExerciseLogSchemaOut]


class PaginatedWorkoutPlans(Schema):
    count: int
    next: Optional[str] = None
    previous: Optional[str] = None
    results: List[WorkoutPlanSchemaOut]

class ErrorDetailSchema(Schema):
    detail: str

class MessageResponseSchema(Schema):
    message: str