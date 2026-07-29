"""
Mirrors this model: https://clerk.com/docs/reference/backend-api/tag/Users#operation/GetUser
"""

# TODO should we create an organization?
# https://clerk.com/docs/reference/backend-api/tag/Organizations#operation/GetOrganization

from datetime import datetime
from enum import Enum

from typeid import TypeID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

# NOTE we use usr_ for our prefix to avoid confusion
CLERK_OBJECT_PREFIX = "user_"

# let's do it Stripe style :)
API_KEY_PREFIX = "sk_live"


class DoctorRole(str, Enum):
    normal = "normal"
    admin = "admin"


class Terminology(str, Enum):
    """
    Role-label pair used everywhere the app names the provider/patient
    relationship (UI, generated notes, intake language). Add new pairs here
    and to the frontend label catalog; never scatter per-surface overrides.
    """

    doctor_patient = "doctor_patient"
    therapist_client = "therapist_client"
    counselor_client = "counselor_client"
    nutritionist_client = "nutritionist_client"


# usr vs user is intentionally used to differentiate from the clerk model, which also uses a prefix ID
class Doctor(
    BaseModel, TimestampsMixin, SoftDeletionMixin, TypeIDMixin("doctor"), table=True
):
    clerk_id: str = Field(unique=True, index=True)
    "external ID of the user in Clerk"

    email: str = Field(nullable=True, default=None, unique=True)
    "email address of the user in Clerk, makes it easy to debug and find users, set async after the user is created"

    role: DoctorRole = Field(default=DoctorRole.normal)
    "role of the user, primarily to support superuser switching"

    last_active_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "last time the user had an active session"

    # organization_id: str

    api_key: TypeIDType | None = Field(
        sa_column=Column(
            TypeIDType(API_KEY_PREFIX), nullable=True, unique=True, index=True
        ),
        default=None,
    )

    biography: str | None
    "what type of doctor is this, what specialist, what they focus on, etc. Used for the intake process."

    terminology: Terminology = Field(default=Terminology.doctor_patient)
    "which provider/patient role labels this doctor sees and generates content with"

    onboarding_completed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "set to now() when the doctor completes the onboarding wizard; null means onboarding not yet done"

    def generate_api_key(self):
        api_key = TypeID(API_KEY_PREFIX)

        self.api_key = api_key  # type: ignore
        self.save()

        return api_key

    def before_save(self):
        if not self.clerk_id or not self.clerk_id.startswith(CLERK_OBJECT_PREFIX):
            raise ValueError(
                f"clerk_id must start with {CLERK_OBJECT_PREFIX}, got {self.clerk_id}"
            )

    # TODO should also check to ensure the value does not change, I think sqlalchemy stores this state somewhere?
