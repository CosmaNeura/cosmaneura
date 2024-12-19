from app import log

from app.models.user import User
from app.models.visit_audio_file import VisitAudioFile


def perform(user: User, blob_name: str) -> None:
    file = VisitAudioFile(user_id=user.id, azure_file_name=blob_name).save()

    # TODO kick off job
    log.info("processing audio file", id=file.id)
