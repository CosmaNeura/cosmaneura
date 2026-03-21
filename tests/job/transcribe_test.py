# pyright: reportReturnType=false

from app.jobs.transcribe import perform, transcribe_audio

from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.visit_audio_file import VisitAudioFile


def count_transcripts_for_audio_file(audio_file: VisitAudioFile) -> int:
    return AIVisitTranscript.where(
        (AIVisitTranscript.doctor_id == audio_file.doctor_id)
        & (AIVisitTranscript.appointment_id == audio_file.appointment_id)
        & (AIVisitTranscript.patient_id == audio_file.patient_id)
    ).count()

def test_perform_idempotency():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.m4a"
  )
  assert audio_file is not None
  assert count_transcripts_for_audio_file(audio_file) == 0

  perform(audio_file.id)
  assert count_transcripts_for_audio_file(audio_file) == 1

  transcript = AIVisitTranscript.get(
      doctor_id=audio_file.doctor_id,
      appointment_id=audio_file.appointment_id,
      patient_id=audio_file.patient_id
  )
  assert transcript is not None

  perform(audio_file.id)
  assert count_transcripts_for_audio_file(audio_file) == 1

  latest_transcript = AIVisitTranscript.get(
     doctor_id=audio_file.doctor_id,
     appointment_id=audio_file.appointment_id,
     patient_id=audio_file.patient_id
  )
  assert latest_transcript is not None
  assert transcript.id == latest_transcript.id

def test_wav_transcription():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.wav"
  )
  assert audio_file is not None

  result = transcribe_audio(audio_file)

  assert result.duration <= 305 # SHORT versions were trimmed to 5 min exactly.
  doctor_entries = list(filter(lambda te: te.speaker == "Doctor", result.transcript))
  assert len(doctor_entries) >= 5
  patient_entries = list(filter(lambda te: te.speaker == "Patient", result.transcript))
  assert len(patient_entries) >= 6

def test_m4a_transcription():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.m4a"
  )
  assert audio_file is not None

  result = transcribe_audio(audio_file)

  assert result.duration <= 305 # SHORT versions were trimmed to 5 min exactly.
  doctor_entries = list(filter(lambda te: te.speaker == "Doctor", result.transcript))
  assert len(doctor_entries) >= 5
  patient_entries = list(filter(lambda te: te.speaker == "Patient", result.transcript))
  assert len(patient_entries) >= 6

def test_mp4_transcription():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.mp4"
  )
  assert audio_file is not None

  result = transcribe_audio(audio_file)

  assert result.duration <= 305 # SHORT versions were trimmed to 5 min exactly.
  doctor_entries = list(filter(lambda te: te.speaker == "Doctor", result.transcript))
  assert len(doctor_entries) >= 5
  patient_entries = list(filter(lambda te: te.speaker == "Patient", result.transcript))
  assert len(patient_entries) >= 6

def test_avi_transcription():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.avi"
  )
  assert audio_file is not None

  result = transcribe_audio(audio_file)

  assert result.duration <= 305 # SHORT versions were trimmed to 5 min exactly.
  doctor_entries = list(filter(lambda te: te.speaker == "Doctor", result.transcript))
  assert len(doctor_entries) >= 5
  patient_entries = list(filter(lambda te: te.speaker == "Patient", result.transcript))
  assert len(patient_entries) >= 6

def test_mov_transcription():
  # Created in test seeds.
  audio_file = VisitAudioFile.get(
     azure_file_name="SHORT-teresa-jensen-mock-appointment-11-15-2024.mov"
  )
  assert audio_file is not None

  result = transcribe_audio(audio_file)

  assert result.duration <= 305 # SHORT versions were trimmed to 5 min exactly.
  doctor_entries = list(filter(lambda te: te.speaker == "Doctor", result.transcript))
  assert len(doctor_entries) >= 5
  patient_entries = list(filter(lambda te: te.speaker == "Patient", result.transcript))
  assert len(patient_entries) >= 6
