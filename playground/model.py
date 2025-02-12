class Organization(TimestampMixin):
    pass


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
