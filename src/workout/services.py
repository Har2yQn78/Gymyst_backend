import openai
from openai import OpenAI, APIError, RateLimitError, OpenAIError
import os
from django.conf import settings
import json
from .models import WorkoutRequest, WorkoutPlan, WorkoutDay, Exercise, WorkoutPlanVisibility
from accounts.models import UserProfile
from decouple import config, AutoConfig

# --- Configuration for OpenRouter ---
# This part for config loading is assumed to be correct as per your original file
config_search_path = settings.BASE_DIR if hasattr(settings, 'BASE_DIR') else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
config_loader = AutoConfig(search_path=config_search_path)  # Renamed to avoid conflict with 'config' function

OPENROUTER_API_KEY = config_loader("OPENROUTER_API_KEY", default=None)
OPENROUTER_API_BASE = config_loader("OPENROUTER_API_BASE", default="https://openrouter.ai/api/v1")
LLM_MODEL_NAME = config_loader("LLM_MODEL_NAME", default="deepseek/deepseek-chat-v3-0324:free")  # Example model

# --- Initialize OpenRouter Client ---
openrouter_client = None
if not OPENROUTER_API_KEY:
    print("ERROR: OPENROUTER_API_KEY not found in environment. Workout generation will fail.")
else:
    try:
        openrouter_client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_API_BASE,
            # You can add http_client, timeout, max_retries if needed, similar to AsyncOpenAI example
        )
        print(
            f"OpenRouter client initialized successfully for model {LLM_MODEL_NAME} via base URL {OPENROUTER_API_BASE}")
    except Exception as e:
        print(f"ERROR: Failed to initialize OpenRouter client: {e}")
        openrouter_client = None


def _get_user_profile_summary(user_profile: UserProfile) -> str:
    """Generates a concise text summary of the user's profile for the LLM."""
    summary_parts = []
    if user_profile.age:
        summary_parts.append(f"Age: {user_profile.age}")
    if user_profile.sex:
        summary_parts.append(f"Sex: {user_profile.get_sex_display()}")
    if user_profile.weight:
        summary_parts.append(f"Weight: {user_profile.weight} kg")  # Assuming kg
    if user_profile.height:
        summary_parts.append(f"Height: {user_profile.height} cm")  # Assuming cm
    return ", ".join(summary_parts)


def _construct_llm_prompt(workout_request: WorkoutRequest, week_number: int, user_profile: UserProfile) -> str:
    """
    Constructs the detailed prompt for the OpenAI LLM.
    This is the core of the "prompt engineering".
    """
    profile_summary = _get_user_profile_summary(user_profile)
    fitness_level = workout_request.fitness_level_at_request or user_profile.get_fitness_level_display() or "Not Specified"
    primary_goal = workout_request.primary_goal_at_request or user_profile.get_goal_display() or "General Fitness"

    plan_context = f"This is Week {week_number} of a {workout_request.duration_weeks}-week workout program."
    if week_number > 1:
        plan_context += " Please ensure this week's plan offers progression or variation from a typical previous week."

    days_per_week = workout_request.days_per_week_preference
    structure_request = (
        f"The user wants to work out {days_per_week} days this week. "
        f"Distribute these workout days appropriately throughout a 7-day week, including rest days. "
        f"The user's primary fitness goal is: '{primary_goal}'. "
        f"Their current fitness level is: '{fitness_level}'. "
    )
    if workout_request.specific_focus_areas:
        structure_request += f"They also have specific focus areas for this plan: '{workout_request.specific_focus_areas}'. Prioritize these."

    output_format_instruction = """
Please provide the workout plan in a strict JSON format. The JSON object should have a main key "weekly_plan".
The "weekly_plan" object should contain:
1.  "weekly_theme": A short, motivational theme for the week (e.g., "Strength Building Phase 1", "Endurance Focus").
2.  "days": An array of 7 objects, one for each day of the week (Day 1 to Day 7, where Day 1 is the start of their workout week). 
    Each day object must have:
    a. "day_number": (Integer, 1-7) The day number in the week.
    b. "day_title": (String) A descriptive title for the day (e.g., "Upper Body Strength", "Active Recovery", "Full Body Circuit", "Rest Day").
    c. "is_rest_day": (Boolean) True if it's a rest day, false otherwise.
    d. "notes": (String, Optional) Any general notes or instructions for the day.
    e. "exercises": An array of exercise objects. This array should be empty if "is_rest_day" is true. 
       Each exercise object must have:
       i.   "name": (String) The name of the exercise (e.g., "Barbell Squats", "Push-ups").
       ii.  "description": (String, Optional) Brief instructions or tips for the exercise.
       iii. "target_sets": (String) Target sets, e.g., "3" or "3-4".
       iv.  "target_reps": (String) Target reps, e.g., "8-12" or "15".
       v.   "target_rest_seconds": (Integer, Optional) Rest time in seconds between sets (e.g., 60, 90).
       vi.  "target_weight_or_intensity": (String, Optional) e.g., "RPE 8", "70% 1RM", "Bodyweight", "Use challenging weight".
       vii. "order": (Integer) The sequence of the exercise for the day, starting from 1.

Ensure the output is ONLY the JSON object, with no preceding or succeeding text.
Validate the JSON structure carefully before outputting. For example:
{
  "weekly_plan": {
    "weekly_theme": "Example Theme",
    "days": [
      {
        "day_number": 1,
        "day_title": "Chest & Triceps",
        "is_rest_day": false,
        "notes": "Focus on controlled movements.",
        "exercises": [
          {
            "name": "Bench Press",
            "description": "Lie on a bench...",
            "target_sets": "3-4",
            "target_reps": "8-12",
            "target_rest_seconds": 90,
            "target_weight_or_intensity": "RPE 7-8",
            "order": 1
          }
        ]
      },
      {
        "day_number": 2,
        "day_title": "Rest Day",
        "is_rest_day": true,
        "notes": "Hydrate and recover.",
        "exercises": []
      }
    ]
  }
}
"""
    full_prompt = f"""
You are an expert AI fitness coach. Your task is to generate a personalized weekly workout plan.

User Profile Summary: {profile_summary}
User Fitness Level: {fitness_level}
User Primary Goal: {primary_goal}

Plan Context: {plan_context}
Requested Workout Structure: {structure_request}

Output Format Instructions:
{output_format_instruction}

Generate the workout plan now.
"""
    return full_prompt


# --- Main Service Function ---
def generate_workout_week_via_llm(workout_request: WorkoutRequest, week_number: int) -> dict | None:
    """
    Generates a workout plan for a specific week using an LLM via OpenRouter,
    parses the response, and prepares data for model creation.
    """
    try:
        user_profile = UserProfile.objects.get(user=workout_request.user)
    except UserProfile.DoesNotExist:
        workout_request.error_message = "User profile not found."
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: UserProfile not found for user {workout_request.user.id}")
        return None

    if not openrouter_client:
        error_msg = "OpenRouter client is not initialized. Check API key and logs."
        workout_request.error_message = error_msg
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: {error_msg} for Request ID {workout_request.id}, Week {week_number}")
        return None

    prompt = _construct_llm_prompt(workout_request, week_number, user_profile)

    print(
        f"Attempting to generate workout via OpenRouter for Request ID: {workout_request.id}, Week: {week_number} using model: {LLM_MODEL_NAME}")
    try:
        chat_completion = openrouter_client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=[
                {"role": "system",
                 "content": "You are an expert AI fitness coach designing personalized workout plans."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}  # Request JSON response from compatible models
            # max_tokens=2048 # Optional: Set if needed, OpenRouter models might have different defaults/limits
        )

        raw_response_content = chat_completion.choices[0].message.content

        print(f"LLM (OpenRouter) Raw Response for Request ID {workout_request.id}, Week {week_number}:")
        # print(raw_response_content) # Be cautious with large responses in logs

        parsed_plan_data = None
        if raw_response_content:
            # Attempt to parse the JSON response (keeping existing robust parsing)
            if "```json" in raw_response_content:
                # Extract JSON from markdown code block
                json_str = raw_response_content.split("```json\n", 1)[1].split("\n```", 1)[0]
            elif raw_response_content.strip().startswith("{") and raw_response_content.strip().endswith("}"):
                json_str = raw_response_content.strip()
            else:
                # Fallback if no clear markers or if response_format={"type": "json_object"} already cleaned it
                json_str = raw_response_content

            try:
                data = json.loads(json_str)
                if "weekly_plan" in data and "days" in data["weekly_plan"] and isinstance(data["weekly_plan"]["days"],
                                                                                          list):
                    parsed_plan_data = data["weekly_plan"]
                    print(
                        f"Successfully parsed LLM (OpenRouter) JSON response for Request ID {workout_request.id}, Week {week_number}")
                else:
                    raise ValueError(
                        "Parsed JSON does not match expected structure: 'weekly_plan' or 'days' key missing/invalid.")
            except json.JSONDecodeError as e:
                workout_request.error_message = f"Failed to parse LLM (OpenRouter) JSON for week {week_number}: {e}. Response: {raw_response_content[:500]}"
                workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
                workout_request.save()
                print(f"Service Error: JSONDecodeError for Request ID {workout_request.id}, Week {week_number}: {e}")
                return None
            except ValueError as e:
                workout_request.error_message = f"LLM (OpenRouter) response structure error for week {week_number}: {e}. Response: {raw_response_content[:500]}"
                workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
                workout_request.save()
                print(
                    f"Service Error: ValueError (structure) for Request ID {workout_request.id}, Week {week_number}: {e}")
                return None
        else:
            workout_request.error_message = f"LLM (OpenRouter) returned empty response for week {week_number}."
            workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
            workout_request.save()
            print(f"Service Error: Empty response from LLM for Request ID {workout_request.id}, Week {week_number}")
            return None

        return {
            "prompt_used": prompt,
            "raw_llm_response": chat_completion.model_dump(),  # Use model_dump() for openai >v1.0.0
            "parsed_plan": parsed_plan_data
        }

    except RateLimitError as e:
        error_msg = f"OpenRouter Rate Limit error for week {week_number}: {type(e).__name__} - {str(e)}"
        workout_request.error_message = error_msg
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: {error_msg} for Request ID {workout_request.id}")
        return None
    except APIError as e:  # Catch other API related errors
        error_msg = f"OpenRouter API error for week {week_number}: {type(e).__name__} - {str(e)}"
        workout_request.error_message = error_msg
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: {error_msg} for Request ID {workout_request.id}")
        return None
    except OpenAIError as e:  # Catch broader OpenAI SDK errors if not covered above
        error_msg = f"OpenRouter/OpenAI SDK error for week {week_number}: {type(e).__name__} - {str(e)}"
        workout_request.error_message = error_msg
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: {error_msg} for Request ID {workout_request.id}")
        return None
    except Exception as e:
        error_msg = f"Unexpected error generating plan (OpenRouter) for week {week_number}: {type(e).__name__} - {str(e)}"
        workout_request.error_message = error_msg
        workout_request.status = WorkoutRequest.StatusChoices.FAILED_GENERATION
        workout_request.save()
        print(f"Service Error: {error_msg} for Request ID {workout_request.id}")
        # import traceback # For more detailed debugging if needed
        # print(traceback.format_exc())
        return None