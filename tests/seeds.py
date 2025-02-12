# seed fresh test databases with data

from datetime import datetime

import pytz

from app import log

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

from tests.routes.utils import get_clerk_dev_user


def seed_test_data():
    log.info("creating test fixture data")

    username, password, user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=user.id)

    patient = Patient.find_or_create_by(
        doctor_id=doctor.id,
        name="Ghengis Khan",
        date_of_birth=datetime.strptime("1970-10-31", "%Y-%m-%d"),
    )

    appointment = Appointment.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(),
    )

    VisitAudioFile.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name="fiat-health-mock-patient-appointment-12-17-2024.m4a",
    )

    # For testing different video filetypes.
    eastern_tz = pytz.timezone("America/New_York")
    hour = 13
    for file_type in ["mp4", "m4a", "mov", "avi"]:
        date_no_zone = datetime(2024, 11, 15, hour, 0, 0)
        date_et = eastern_tz.localize(date_no_zone)
        hour += 1
        appt = Appointment.find_or_create_by(
            doctor_id=doctor.id, patient_id=patient.id, appointment_date=date_et
        )
        print(f"seeding teresa-jensen-mock-appointment-11-15-2024.{file_type}")
        VisitAudioFile.find_or_create_by(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=appt.id,
            azure_file_name=f"teresa-jensen-mock-appointment-11-15-2024.{file_type}",
        )

    DoctorNoteFile.find_or_create_by(
        doctor_id=doctor.id,
        azure_file_name="teresa-jensen-mock-appointment-note-11-15-2024.pdf",
        note_type="Telehealth H&P Note",
    )
    DoctorNoteFile.find_or_create_by(
        doctor_id=doctor.id,
        azure_file_name="teresa-jensen-mock-appointment-note-11-22-2024.pdf",
        note_type="Telehealth SOAP Note",
    )

    schema = DoctorNoteSchema.find_or_create_by(
        doctor_id=doctor.id,
        note_type="Test SOAP Note",
        fields={
            "CC": "Summarize the main reason for the patient's visit in one sentence.",
            "ROS": "Conduct a review of systems to identify any additional symptoms the patient may be experiencing across different body systems.",
            "Plan": "Outline the proposed management plan, including any treatments, medications, follow-up appointments, and patient education.",
            "Vitals": "Record the patient's vital signs, including temperature, blood pressure, heart rate, respiratory rate, and oxygen saturation.",
            "Allergies": "Note any known allergies the patient has, including drug, food, and environmental allergies, and describe the type of reaction if known.",
            "Objective": "Include findings from the physical examination and any relevant diagnostic tests or imaging results.",
            "Assessment": "List the primary diagnosis and any secondary diagnoses, along with the rationale for each diagnosis.",
            "Subjective": "Document the patient's own description of their symptoms, concerns, and any relevant personal or family medical history.",
            "Medications": "List all current medications the patient is taking, including dosages and frequency.",
            "Mental/Functional": "Assess the patient's mental status and functional abilities, noting any cognitive or psychological concerns.",
        },
    )

    DoctorNoteExample.find_or_create_by(
        doctor_id=doctor.id,
        doctor_note_schema_id=schema.id,
        field_examples={
            "CC": "Thyroid",
            "ROS": "12 pt ROS performed including Gen, HEENT, Resp, CV, GI, Skin - nothing additional to HPI",
            "Plan": "Hashimoto thyroiditis\nClinically, not feeling great and c/o fatigue with unrestorative sleep. However, able to work out and gaining stamina and strength, supporting the lab results that indicate adequate thyroid function. History suggests against a sleep disorder, depression or hormonal dysfunction, but she may have chronic sleep deprivation that is not yet corrected based on the ages of her kids. Also may need combined T3 and T4 supplementation for best results. Discussed other ways to optimize immune regulation through diet or other medications (naltrexone) - resources sent.\nAgreed to reduce NP3 to 50mg and add back levotyroxine 50mcg.\nCheck thyroid panel in 6 weeks - sent to LabCorps.\nCall if feeling significantly worse and need to be seen earlier to reassess medications, otherwise schedule follow up for after labs are completed.",
            "Vitals": "Height/Length Weight BMI Blood Pressure Temperature Pulse RR SpO2\nRecorded: 2024 Nov 22 14:08 by: Teresa Asam-Jensen",
            "Allergies": "No known allergies",
            "Objective": "GEN: NAD, sitting comfortably facing screen, engaged easily in conversation.\nHEENT: MMM to observation\nNECK: supple, NT, FROM\nRESP: nonlabored breathing, no use of accessory of muscles of respiration\nDERM: skin warm and dry\nEXT: symmetric movement bilat/UE; no cyanosis/clubbing/edema\nNEURO: AO x 3, grossly normal coordination and tone\nPSYCH: judgment/insight intact, euthymic mood/ congruent affect; good eye contact\n\nLabs Reviewed: Nov 2024\n1.2 TSH\nT4 1.21 (direct)\nT3 - WNL\nTPO nearly 200\n\nImaging: none\nCycle review: none",
            "Assessment": "Hashimoto thyroiditis (E06.3) Autoimmune thyroiditis modified 22 Nov, 2024",
            "Subjective": "Emily B - history of thyroid disorder (Hashimoto); as of the last visit was placed on NP thyroid 100mg daily. She had tried levothyroxine when first dx after the birth of her last child -without much success. Numbers didn't seem to respond and didn't feel great.\n\nMost recent labs:\n1.2 TSH\nT4 1.21 (direct)\nT3 - WNL\nTPO nearly 200\nup from 130 (after going back to gluten diet)\ncut it back out - went up instead - feel really tired; sleeps 2hrs more than spouse; unsure if its life or thyroid\n+hair loss; skin normal; BMs normal; appetite about the same maybe more sensitive to alcohol and other rich/junk food\nmood is good\nworking out; gaining stamina, feels good\nsleeps through night 5d per week - kids wake up 1-2 days per week. Sleep: no snoring; light sleeper for kids but doesn't move and seems sound. 8h\nAt dx cut out inflammatory foods - antibodies went down and felt better\n\nNo recent illnesses or unusual stressors; just a mild cold 2 weeks ago.\nNo change in supplements, no other medications.",
            "Medications": "(Medication reconciliation last updated 11/22/2024 01:42 PM, TERESA ASAM-JENSEN - Performed)\nNP Thyroid 120 mg tablet\nVitamin D3 125 mcg (5,000 unit) tablet",
            "Mental/Functional": "Mood is good: denies depression, anxiety",
        },
    )

    AIVisitTranscript.find_or_create_by(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=182,
        transcript_data=[
            item.model_dump()
            for item in [
                TranscriptEntry(
                    startTime=1000,
                    endTime=6000,
                    speaker="Doctor",
                    content="How can I help you today? Are you having any issues?",
                ),
                TranscriptEntry(
                    startTime=6000,
                    endTime=26000,
                    speaker="Patient",
                    content="The last couple days I've been struggling. I feel like I7 hard time breathing a little bit. My throat's been really scratchy. You know, I thought it might just over a couple of days go away, but it's just been persistent, seemingly getting worse.",
                ),
                TranscriptEntry(
                    startTime=26000,
                    endTime=30000,
                    speaker="Doctor",
                    content="Is anybody else in your family sick?",
                ),
                TranscriptEntry(
                    startTime=30000,
                    endTime=40000,
                    speaker="Patient",
                    content="My daughter had something going on a little bit last week, but that was. She seems to be better now, but that's about it.",
                ),
                TranscriptEntry(
                    startTime=40000,
                    endTime=44000,
                    speaker="Doctor",
                    content="How many days have you had a sore throat for?",
                ),
                TranscriptEntry(
                    startTime=44000,
                    endTime=50000,
                    speaker="Patient",
                    content="This has probably been going on since Saturday, so three or four days.",
                ),
                TranscriptEntry(
                    startTime=50000,
                    endTime=55000,
                    speaker="Doctor",
                    content="Okay. Have you had any fever?",
                ),
                TranscriptEntry(
                    startTime=55000,
                    endTime=70000,
                    speaker="Patient",
                    content="A little bit. A little bit of a fever and a little bit of a headache that I noticed it mostly. I would say it gets worse at night than. I don't notice it so much during the day.",
                ),
                TranscriptEntry(
                    startTime=70000,
                    endTime=75000,
                    speaker="Doctor",
                    content="Have you been able to eat and drink or... ?",
                ),
                TranscriptEntry(
                    startTime=75000,
                    endTime=90000,
                    speaker="Patient",
                    content="Yeah, I've been keeping things down. My appetite's not as significant as it usually is, but I've been keeping things in. I haven't been vomiting or anything like that.",
                ),
                TranscriptEntry(
                    startTime=90000,
                    endTime=95000,
                    speaker="Doctor",
                    content="And what about any cough or vomiting? Any other symptoms besides the sore throat?",
                ),
                TranscriptEntry(
                    startTime=95000,
                    endTime=120000,
                    speaker="Patient",
                    content="Yeah, probably coughing a little bit more. No vomiting, no stomach issues at all, but just like this persistent. Sometimes it's a little bit harder to breathe. Again, the symptoms get a little bit worse at night, so my wife's telling me that I'm snoring a little bit more often while I'm sleeping. It's been hard to kind of maintain quality sleep during the nighttime.",
                ),
                TranscriptEntry(
                    startTime=120000,
                    endTime=125000,
                    speaker="Doctor",
                    content="And what would you rate your pain? 0 to 10.",
                ),
                TranscriptEntry(
                    startTime=125000,
                    endTime=130000,
                    speaker="Patient",
                    content="Probably about a 7, 7 and a half or 8.",
                ),
                TranscriptEntry(
                    startTime=130000,
                    endTime=135000,
                    speaker="Doctor",
                    content="Do you feel like it's been constant or it's coming and going?",
                ),
                TranscriptEntry(
                    startTime=135000,
                    endTime=150000,
                    speaker="Patient",
                    content="It's been getting a little bit gradually worse and again noticing it more in the evening time. And I got to do something about this. I've got a really big presentation tomorrow night, and I need something that's going to help me real quick.",
                ),
                TranscriptEntry(
                    startTime=150000,
                    endTime=155000,
                    speaker="Doctor",
                    content="Can I do an exam and, like, dictate an exam as well?",
                ),
                TranscriptEntry(
                    startTime=155000,
                    endTime=156000,
                    speaker="Patient",
                    content="Yeah, please.",
                ),
                TranscriptEntry(
                    startTime=156000,
                    endTime=180000,
                    speaker="Doctor",
                    content="Okay, so let's see. Okay, we got your weight. You're like 180 pounds, and blood pressure looks great. 110 over 90. Your tonsils are pretty big and inflamed. I don't see any exudates, though. Definitely. Definitely looks like something's going on, though. Lung sounds are clear, heart rate normal and about 80. Cervical lymph nodes are shoddy, and ears look perfect. Nothing. Tympanic membranes look normal. No congestion, no rhinorrhea. Okay, I think we'll do a strep test and see what that comes back with. If it's positive, we'll treat with antibiotics. And if it's negative? Then I'd say it's viral and it's got to run its course.",
                ),
                TranscriptEntry(
                    startTime=180000,
                    endTime=182000,
                    speaker="Patient",
                    content="Okay. Thanks, Doc.",
                ),
            ]
        ],
    )

    ai_visit_transcript = AIVisitTranscript.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        duration=180,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor",
                content="Hello, how are you feeling today?",
                startTime=0,
                endTime=2000,
            ),
            TranscriptEntry(
                speaker="Patient",
                content="I've been having some headaches lately.",
                startTime=2500,
                endTime=5000,
            ),
            TranscriptEntry(
                speaker="Doctor",
                content="I see. How long have you been experiencing these headaches?",
                startTime=5500,
                endTime=8000,
            ),
        ],
    )

    doctor_note_schema = DoctorNoteSchema.find_or_create_by(
        doctor_id=doctor.id,
        note_type="Telehealth SOAP Note",
        fields={
            "Chief Complaint": "Document the patient's primary reason for visit",
            "History of Present Illness": "Describe the chronological history of the current medical problem",
            "Assessment": "List the diagnoses and clinical impressions",
            "Plan": "Detail the treatment plan and next steps",
        },
    )

    AIVisitNote.find_or_create_by(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=ai_visit_transcript.id,
        doctor_note_schema_id=doctor_note_schema.id,
        note={
            "Chief Complaint": "Patient reports recurring headaches",
            "History of Present Illness": "Patient has been experiencing headaches recently. Frequency and duration to be determined.",
            "Assessment": "Recurring headaches of unknown etiology",
            "Plan": "Further evaluation needed to determine cause and appropriate treatment plan",
        },
    )
