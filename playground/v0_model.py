class Provider(TimestampMixin):
    organization_id: Organization


class DoctorNoteSchema(TimestampMixin):
    """
    A description of the fields required for a specific note type and instructions on what information goes in each field
    """

    provider_id: Provider
    note_type: str
    fields: dict


class DoctorNoteExample(TimestampMixin):
    """
    When onboarding, doctors will upload PDFs of their best notes. The fields in this model will be extracted from the PDF.
    """

    provider_id: Provider
    doctor_note_schema_id: DoctorNoteSchema

    note: dict


class VisitAudioFile(TimestampMixin, DeletedAtMixin):
    """
    Link to the blob for a specific visit audio file
    """

    appointment_id: uid
    azure_blob_id: uid


class Patient(TimestampMixin):
    """
    Represents a patient
    """

    name: str


class Appointment(TimestampMixin):
    """
    Represents an appointment with a patient from accuity
    """

    patient_id: Patient
    location: str


class DeidentifiedAIVisitTranscript(TimestampMixin, DeletedAtMixin):
    """
    Contents of an AIVisitTranscript after being run through Azure's deidentification service.
    """

    transcript_data: list[TranscriptEntries]
    deidentified_visit_note_id: uid


class DeidentifiedAIVisitNote(TimestampMixin, DeletedAtMixin):
    """
    Contents of an AIVisitNote after being run through Azure's deidentification service.
    """

    note: dict
    note_schema_id: uid


class AIVisitTranscript(TimestampMixin, DeletedAtMixin):
    """
    After a certain point, we need to hard delete the transcript contents. We will keep the rest of the record around
    and wipe the transcript data.
    """

    class TranscriptEntries:
        speaker: str
        content: str

    user_id: uid
    appointment_id: uid
    transcript_data: list[TranscriptEntries]
    visit_audio_file_id: uid
    visit_time: int


class AIVisitNote(TimestampMixin, DeletedAtMixin):
    """
    Contents of the note are persisted for a long period of time and then deleted
    """

    user_id: uid
    appointment_id: uid
    transcript_id: uid
    note: dict
    note_schema_id: uid
