from fastapi import FastAPI
from src.journeys.routes import journey_router



version = "v1"


app = FastAPI(
    title = "My Journey Music",
    description = "Backend API for My Journey Music",
    version = version,
)

app.include_router(journey_router, prefix=f"/api/{version}/journeys", tags=['journeys'])