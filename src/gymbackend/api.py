from ninja_extra import NinjaExtraAPI
from ninja_jwt.controller import NinjaJWTDefaultController
from accounts.api import auth_router as accounts_auth_router # Assuming this is still a Ninja Router
from accounts.api import profile_router as accounts_profile_router # Assuming this is still a Ninja Router
from subscription.api import SubscriptionController, PaymentCallbackController

api = NinjaExtraAPI(version="1.0.0", csrf=True)

api.add_router("/auth", accounts_auth_router, tags=["Authentication"])
api.add_router("/users", accounts_profile_router, tags=["User & Profile"])
api.register_controllers(SubscriptionController, PaymentCallbackController)

api.register_controllers(NinjaJWTDefaultController)
