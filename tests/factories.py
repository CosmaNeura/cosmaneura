from copy import deepcopy

from polyfactory import BaseFactory, Ignore, Use
from polyfactory.decorators import post_generated
from polyfactory.factories.pydantic_factory import ModelFactory
from typeid import TypeID

from app.commands.intake.create import AppointmentRequest

from activemodel.pytest.factories import ActiveModelFactory
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import API_KEY_PREFIX, CLERK_OBJECT_PREFIX, Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import ExternalIntakeData, Intake, IntakeSchemaQuestion
from app.models.patient import Patient


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

_MY_CATHOLIC_DOCTOR_EXTERNAL_INTAKE = {
    "type": "Dermatology - NEW Patient Consultation",
    "category": "Virtual Dermatology",
    "forms": [
        {
            "id": 1252863,
            "name": "Patient DOB, Gender, Height, & Weight",
            "values": [
                {
                    "id": 5577842739,
                    "fieldID": 6851254,
                    "value": None,
                    "name": "Patient Date of Birth (MM/DD/YYYY)",
                },
                {
                    "id": 5577842740,
                    "fieldID": 8786185,
                    "value": None,
                    "name": "Patient Sex",
                },
                {
                    "id": 5577842741,
                    "fieldID": 13188233,
                    "value": None,
                    "name": "Patient Height",
                },
                {
                    "id": 5577842742,
                    "fieldID": 13188234,
                    "value": None,
                    "name": "Patient Weight",
                },
            ],
        },
        {
            "id": 1249290,
            "name": "Billing Address (Required)",
            "values": [
                {
                    "id": 5577842743,
                    "fieldID": 6829675,
                    "value": None,
                    "name": "Address",
                },
                {
                    "id": 5577842744,
                    "fieldID": 6829676,
                    "value": None,
                    "name": "Address 2",
                },
                {
                    "id": 5577842745,
                    "fieldID": 6829677,
                    "value": None,
                    "name": "City",
                },
                {
                    "id": 5577842746,
                    "fieldID": 6829678,
                    "value": None,
                    "name": "State",
                },
                {
                    "id": 5577842747,
                    "fieldID": 6829679,
                    "value": None,
                    "name": "Zip",
                },
                {
                    "id": 5577842748,
                    "fieldID": 7485296,
                    "value": None,
                    "name": "What is the primary reason for making this appointment?",
                },
            ],
        },
    ],
}

def _make_intake_json(faker):
    def _builder():
        data = deepcopy(_MY_CATHOLIC_DOCTOR_EXTERNAL_INTAKE)

        for form in data["forms"]:
            for v in form["values"]:
                name = v["name"]

                if name == "Patient Date of Birth (MM/DD/YYYY)":
                    dob = faker.date_of_birth(minimum_age=0, maximum_age=90)
                    v["value"] = dob.strftime("%-m/%-d/%Y")

                elif name == "Patient Sex":
                    v["value"] = faker.random_element(["Male", "Female", "Other"])

                elif name == "Patient Height":
                    feet = faker.random_int(4, 6)
                    inches = faker.random_int(0, 11)
                    v["value"] = f"{feet}'{inches}"

                elif name == "Patient Weight":
                    v["value"] = str(faker.random_int(90, 300))

                elif name == "Address":
                    v["value"] = faker.street_address()

                elif name == "Address 2":
                    v["value"] = faker.secondary_address()

                elif name == "City":
                    v["value"] = faker.city()

                elif name == "State":
                    state = faker.state()
                    v["value"] = f"{state} – {faker.state_abbr()}"

                elif name == "Zip":
                    v["value"] = faker.zipcode()

                elif name == "What is the primary reason for making this appointment?":
                    choices = [
                        "Annual mole check",
                        "eczema",
                        "Rash on upper back",
                        "diaper rash",
                        "Dermatitis flare-up review",
                        "Hormonal Acne, Body Acne, Cysts",
                        "Hair thinning on crown",
                    ]
                    v["value"] = faker.random_element(choices)

        return data

    return _builder


class AppointmentRequestFactory(ActiveModelFactory[AppointmentRequest]):
    "generate an API request that a user (clinic) would send to create an appointment"

    # pick a random ID, in any format, since this is not a typeid
    id = BaseFactory.__faker__.uuid4

    intake_json = Use(_make_intake_json(BaseFactory.__faker__))

    # A date in the future, with or without an attached timezone.
    date = Use(lambda: BaseFactory.__random__.choice([
        BaseFactory.__faker__.future_datetime(),
        BaseFactory.__faker__.future_datetime(
            tzinfo=BaseFactory.__faker__.pytimezone()
        ),
    ]))

    # TODO this needs to be fixed upstream in polyfactory, these are all slightly different...
    # doctor = None
    # doctor = Use(lambda: None)
    # doctor = lambda: None

    # note that `Ignore()` will only set a None value if that default is defined on the underlying model
    doctor = Ignore()
    doctor_id = Ignore()

    @classmethod
    def build_json(cls):
        request = cls.build(factory_use_construct=True)

        # paranoid because we are adding this logic to polyfactory
        assert request.intake_json

        # fields exist, but are None
        assert request.doctor is None
        assert request.doctor_id is None

        # in order to mock the user request, make sure the doctor fields don't end up in the request!
        return request.model_dump(mode="json", exclude={"doctor", "doctor_id"})

    @classmethod
    def doctor_email(cls) -> str:
        # optional by default, but make it required in the factory otherwise the randomness of doctor_{email,id} will cause us issues
        # TODO https://github.com/litestar-org/polyfactory/issues/655
        return cls.__faker__.ascii_email()


class PastAppointmentRequestFactory(AppointmentRequestFactory):
    """
    Same as AppointmentRequestFactory, but always generates a date in the past.
    """
    date = BaseFactory.__faker__.date_time_between(
        start_date="-1y",  # up to one year ago
        end_date="now",    # up until this instant
    )


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

    summary = Ignore()

    # TODO should we use ignore instead and just default to server defaults?
    intake_questions_answers = list

    cleaned_external_intake_data = ExternalIntakeData(
        reason_for_visit="Acne on face",
        appointment_type="Dermatology │ Existing Patient",
        additional_info="Male, 19 years old, 6'0\", 155 lbs.",
        appointment_category="Virtual Dermatology",
    )

    # TODO automatically ignoring all relationships on sqlmodel needs to be upstreamed
    # https://discord.com/channels/919193495116337154/1349832200060665969

    # TODO this is not the best way to do this, but we need a way to customize generation of relationships
    # https://discord.com/channels/919193495116337154/1349843948809486398
    @classmethod
    def post_build(cls, model):
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

        return model


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
    def post_build(cls, model):
        super().post_build(model)

        "answer all questions"
        for question in model.intake_questions:
            question.answer = cls.__faker__.sentence()
            question.answer_timestamp = int(cls.__faker__.unix_time())

        # IMPORTANT json objects are not automatically marked as dirty
        model.flag_modified("intake_questions")
        model.save()

        return model


class DoctorNoteExampleFactory(ActiveModelFactory[DoctorNoteExample]):
    # TODO we should be able to inspect the types on the field and generate the correct dict types
    # field_examples = lambda: BaseFactory.__faker__.pydict(allowed_types=(str,))
    pass


class DoctorNoteSchemaFactory(ActiveModelFactory[DoctorNoteSchema]):
    # doctor = DoctorFactory.build
    note_type = BaseFactory.__faker__.word

    # fields = factory.Faker("pydict", allowed_types=(str,))

    @classmethod
    def post_build(cls, model):
        # TODO why can't i do this on the header level?
        if not model.doctor_id:
            model.doctor = DoctorFactory.build().save()
        # model.doctor_id = DoctorFactory.build().save().id

        model.save()

        # TODO one-to-many relationship could be generalized here
        DoctorNoteExampleFactory.build(
            doctor_note_schema_id=model.id, doctor_id=model.doctor_id
        ).save()

        return model


class DoctorNoteFileFactory(ActiveModelFactory[DoctorNoteFile]):
    # TODO all we are trying to do here is setup a relationship, there's got to be a better way to do this...
    doctor_id = lambda: DoctorFactory.build().save().id
