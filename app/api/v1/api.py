from fastapi import APIRouter

from app.api.v1.routers import auth, categories, dashboard, expenses, friends, groups, health, users

api_router = APIRouter()

api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(categories.router, prefix="/categories", tags=["Categories"])
api_router.include_router(friends.router, prefix="/friends", tags=["Friends"])
api_router.include_router(expenses.router, prefix="/expenses", tags=["Expenses"])
api_router.include_router(groups.router, prefix="/groups", tags=["Groups"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])

# Optional backward compatibility alias for Phase 1 scaffolding
api_router.add_api_route(
    "/me",
    users.get_current_user_profile,
    methods=["GET"],
    include_in_schema=False,
)
