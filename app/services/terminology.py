"""
Provider/patient role terms for LLM prompt wording.

The Doctor.terminology setting decides what the provider and the person they
care for are called in generated content (notes, intake questions, intake
summaries): doctor/patient (default), therapist/client, or counselor/client.
Prompt builders interpolate these terms instead of hardcoding role words.

The frontend label catalog lives in web/app/utils/terminology.ts — keep the
two in sync when adding a label set.

NOTE: transcript speaker labels ("Doctor:"/"Patient:") are data produced at
transcription time and are NOT remapped — prompt instructions that reference
those literal labels must keep them, and add a mapping sentence instead (see
transcript_label_note()).
"""

from dataclasses import dataclass

from app.models.doctor import Doctor, Terminology


@dataclass(frozen=True)
class Terms:
    provider: str
    patient: str

    @property
    def provider_title(self) -> str:
        return self.provider.capitalize()

    @property
    def patient_title(self) -> str:
        return self.patient.capitalize()


TERMS: dict[Terminology, Terms] = {
    Terminology.doctor_patient: Terms(provider="doctor", patient="patient"),
    Terminology.therapist_client: Terms(provider="therapist", patient="client"),
    Terminology.counselor_client: Terms(provider="counselor", patient="client"),
    Terminology.nutritionist_client: Terms(provider="nutritionist", patient="client"),
}

DEFAULT_TERMS = TERMS[Terminology.doctor_patient]


def terminology_terms(doctor: Doctor | None) -> Terms:
    if doctor is None:
        return DEFAULT_TERMS
    return TERMS.get(doctor.terminology, DEFAULT_TERMS)


def transcript_label_note(t: Terms) -> str:
    """One sentence explaining the fixed "Doctor:"/"Patient:" transcript
    speaker labels when the active terms differ from the default. Empty for
    the default set so default prompts stay byte-identical."""

    if t == DEFAULT_TERMS:
        return ""

    return (
        f'Note: in the transcript, lines labeled "Doctor:" are you '
        f'(the {t.provider}) and lines labeled "Patient:" are the {t.patient}. '
        f'The note itself must use the term "{t.patient}", not "patient".'
    )
