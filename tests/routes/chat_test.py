import typing as t

from fastapi import status
from fastapi.testclient import TestClient
from polyfactory import BaseFactory, Ignore
from polyfactory.decorators import post_generated
from polyfactory.factories.pydantic_factory import ModelFactory
from polyfactory.field_meta import FieldMeta
from typeid import TypeID

import app.commands.intake.chat
from app.commands.intake.create import AppointmentRequest
from app.server import api_app

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import API_KEY_PREFIX, CLERK_OBJECT_PREFIX, Doctor
from app.models.intake.doctor_intake_schema import DoctorIntakeSchema, IntakeQuestion
from app.models.intake.intake import Intake
from app.models.patient import Patient

"""
Notes on polyfactory:

1. is_supported_type validates that the class can be used to generate a factory
https://github.com/litestar-org/polyfactory/issues/655#issuecomment-2727450854
"""


class SQLModelFactory[T](ModelFactory):
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


class ActiveModelFactory[T](SQLModelFactory):
    __is_base_factory__ = True

    @classmethod
    def should_set_field_value(cls, field_meta: FieldMeta, **kwargs: t.Any) -> bool:
        # deleted at mixin, should be smarter about this...
        if field_meta.name == "deleted_at":
            return False

        return super().should_set_field_value(field_meta, **kwargs)


class DoctorFactory(ActiveModelFactory[Doctor]):
    # TODO cleanup these prefixes
    api_key = lambda: str(TypeID(API_KEY_PREFIX))
    clerk_id = lambda: str(TypeID(CLERK_OBJECT_PREFIX[0:-1]))


class PatientFactory(SQLModelFactory[Patient]):
    pass


class AppointmentFactory(ModelFactory[Appointment]):
    # ignore relationships
    id = Ignore()
    doctor_id = Ignore()

    # patient_id = Ignore()
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

    external_id = Ignore()


# TODO move to factories file
class AppointmentRequestFactory(ModelFactory[AppointmentRequest]):
    # we want to test the validation logic in a deterministic way
    doctor = None
    doctor_id = None

    @classmethod
    def doctor_email(cls) -> str:
        # optional by default, but make it required in the factory otherwise the randomness of doctor_{email,id} will cause us issues
        # TODO https://github.com/litestar-org/polyfactory/issues/655
        return cls.__faker__.ascii_email()


# class TypeIDTypeFactory(BaseFactory[TypeIDType]):
#     __set_as_default_factory_for_type__ = True

#     @classmethod
#     def process_kwargs(cls, **kwargs: t.Any) -> dict[str, t.Any]:
#         breakpoint()

#     @classmethod
#     def is_supported_type(cls, value: t.Any) -> t.TypeGuard[type[TypeIDType]]:
#         """Determine whether the given value is supported by the factory.

#         :param value: An arbitrary value.
#         :returns: A typeguard
#         """
#         return isinstance(value, TypeIDType)


EXAMPLE_INTAKE_QUESTIONS = [
    IntakeQuestion(
        key="medical_history",
        question="Do you have any pre-existing medical conditions?",
        description="Please list any diagnosed conditions or ongoing health issues",
        order=1,
    ),
    IntakeQuestion(
        key="current_medications",
        question="What medications are you currently taking?",
        description="Include prescription, over-the-counter medications and supplements",
        order=2,
    ),
    IntakeQuestion(
        key="allergies",
        question="Do you have any known allergies?",
        description="List any allergies to medications, foods, or environmental factors",
        order=3,
    ),
]


class DoctorIntakeSchemaFactory(ActiveModelFactory[DoctorIntakeSchema]):
    questions = EXAMPLE_INTAKE_QUESTIONS

    # doctor = Ignore()
    # @post_generated
    # @classmethod
    # def doctor_id(cls):
    #     breakpoint()

    # @classmethod
    # def post_generate(cls, result: dict[str, t.Any]) -> dict[str, t.Any]:
    #     doctor = DoctorFactory.build()
    #     doctor.save()

    #     result["doctor_id"] = doctor.id

    #     return result


class SimpleIncompleteIntakeFactory(ModelFactory[Intake]):
    # TODO no...
    intake_questions = EXAMPLE_INTAKE_QUESTIONS

    # TODO should we use ignore instead and just default to server defaults?
    intake_questions_answers = []
    intake_followup_questions = []
    intake_followup_questions_answers = []

    # TODO this is not the best way to do this, but we need a way to customize generation of relationships
    # TODO automatically ignoring all relationships on sqlmodel needs to be upstreamed
    # https://discord.com/channels/919193495116337154/1349832200060665969

    doctor = Ignore()
    appointment = Ignore()
    patient = Ignore()
    doctor_intake_schema = Ignore()

    id = Ignore()
    doctor_id = Ignore()
    appointment_id = Ignore()
    patient_id = Ignore()
    doctor_intake_schema_id = Ignore()

    # https://discord.com/channels/919193495116337154/1349843948809486398
    @classmethod
    def build(
        cls,
        factory_use_construct: bool | None = None,
        **kwargs: t.Any,
    ) -> Intake:
        """
        We need:

        - intake schema
        - appointment

        all tied to the same doctor
        """

        doctor = DoctorFactory.build().save()

        intake_schema = DoctorIntakeSchemaFactory.build(doctor_id=doctor.id).save()
        appointment = AppointmentFactory.build(doctor_id=doctor.id).save()

        intake_record = super().build(
            factory_use_construct=factory_use_construct,
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            doctor_intake_schema_id=intake_schema.id,
            **kwargs,
        )

        # intake_record.
        return intake_record

    # @post_generated
    # @classmethod
    # def doctor_id(cls):
    #     breakpoint()
    #     return None


# TODO move to general route assertions
def assert_status(response, status_code: int = status.HTTP_200_OK):
    assert response.status_code == status_code, response.json()


def test_intake_creation(client: TestClient):
    # paranoid
    assert Intake.count() == 0

    request = AppointmentRequestFactory.build(factory_use_construct=True)

    # use the same random email as the request
    user = DoctorFactory.build(email=request.doctor_email)
    user.generate_api_key()
    user.save()

    DoctorIntakeSchemaFactory.build(doctor_id=user.id).save()

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response)

    # successful response should include a url to redirect the user to
    data = response.json()
    assert data["url"].startswith("https://")

    assert Intake.count() == 1
    intake = Intake.last()

    assert intake
    assert intake.doctor_id == user.id
    assert intake.appointment_id

    # TODO we can't do this since the session is temporal, need to fix in activemodel
    # appointment = intake.appointment

    appointment = Appointment.get(intake.appointment_id)

    assert appointment
    assert appointment.created_by_system == "api"

    # try posting again, and let's make sure nothing new is created
    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response)

    assert Intake.count() == 1


def test_intake_without_doctor_email(client: TestClient):
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    # email on the above request will not exist
    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()

    # we are intentionally not creating an intake schema since we should fail before then

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json"),
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    data = response.json()
    assert "detail" in data
    assert "Doctor not found" in data["detail"][0]["msg"]


def test_intake_without_doctor_id(client: TestClient):
    request = AppointmentRequestFactory.build(factory_use_construct=True)

    user = DoctorFactory.build()
    user.generate_api_key()
    user.save()

    assert not user.deleted_at

    response = client.post(
        api_app.url_path_for("create_intake"),
        headers={"Authorization": f"Bearer {user.api_key}"},
        json=request.model_dump(mode="json")
        | {"doctor_id": None, "doctor_email": None},
    )

    assert_status(response, status.HTTP_422_UNPROCESSABLE_ENTITY)

    data = response.json()
    assert "detail" in data
    assert "doctor_id" in data["detail"][0]["msg"]


def test_last_question(client: TestClient):
    """
    In order to test this, we need an intake with a valid structure (questions, etc)
    """
    intake = SimpleIncompleteIntakeFactory.build().save()

    # paranoid: learning the factory system
    assert not intake.is_new()

    result = app.commands.intake.chat.perform(intake, None)

    assert result
    assert result.question == EXAMPLE_INTAKE_QUESTIONS[0].question
    assert result.key == EXAMPLE_INTAKE_QUESTIONS[0].key


def test_next_question():
    intake = SimpleIncompleteIntakeFactory.build().save()

    # should be empty to start
    assert not intake.intake_questions_answers

    # send in a fake response
    fake_response = BaseFactory.__faker__.sentence()
    result = app.commands.intake.chat.perform(intake, fake_response)

    assert result
    # second q test
    assert result.question == EXAMPLE_INTAKE_QUESTIONS[1].question
    assert result.key == EXAMPLE_INTAKE_QUESTIONS[1].key

    intake.refresh()

    # now we should have an answer
    assert len(intake.intake_questions_answers) == 1
    assert (
        intake.intake_questions_answers[0].question_key
        == EXAMPLE_INTAKE_QUESTIONS[0].key
    )
    assert intake.intake_questions_answers[0].answer == fake_response
