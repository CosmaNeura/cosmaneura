from fastapi import status

from app.models.ai_visit_transcript import PydanticBaseModel


class EarlyResponseException(Exception):
    def __init__(
        self,
        *,
        data: dict | PydanticBaseModel,
        status: int = status.HTTP_200_OK,
    ):
        if isinstance(data, PydanticBaseModel):
            self.data = data.model_dump()
        else:
            self.data = data

        self.status = status
