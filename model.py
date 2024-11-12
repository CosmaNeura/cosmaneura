# TODO need to understand the clerk data model


class Organization(TimestampMixin):
    pass


class Provider(TimestampMixin):
    organization_id: Organization = Relationship()


class PatientIntakeExample(TimestampMixin):
    """
    When onboarding, doctor's will upload their patients best intake examples. The fields in this model will be extracted from the PDF.
    """

    provider_id: Provider = Relationship()

    # TODO need to understand what types are in accuity and how to map them
    appointment_type: str
    content: str


class SchedulerEvent(TimestampMixin):
    """
    For each integrated
    """

    raw_payload: dict


class IntakeLink(TimestampMixin):
    """
    For each scheduled event, we will create a link to a unique intake chat.
    """


class IntakeTranscript(TimestampMixin):
    """
    When a patient submits an intake form, the raw data is stored here.
    """


class DoctorNoteExample(TimestampMixin):
    """
    When onboarding, doctors will upload PDFs of their best notes. The fields in this model will be extracted from the PDF.
    """

    # TODO do we need the organization_id?
    provider_id: Provider = Relationship()
    # name of note type, pulled out of PDF
    note_type: str
    # just the fields, pulled out of PDF
    fields: dict
    # the good example broken into fields
    content: dict
