from ninja import Router
from ninja.errors import HttpError
from django.contrib.auth import authenticate
from enum import Enum
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.tokens import RefreshToken

from .models import User, UserProfile
from .schemas import (
    UserCreateSchemaIn, UserSchemaOut, AuthResponseSchema, LoginPayload,
    ProfileUpdateSchemaIn, ProfileSchemaOut, UserWithProfileResponse,
    ErrorDetail
)

auth_router = Router()
profile_router = Router(auth=JWTAuth())


@auth_router.post("/signup", response={201: AuthResponseSchema, 400: ErrorDetail})
def signup(request, payload: UserCreateSchemaIn):
    if User.objects.filter(email=payload.email).exists():
        raise HttpError(400, "Email already registered.")
    if User.objects.filter(username=payload.username).exists():
        raise HttpError(400, "Username already taken.")

    user = User.objects.create_user(
        email=payload.email,
        username=payload.username,
        name=payload.name,
        family_name=payload.family_name,
        password=payload.password
    )

    refresh = RefreshToken.for_user(user)
    tokens = {"access": str(refresh.access_token), "refresh": str(refresh)}
    user_out = UserSchemaOut.from_orm(user)
    return 201, AuthResponseSchema(user=user_out, tokens=tokens)


@auth_router.post("/login", response={200: AuthResponseSchema, 401: ErrorDetail})
def custom_login(request, payload: LoginPayload):
    user = authenticate(request, username=payload.email, password=payload.password)
    if user is not None:
        refresh = RefreshToken.for_user(user)
        tokens = {"access": str(refresh.access_token), "refresh": str(refresh)}
        user_out = UserSchemaOut.from_orm(user)
        return 200, AuthResponseSchema(user=user_out, tokens=tokens)
    else:
        raise HttpError(401, "Invalid credentials")


@profile_router.get("/profile", response={200: UserWithProfileResponse, 404: ErrorDetail})
def get_user_profile(request):
    user = request.auth
    try:
        profile = UserProfile.objects.select_related('user').get(user=user)
    except UserProfile.DoesNotExist:
        raise HttpError(404, "User profile not found.")

    user_data = UserSchemaOut.from_orm(user)
    profile_data_for_schema = {
        "city": profile.city,
        "birthday_date": profile.birthday_date,
        "sex": profile.get_sex_display() if profile.sex else None,
        "goal": profile.get_goal_display() if profile.goal else None,
        "fitness_level": profile.get_fitness_level_display() if profile.fitness_level else None,
        "height": profile.height,
        "weight": profile.weight,
        "age": profile.age,
    }
    profile_out = ProfileSchemaOut(**profile_data_for_schema)
    return 200, UserWithProfileResponse(user=user_data, profile=profile_out)


@profile_router.put("/profile", response={200: ProfileSchemaOut, 400: ErrorDetail, 404: ErrorDetail})
def update_user_profile(request, payload: ProfileUpdateSchemaIn):
    user = request.auth
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        raise HttpError(404, "User profile not found to update.")

    updated_fields_count = 0
    for attr, value in payload.dict(exclude_unset=True).items():
        if value is not None:
            if hasattr(value, 'name') and isinstance(value, Enum):
                setattr(profile, attr, value.name)
            else:
                setattr(profile, attr, value)
            updated_fields_count += 1

    if updated_fields_count > 0:
        profile.save()

    profile_data_for_schema = {
        "city": profile.city,
        "birthday_date": profile.birthday_date,
        "sex": profile.get_sex_display() if profile.sex else None,
        "goal": profile.get_goal_display() if profile.goal else None,
        "fitness_level": profile.get_fitness_level_display() if profile.fitness_level else None,
        "height": profile.height,
        "weight": profile.weight,
        "age": profile.age,
    }
    return 200, ProfileSchemaOut(**profile_data_for_schema)