"""
NOTE pretty much moved over to polyfactory instead, but still need to refactor this

TODO we should autogen all factories and then allow the user to extend them
https://github.com/fastapi/sqlmodel/discussions/615
https://factoryboy.readthedocs.io/en/stable/orms.html#managing-sessions
"""

import factory
from factory.alchemy import SQLAlchemyModelFactory
from typeid import TypeID

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema


class DoctorFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Doctor

    clerk_id = str(TypeID("user"))


class AppointmentFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Appointment

    appointment_date = factory.Faker(
        "date_between", start_date="-90d", end_date="today"
    )
    appointment_name = factory.Faker("word")
    appointment_notes_preview = factory.Faker("sentence")


class DoctorNoteExampleFactory(SQLAlchemyModelFactory):
    class Meta:
        model = DoctorNoteExample

    # TODO we should be able to inspect the types on the field
    field_examples = factory.Faker("pydict", allowed_types=(str,))


class DoctorNoteSchemaFactory(SQLAlchemyModelFactory):
    class Meta:
        model = DoctorNoteSchema

    doctor = factory.SubFactory(DoctorFactory)
    note_type = factory.Faker("word")
    fields = factory.Faker("pydict", allowed_types=(str,))

    @factory.post_generation
    def generate_examples(self, create, extracted, **kwargs):
        if not create:
            return

        # one-to-many relationship could be generalized here
        DoctorNoteExampleFactory(
            doctor_note_schema_id=self.id, doctor_id=self.doctor_id
        ).save()
