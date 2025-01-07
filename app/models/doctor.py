"""
Mirrors this model: https://clerk.com/docs/reference/backend-api/tag/Users#operation/GetUser
"""

# TODO should we create an organization?
# https://clerk.com/docs/reference/backend-api/tag/Organizations#operation/GetOrganization

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin

# NOTE we use usr_ for our prefix to avoid confusion
CLERK_OBJECT_PREFIX = "user_"


# usr vs user is intentionally used to differentiate from the clerk model, which also uses a prefix ID
class Doctor(
    BaseModel, TimestampsMixin, SoftDeletionMixin, TypeIDMixin("doctor"), table=True
):
    clerk_id: str

    # organization_id: str

    def before_save(self):
        if not self.clerk_id.startswith(CLERK_OBJECT_PREFIX):
            raise ValueError(
                f"clerk_id must start with {CLERK_OBJECT_PREFIX}, got {self.clerk_id}"
            )

    # TODO should also check to ensure the value does not change, I think sqlalchemy stores this state somewhere?
