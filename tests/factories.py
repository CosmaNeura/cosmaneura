"""
Remove the alt once factoryboy is refactored out of the codebase
"""

import typing as t

from polyfactory import BaseFactory, NeverNone
from polyfactory.decorators import post_generated
from polyfactory.factories.pydantic_factory import ModelFactory
from polyfactory.field_meta import FieldMeta
from typeid import TypeID

from app.commands.intake.create import AppointmentRequest

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import API_KEY_PREFIX, CLERK_OBJECT_PREFIX, Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake, IntakeSchemaQuestion
from app.models.patient import Patient

"""
Notes on polyfactory:

1. is_supported_type validates that the class can be used to generate a factory
https://github.com/litestar-org/polyfactory/issues/655#issuecomment-2727450854
"""


# TODO not currently used
def type_id_provider(cls, field_meta):
    # TODO this doesn't work well with __ args:
    # https://github.com/litestar-org/polyfactory/pull/666/files
    return str(TypeID("hi"))


BaseFactory.add_provider(TypeIDType, type_id_provider)


class SQLModelFactory[T](ModelFactory[T]):
    """
    Base factory for SQLModel models:

    1. Ability to ignore all relationship fks
    2. Option to ignore all pks
    """

    __is_base_factory__ = True

    @classmethod
    def should_set_field_value(cls, field_meta: FieldMeta, **kwargs: t.Any) -> bool:
        has_object_override = hasattr(cls, field_meta.name)

        # TODO this should be more intelligent
        if not has_object_override and (
            field_meta.name == "id" or field_meta.name.endswith("_id")
        ):
            return False

        return super().should_set_field_value(field_meta, **kwargs)


class ActiveModelFactory[T](SQLModelFactory[T]):
    __is_base_factory__ = True

    @classmethod
    def should_set_field_value(cls, field_meta: FieldMeta, **kwargs: t.Any) -> bool:
        # do not default deleted at mixin to deleted!
        # TODO should be smarter about this...
        if field_meta.name == "deleted_at":
            return False

        return super().should_set_field_value(field_meta, **kwargs)

    # @classmethod
    # def build(
    #     cls,
    #     factory_use_construct: bool | None = None,
    #     sqlmodel_save: bool = False,
    #     **kwargs: t.Any,
    # ) -> T:
    #     result = super().build(factory_use_construct=factory_use_construct, **kwargs)

    #     # TODO allow magic dunder method here
    #     if sqlmodel_save:
    #         result.save()

    #     return result


class DoctorFactory(ActiveModelFactory[Doctor]):
    # TODO cleanup these prefixes
    # TODO can polyfactory handle this for us?
    api_key = lambda: str(TypeID(API_KEY_PREFIX))
    clerk_id = lambda: str(TypeID(CLERK_OBJECT_PREFIX[0:-1]))


class PatientFactory(ActiveModelFactory[Patient]):
    pass


class AppointmentFactory(ActiveModelFactory[Appointment]):
    # doctor_id is dependency-injected
    @post_generated
    @classmethod
    def patient_id(cls, doctor_id: TypeIDType) -> TypeIDType:
        assert doctor_id
        patient = PatientFactory.build(doctor_id=doctor_id).save()
        return patient.id

    appointment_date = ModelFactory.__faker__.date_between(
        start_date="-90d", end_date="today"
    )
    appointment_name = ModelFactory.__faker__.catch_phrase()


class FutureAppointmentFactory(AppointmentFactory):
    appointment_date = ModelFactory.__faker__.date_between(
        start_date="+1d", end_date="+90d"
    )


class AppointmentRequestFactory(ActiveModelFactory[AppointmentRequest]):
    # pick a random ID, in any format
    id = BaseFactory.__faker__.uuid4

    intake_json = NeverNone()

    @classmethod
    def doctor_email(cls) -> str:
        # optional by default, but make it required in the factory otherwise the randomness of doctor_{email,id} will cause us issues
        # TODO https://github.com/litestar-org/polyfactory/issues/655
        return cls.__faker__.ascii_email()


EXAMPLE_INTAKE_QUESTIONS = [
    IntakeSchemaQuestion(
        question="Do you have any pre-existing medical conditions?",
        required_details="Include important details",
        order=1,
    ),
    IntakeSchemaQuestion(
        question="What medications are you currently taking?",
        required_details="Include important details",
        order=2,
    ),
    IntakeSchemaQuestion(
        question="Do you have any known allergies?",
        required_details="Include important details",
        order=3,
    ),
]


class SimpleIncompleteIntakeFactory(ActiveModelFactory[Intake]):
    # TODO no...
    intake_questions = EXAMPLE_INTAKE_QUESTIONS

    summary = None

    # TODO should we use ignore instead and just default to server defaults?
    intake_questions_answers = list

    # TODO automatically ignoring all relationships on sqlmodel needs to be upstreamed
    # https://discord.com/channels/919193495116337154/1349832200060665969

    # TODO this is not the best way to do this, but we need a way to customize generation of relationships
    # https://discord.com/channels/919193495116337154/1349843948809486398
    @classmethod
    def post_create(cls, model):
        """
        We need:

        - intake schema
        - appointment

        all tied to the same doctor
        """
        doctor_id = model.doctor_id

        if not doctor_id:
            doctor_id = DoctorFactory.build().save().id
            model.doctor_id = doctor_id

        if not model.appointment_id:
            appointment = FutureAppointmentFactory.build(doctor_id=doctor_id).save()
            model.appointment_id = appointment.id

        model.save()


class SimpleCompleteIntakeFactory(SimpleIncompleteIntakeFactory):
    summary = """
- **Reason for Visit**: Follow-up for acne.
- **Appointment Type**: Dermatology │ Existing Patient.
- **Appointment Category**: Virtual Dermatology.
- **Patient Details**: 19-year-old male, 6'0", 155 lbs.

- **Acne Changes**: Acne has worsened with more breakouts on cheeks and forehead, appearing more inflamed.
- **Triggers**: Stress and increased consumption of dairy products seem to trigger breakouts.
- **Current Treatments**:
  - Topical retinoid cream (Tretinoin) used nightly.
  - Over-the-counter benzoyl peroxide wash used in the morning.
  - New moisturizer added to routine.
- **Side Effects**: No side effects reported from current treatments.
- **Other Skin Concerns**: Dryness and peeling around nose and mouth, likely due to retinoid cream.
- **Diet/Lifestyle Changes**: Consumes one raw sardine each morning with sunrise.
- **Other Medications/Supplements**: Taking "Molly."

- **Previous Visit (0 days ago)**:
  - **Chief Complaint**: Recurring headaches.
  - **Assessment**: Recurring headaches of unknown etiology.
  - **Plan**: Further evaluation needed to determine cause and appropriate treatment plan.
"""

    @classmethod
    def post_create(cls, model):
        super().post_create(model)

        "answer all questions"
        for question in model.intake_questions:
            question.answer = cls.__faker__.sentence()
            question.answer_timestamp = int(cls.__faker__.unix_time())

        # IMPORTANT json objects are not automatically marked as dirty
        model.flag_modified("intake_questions")
        model.save()


class DoctorNoteExampleFactory(ActiveModelFactory[DoctorNoteExample]):
    # TODO we should be able to inspect the types on the field and generate the correct dict types
    # field_examples = lambda: BaseFactory.__faker__.pydict(allowed_types=(str,))
    pass


class DoctorNoteSchemaFactory(ActiveModelFactory[DoctorNoteSchema]):
    # doctor = DoctorFactory.build
    note_type = BaseFactory.__faker__.word

    # fields = factory.Faker("pydict", allowed_types=(str,))

    @classmethod
    def post_create(cls, model: DoctorNoteSchema):
        # TODO why can't i do this on the header level?
        if not model.doctor_id:
            model.doctor = DoctorFactory.build().save()
        # model.doctor_id = DoctorFactory.build().save().id

        model.save()

        # TODO one-to-many relationship could be generalized here
        DoctorNoteExampleFactory.build(
            doctor_note_schema_id=model.id, doctor_id=model.doctor_id
        ).save()


class DoctorNoteFileFactory(ActiveModelFactory[DoctorNoteFile]):
    # TODO all we are trying to do here is setup a relationship, there's got to be a better way to do this...
    doctor_id = lambda: DoctorFactory.build().save().id
