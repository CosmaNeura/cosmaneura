from app import log

from app.models.doctor import Doctor
from app.models.visit_audio_file import VisitAudioFile


def perform(doctor: Doctor, blob_name: str) -> None:
    file = VisitAudioFile(doctor_id=doctor.id, azure_file_name=blob_name).save()

    # TODO kick off job
    log.info("processing audio file", id=file.id)
