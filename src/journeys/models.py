from pydantic import BaseModel
from typing import List


class JourneyRequest(BaseModel):
    origin: str
    destination: str


class PlaylistRequest(BaseModel):
    genres: List[str]
    journey_id: int