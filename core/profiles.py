"""Validate editable JSON before using it in prompts."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Communication(Profile):
    language: str = "fr"
    address_user_as: list[str] = Field(default_factory=list)
    concise: bool = False


class IdentityData(Profile):
    name: str = "A.I.R.I."
    identity: str = ""
    role: str = ""
    values: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    communication: Communication = Field(default_factory=Communication)


class Humor(Profile):
    enabled: bool = False
    style: str = "subtil"


class PersonalityData(Profile):
    tone: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    humor: Humor = Field(default_factory=Humor)
    traits: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)
    behavior: list[str] = Field(default_factory=list)


class MoodData(Profile):
    mood: str = "calm"
    energy: float = Field(default=0.5, ge=0, le=1)
    humor: float = Field(default=0.5, ge=0, le=1)
    verbosity: float = Field(default=0.5, ge=0, le=1)


class RelationshipData(Profile):
    familiarity: float = Field(default=0.0, ge=0, le=1)
    interaction_style: str = "casual"
    shared_experiences: list[str] = Field(default_factory=list, max_length=50)
    conversation_preferences: list[str] = Field(default_factory=list, max_length=50)
    recurring_topics: list[str] = Field(default_factory=list, max_length=50)
    important_moments: list[str] = Field(default_factory=list, max_length=50)
