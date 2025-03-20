# seed fresh test databases with data

from datetime import datetime

import pytz

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

from tests.routes.utils import get_clerk_seed_user
from tests.utils import log


def seed_test_data():
    log.info("creating test fixture data")

    username, password, user = get_clerk_seed_user()
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
        log.info(f"seeding teresa-jensen-mock-appointment-11-15-2024.{file_type}")
        VisitAudioFile.find_or_create_by(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_id=appt.id,
            azure_file_name=f"teresa-jensen-mock-appointment-11-15-2024.{file_type}",
        )

    # For audio-only files.
    VisitAudioFile.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name="teresa-jensen-mock-appointment-11-15-2024.wav",
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

    schema = DoctorNoteSchema(
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
    ).save()

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

    doctor_note_schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Telehealth SOAP Note",
        fields={
            "Chief Complaint": "Document the patient's primary reason for visit",
            "History of Present Illness": "Describe the chronological history of the current medical problem",
            "Assessment": "List the diagnoses and clinical impressions",
            "Plan": "Detail the treatment plan and next steps",
        },
    ).save()

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

    patient2 = Patient.find_or_create_by(
        doctor_id=doctor.id,
        name="Ocie Dosie",
        date_of_birth=datetime.strptime("1980-12-25", "%Y-%m-%d"),
    )

    appointment2 = Appointment.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient2.id,
        appointment_date=datetime.now(),
    )

    # This is real patient data that has been de-identified using the Azure
    # de-identification service.
    AIVisitTranscript.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient2.id,
        appointment_id=appointment2.id,
        duration=1971,
        transcript_data=[
            TranscriptEntry(
                content="Okay. Yeah. So let's see. I was diagnosed with diabetes while I was in school. I believe it was I think 2019 or 2020, right around then. So it hasn't been a very long time. So fairly new with all that stuff. And then yeah, after, after I was diagnosed with diabetes, we began treating that because I'd been sick for probably about a year and a half. And then I progressively got to the point where I lost 30 pounds and that's when I actually went into the, the, like the health care office or whatever you want to call it, the doctor's office at the school just to get like a general checkup because I was like, something's wrong, like, why am I. And then they tested me for diabetes and my A1C was like 14.1. And they wanted to hospitalize me obviously, but with classes and stuff, I couldn't really afford to do that. So I just checked in with them daily, got my levels down and then I went home. So this was in the fall, I believe. So then I went home on winter break and still really wasn't feeling that much better, even though my numbers were under control. And I, I got the Dexcom and everything and it still wasn't under control. And we did some more testing, a whole lot of testing. I'm not sure if you were able to look at all my health records and stuff, but I've done. I'm not actually sure if they're all even in there, but we did a lot of different testing, including a special Lyme test out of North Dakota because we had a family friend who had Lyme and some of the symptoms sound similar. So she advises to take that, that and found out that I did have vime and you can that. And I mean it was not like a quick fix by any means. We started treating it, which is antibiotics. Doxycycline was the main one. I was on that for quite a while. And then. Yeah. What was the other? Bicillin injections as well. So just another way to fight the infection type thing. And then eventually, yeah, my energy started returning and, and got me to where I am today, which is pretty good, I would say, compared to where I was. So. Yeah. And then let's see. Insulin. Let's see. So I do two different types of insulin. Long acting and short acting. The long acting Atlantis, you 31 units a day, the morning and then for the fast acting. That obviously varies on my activity levels and yeah. Know what I consume, how Much car, how many carbs I eat and all that. I think my A1C last because I do. I have the Dexcom app. So it checks my A1C through like the follow app or something that's connected to it. And I think it was like 6.5 or 6.7 last I checked, like a couple weeks ago. So it's in a normal range now. Yeah. But yeah, I would, I would say that that's. That's pretty much with the competition.",
                endTime=235840, 
                speaker="Patient",
                startTime=6080
            ),
            TranscriptEntry(
                content="Okay. And so from. Other than the ejaculation problems, do you have other numbness, tingling that is chronic and the hands or feet, restless legs at night?",
                endTime=252460, 
                speaker="Doctor",
                startTime=236760
            ),
            TranscriptEntry(
                content="No, not anymore. I did have that for a while when I was treating the Lyme. Like bad. Like neuropathy. Right. It would like keep me up at night.",
                endTime=269180, 
                speaker="Patient",
                startTime=255720
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=270224, 
                speaker="Doctor",
                startTime=269560
            ),
            TranscriptEntry(
                content="Thing where it's just like pins and needles, but it's not like good pin needles. It's like pain needles. Yeah. Yeah. But the. The issue with ejaculation started even getting diet diabetes. So.",
                endTime=288220, 
                speaker="Patient",
                startTime=270352
            ),
            TranscriptEntry(
                content="Yeah. Yeah. That a 1C of 14 doesn't happen overnight. That. That was probably sitting there doing. Doing bad work for quite a long time. Have you had any concerns about vision or any other complications?",
                endTime=306510, 
                speaker="Doctor",
                startTime=290530
            ),
            TranscriptEntry(
                content="I did have vision stuff. Luckily my wife is. She works in an eye clinic, so I'm all set up. I have. I go every six months now just to get everything checked out. But I did have a little bit of diabetic retinopathy, so I had to get exactly how many. I did have a laser at one point, but yeah, it's all under control now. I hardly forget loaders. And of course she's all on me if I. If I have any right away.",
                endTime=343380, 
                speaker="Patient",
                startTime=307170
            ),
            TranscriptEntry(
                content="That's awesome. Okay. Gi. Good function or have you had issues with constipation? Slow transit.",
                endTime=353520, 
                speaker="Doctor",
                startTime=345780
            ),
            TranscriptEntry(
                content="Yes, but it's based on stuff I eat. I know. And this was kind of happening before I was diagnosed. I don't know if this has anything to do with it, but if I have coffee screws on my stomach, like bad, especially if I like, I can have it maybe once a week maximum. But if I do like back to back days or like too much. And in college I used to drink coffee all the time, no issues with it. So I don't know if that's something that came on with that. But that would cause interest. It usually has the opposite effect. Right.",
                endTime=391264, 
                speaker="Patient",
                startTime=356260
            ),
            TranscriptEntry(
                content="Interesting. Yeah.",
                endTime=392896, 
                speaker="Doctor",
                startTime=391352
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=393780, 
                speaker="Patient",
                startTime=393008
            ),
            TranscriptEntry(
                content="I don't know. So it's definitely. We do see that slowing if you get nerve injury to the gut. That's a huge issue for diabetes. But Also with Lyme disease and the having to be on antibiotics and changing that profile, or just the Lyme inflammation itself and then being on antibiotics tends to mess with the gut quite a bit. So that's why I actually asked about that. But. So, yeah, thanks. That's a really good history. Just helps me be the reason I asked some of those questions. It tells me a lot about where. Where kind of your baseline is at in terms of vascular inflammation. So we know that you can't help having the diabetes shows up and it's hit. It's hiding from you and it's doing its damage. You force. And our question really is, like, how much damage has it done? And we won't know all of that, but it helps me to understand what. What might be involved, the extent of it, and then to think about that in terms of how does that impact your metabolism and like, the vascular blood flow, things like that. So Dr. Mozal had recommended a couple things, and I don't know if you had. Did he send you his note at all?",
                endTime=657667, 
                speaker="Doctor",
                startTime=394520
            ),
            TranscriptEntry(
                content="He did, and I looked at it, I think last week. It was mostly all like, the general health stuff. Yeah, yeah.",
                endTime=489440, 
                speaker="Patient",
                startTime=478842
            ),
            TranscriptEntry(
                content="So he had talked about maybe trying some of these other treatments there. What they would do, if you wanted to do it, is I would do the prescribing under his recommendations because he knows what he's talking about, but he doesn't have prescribing privileges. So if you had wanted to try the Sudafed or Sildenafil, that would all be like, I would prescribe that. And your history gives me a very good baseline for making a case for that. And then. And so that's something that we can certainly try the typical. So this will maybe not sound like a non sequitur, but I spend. I have a small. Or when I was in more general practice, I had a small number of patients with type 1 diabetes, and then I had a huge number of type 2. And naturally, one of the main issues that obviously men present with are these vascular problems and that affects all of their function for not just the blood flow to. For intercourse, but also blood flow to the scrotum for, like, healthy testicles.",
                endTime=566512, 
                speaker="Doctor",
                startTime=492100
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=567072, 
                speaker="Patient",
                startTime=566656
            ),
            TranscriptEntry(
                content="And so you. If you had that for a long time, things start to not just dysfunction, but like, the testicles shrink. So maintaining good blood flow is a huge issue. And so when I ask questions about vascular problems, it's like in the back of my mind. So this is why both Sudafed and sildenafil do help. Sudafed actually helps with Constriction, oddly enough to allow for an appropriate forward blood flow. And then sildenafil is for dilation. Yeah, it's, it's very interesting because patients who've had this problem and not yours identically, but just in general, they might have erectile dysfunction or something. When we're able to get their blood sugar down for a long time, like yours already has been, and then add anti inflammatory measures to it, you will see some natural resolution of that problem as the blood flow improves. And the it is more studied in heart disease. But there are a lot of good cardiology articles that suggest that for men, their vascular function and their genitals is actually the number one and most like responsive to anti inflammatory dietary changes like you can actually or to statins, whichever you choose to use. And statins are those chronic cholesterol meds. But what they really do that's important is to actually decrease inflammation in the arteries. So they know that regardless of the source, it is partly reversible. And I just sharing that more just like perspective because it can seem like an overwhelming out of control, permanent problem. But the largest studies were actually done at Union Memorial Hospital and they would, they put their heart patients on a half of them went I think on a vegetarian diet, the other half went on a vegan diet. It would have been like vegetarian, Mediterranean. And then they would scan them to watch their vascular blood flow and they literally over a year and a half saw their cholesterol plaques disappear and the intimas, the lining of the blood vessels, what gets swollen and like starts to build the plaques that would actually return to more normal. So even when we're looking at something that's several years old for these people, this might have been like decades of inflammation. The blood vessels, it was responsive to a very rigid or maybe I should say intense intervention. So I want to offer that because I can share some of these ideas with you. What is manageable, what's sustainable is up to you. But knowing that type 1 diabetes has nerve damage, that's much harder to reverse, but not impossible for a small amount reversal. And then there's the blood vessel damage. And so in addition to medication, I always look at these metabolic factors and then you can decide what might work for you. But using intense lifestyle management can actually have a positive impact on the blood vessels and the blood flow. When I have patients who develop neuropathy from uncontrolled blood sugar, it is partly reversible, but it's much slower because nerves heal more slowly than blood vessels. But it is and I don't know because I don't do, like, EMGs on them, which is like, nerve testing. But I do know that they. Like, I've had patients come in, like, I've had this for years. And if we can get their blood sugar down over a course of, like, three years, it that we know that their sensation comes back. Doesn't always. But they're like people with, like, 30 years of disease.",
                endTime=856141, 
                speaker="Doctor",
                startTime=567136
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=797424, 
                speaker="Patient",
                startTime=797024
            ),
            TranscriptEntry(
                content="So just kind of throw that out there just to sort of give bigger context about the ability to make a difference in your functioning. So that's, like, one thing. And so when we do the fasting labs that he's looking at, we're looking at other points of intervention in order to kind of optimize those, in order to think about what could. What we can do to optimize your baseline function. Okay, that was just a lot of information. Did you have any questions about that?",
                endTime=828920, 
                speaker="Doctor",
                startTime=797472
            ),
            TranscriptEntry(
                content="No, but I do remember him talking about the. The medications, whether it's the Sudafest, the other one addition to the D. Because obviously this take some time, but I think it'd be more beneficial if we could try to reverse it. It's going straight for.",
                endTime=850840, 
                speaker="Patient",
                startTime=830020
            ),
            TranscriptEntry(
                content="Yeah, you broke up just a tiny bit. So you said make some steps towards potentially reversing it versus jumping into iui. Is that what you said?",
                endTime=862646, 
                speaker="Doctor",
                startTime=853470
            ),
            TranscriptEntry(
                content="Yeah, I think that would probably be beneficial. And then, you know, if it comes to the point where we still do go ahead and do the iui, then at least we're still working towards trying to resolve the problem, whether it fixes itself or not.",
                endTime=877090, 
                speaker="Patient",
                startTime=862718
            ),
            TranscriptEntry(
                content="Yeah, no, I agree. And the reason I brought up the difference of, like, scrotal or the size the testicles and inflammation blood flow is because that will improve the quality of the. The semen to begin with. So these were all just. The more I thought about your case, I thought, well, you're a slightly different presentation, but I've actually addressed this for years in other patients and just a different setting, and there's a lot of. There's a lot to hope for, so I'm very happy to work with that. One of the. We do need to do the labs first. And then the other thing that I don't know. Had anyone ever done EKG to look at your cardiac function? So they put the stickers on the chest and it, like, prints out a little?",
                endTime=934060, 
                speaker="Doctor",
                startTime=878920
            ),
            TranscriptEntry(
                content="Yes, I think so. I feel like I've definitely had that done. I've done. I mean, like you mentioned, what was it? The nerve testing one. I've done that before. Not Fun.",
                endTime=438344, 
                speaker="Patient",
                startTime=935170
            ),
            TranscriptEntry(
                content="No, it's not.",
                endTime=951830, 
                speaker="Doctor",
                startTime=950610
            ),
            TranscriptEntry(
                content="I've done that at some point. Were you able to find one of those in my records? I'm guessing not.",
                endTime=217177, 
                speaker="Patient",
                startTime=952770
            ),
            TranscriptEntry(
                content="I did not yet receive any records and I. I'm not entirely sure if it's because I didn't ask yet, because I do all of it myself, or I just didn't get it yet.",
                endTime=437555, 
                speaker="Doctor",
                startTime=959250
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=970212, 
                speaker="Patient",
                startTime=969708
            ),
            TranscriptEntry(
                content="So it doesn't change my plan for today, but I will make sure that for the follow up I have some access to it.",
                endTime=980756, 
                speaker="Doctor",
                startTime=970276
            ),
            TranscriptEntry(
                content="Yeah. Is that, that's where there's like. It's three stickers, right? Yeah, yeah, I've done that.",
                endTime=986640, 
                speaker="Patient",
                startTime=980908
            ),
            TranscriptEntry(
                content="Yeah. Three is like a minimal one.",
                endTime=989924, 
                speaker="Doctor",
                startTime=988060
            ),
            TranscriptEntry(
                content="Yeah, yeah.",
                endTime=991380, 
                speaker="Patient",
                startTime=990012
            ),
            TranscriptEntry(
                content="Just because again, it helps me understand like the vascular neurological status. So, yeah, if you. So that's. I'll put that into the work. Not the workup, but the history and I'll get a handle on that eventually. So for the lab work, it is fasting. It is somewhat repetitive for what you already had, except it will include things like your insulin level, your cortisol, your testosterone, and not just the testosterone, but also something called dhea, which is what your adrenals create with testosterone and in order to make testosterone. So there'll be kind of a bigger panel than you would normally do. And it is fasting. It is typically supported by the diagnoses of, of infertility. But also just because you already have a baseline diabetic vasculopathy, then that should be appropriate coverage for that.",
                endTime=1056940, 
                speaker="Doctor",
                startTime=992360
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1058180, 
                speaker="Patient",
                startTime=1057320
            ),
            TranscriptEntry(
                content="Do you use local hospital or Medstar Health What would be. What would make the most sense? We have contracts with Medstar Health and St. Mary so that what we send to them electronically comes right back. But we can be flexible.",
                endTime=1077360, 
                speaker="Doctor",
                startTime=1059320
            ),
            TranscriptEntry(
                content="I think I've gone to St. Mary or normally I just go to Montgomery General Hospital. Is there. Would there be like you know of. Because I'm.",
                endTime=1089708, 
                speaker="Patient",
                startTime=1078580
            ),
            TranscriptEntry(
                content="I can. Let's see. I would say I can do Cardston Hospital if that's easier for you. They do eventually get their results back to me. St. Mary is more direct. They. They just kind of land back in the charts. It's a little bit less work on my end if you're open to St. Mary and depends how hard it is to get to. It's a one time lab first thing in the morning.",
                endTime=1114960, 
                speaker="Doctor",
                startTime=1089804
            ),
            TranscriptEntry(
                content="Yeah, I'm open to St. Mary. I just have to make sure I know where one is around me. I think there's one in James, if that's the right one I'm thinking of.",
                endTime=1124540, 
                speaker="Patient",
                startTime=1115260
            ),
            TranscriptEntry(
                content="I think you're correct.",
                endTime=1127620, 
                speaker="Doctor",
                startTime=1125640
            ),
            TranscriptEntry(
                content="Okay. Yeah, I'm open to doing that.",
                endTime=1134620, 
                speaker="Patient",
                startTime=1130200
            ),
            TranscriptEntry(
                content="Yes, there's a St. Mary in Matamoras, is that close enough to you? One in Waynesville, one in Franklin.",
                endTime=1147710, 
                speaker="Doctor",
                startTime=1136600
            ),
            TranscriptEntry(
                content="Yeah, I think. Yeah, I could. I could make that work. My job is pretty flexible. As long as I let them know ahead of time, I can just come into work late.",
                endTime=1160150, 
                speaker="Patient",
                startTime=1149970
            ),
            TranscriptEntry(
                content="Okay. All right, so I'll make sure we do them in St. Mary. So the list would be similar to what Dr. Mozal sent you, but a little bit refined, because I don't want to do a glucose tolerance test right now. The role of a glucose tolerance test is interesting if it would be easier, actually, and I think we talked about this, for you to actually track your blood sugar on your glucose monitor. So the role of a glucose tolerance test is to see is your blood sugar spiking too high? What? We can't see it because we're only getting a fasting glucose, but you're already paying attention to that. I'm sure your endocrinologist is all over your postprandial levels. So if you're not having alarms where you're giving yourself extra insulin, you know, two hours later, then your. Your baseline is what we're interested in. Do you end up chasing your blood sugar a lot?",
                endTime=1222550, 
                speaker="Doctor",
                startTime=1160850
            ),
            TranscriptEntry(
                content="Enough.",
                endTime=1223786, 
                speaker="Patient",
                startTime=1223330
            ),
            TranscriptEntry(
                content="What do you end up, like, trying to correct your blood sugar after the fact a lot? Or do you feel pretty confident on your ratios?",
                endTime=1231230, 
                speaker="Doctor",
                startTime=1223858
            ),
            TranscriptEntry(
                content="It depends on my activity levels. I know if I'm going to the gym for like two days in a row, then sometimes I'm chasing it a little bit more. But it's usually pretty consistent where my numbers are, my ratios are accurate, what I'm eating.",
                endTime=1247220, 
                speaker="Patient",
                startTime=1233000
            ),
            TranscriptEntry(
                content="Great. Okay. I'm not too worried then. You haven't have an advantage over a lot of other people in that particular sphere. Okay, just adding something here before I. And your weight's been pretty stable.",
                endTime=1273470, 
                speaker="Doctor",
                startTime=1247720
            ),
            TranscriptEntry(
                content="Yes. Yep. It was strange because when I was in high school and college, it was pretty much the same weight all the time. And then it went down really low, obviously, when I was sick. But then as soon as I started actually treating the diabetes, it got up to a normal weight for how tall I am. So it was all those years where I could have weighed more, but I didn't because probably the diabetes.",
                endTime=1296670, 
                speaker="Patient",
                startTime=1273970
            ),
            TranscriptEntry(
                content="Yeah, that's so interesting how that sneaks up on people. So what Dr. Mozal recommended for labs. I usually do a basic metabolic panel, but you've probably had that recently, so we don't need to over. Unless your kidney function is something they're watching carefully. Has that been good recently?",
                endTime=1338460, 
                speaker="Doctor",
                startTime=1298290
            ),
            TranscriptEntry(
                content="Yeah, it's been fine as far as I know.",
                endTime=1342220, 
                speaker="Patient",
                startTime=1339960
            ),
            TranscriptEntry(
                content="Okay, so we'll just get a single glucose level so that I can see what it is at the same time as your insulin level. And then we'll get the cortisol. It's called a D. And I'll send you a list of these labs so you don't have to remember them. Okay. The dhea, testosterone. He actually always has us check the lower hormones. Lower for you. Estradiol and progesterone. LH and FSH are. We associate with a woman's ovaries and her cycles, but actually, men produce that, too. It's part of regulating the testicles and then thyroid, which I know they're probably watching.",
                endTime=1394120, 
                speaker="Doctor",
                startTime=1342680
            ),
            TranscriptEntry(
                content="Yeah, I think sometimes. Yeah, I think I just had an FSH test, but I could be wrong. Three or four months.",
                endTime=1410320, 
                speaker="Patient",
                startTime=1399100
            ),
            TranscriptEntry(
                content="Was it a TSH for thyroid?",
                endTime=1414840, 
                speaker="Doctor",
                startTime=1412380
            ),
            TranscriptEntry(
                content="No, it was part of an infertility workup. All I. I'm not sure, but I know that the urologist I went and saw at Cardston Hospital, like, maybe four or five months ago ordered it, and I had that one done.",
                endTime=1431400, 
                speaker="Patient",
                startTime=1416260
            ),
            TranscriptEntry(
                content="Okay, so before I go reordering tests, who is the urologist that you saw so I can get their results?",
                endTime=1439400, 
                speaker="Doctor",
                startTime=1432180
            ),
            TranscriptEntry(
                content="Well, let's see here. I'm gonna have to try to pull up his name because I don't remember it. It's like.",
                endTime=1446920, 
                speaker="Patient",
                startTime=1441680
            ),
            TranscriptEntry(
                content="No, it's okay.",
                endTime=1448220, 
                speaker="Doctor",
                startTime=1447040
            ),
            TranscriptEntry(
                content="It starts with a V. It's like this. Guys, he's a Cardston Hospital doctor. Not that I would see. I would have to look him up. Let me see if I can switch apps here. I'm on my phone. Let's see. I should be able to pull up his name real quick here. See?",
                endTime=1494360, 
                speaker="Patient",
                startTime=1448640
            ),
            TranscriptEntry(
                content="Nope.",
                endTime=1497484, 
                speaker="Doctor",
                startTime=1496980
            ),
            TranscriptEntry(
                content="That's the nurse. It's Kareen Heitshusen.",
                endTime=8422037, 
                speaker="Patient",
                startTime=1497532
            ),
            TranscriptEntry(
                content="Okay. Okay. I will get those labs. I definitely don't have that, so I'll get those labs, especially if they're done the last 90 days so I don't repeat anything. And then get a copy. I'll call this afternoon and see if I can get them and then get a copy to get to you. A list of the labs ordered at St. Mary.",
                endTime=1542032, 
                speaker="Doctor",
                startTime=1520120
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1542704, 
                speaker="Patient",
                startTime=1542176
            ),
            TranscriptEntry(
                content="To kind of augment that. In the meantime, once that we have those back, the recommendation to do for medication is something that can be started more or less immediately after that. And was there anything you had, like, already read about or already done any research on, on your own that you wanted to take a look at?",
                endTime=1568820, 
                speaker="Doctor",
                startTime=1542752
            ),
            TranscriptEntry(
                content="I mean, not much more Than, than like I read a couple studies on like pseudoephedrine and other similar type medications that helped some patients. So other than that I did read something about what was. Was like testosterone therapy. But obviously that would mean that my testosterone levels would have to be really low. Yeah. Even be considered. But I was curious about that too. But those. Yeah. Not. Not much more. I mean I.",
                endTime=1607904, 
                speaker="Patient",
                startTime=1572010
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1608624, 
                speaker="Doctor",
                startTime=1607992
            ),
            TranscriptEntry(
                content="Yeah, I'm okay.",
                endTime=1610420, 
                speaker="Patient",
                startTime=1608752
            ),
            TranscriptEntry(
                content="Okay. So I will support a testosterone definitely is knowing what levels so how much your levels were affected and then so we'll kind of cross that bridge when we get there. I'll ask Dr. What the first recommendation he has is in your particular situation. So if it's pseudo fed that's not too hard to get. But I'm sure there's a specific dosing for it. It's certainly a new. He wrote in his letter. He didn't write the actual dosing. Did he tell you what the dose was?",
                endTime=1655096, 
                speaker="Doctor",
                startTime=1610920
            ),
            TranscriptEntry(
                content="Just a standard dose or not that I can recall. Okay.",
                endTime=1660424, 
                speaker="Patient",
                startTime=1655168
            ),
            TranscriptEntry(
                content="I'll reach out to him and send you a message through summerland health centre.",
                endTime=1664424, 
                speaker="Doctor",
                startTime=1660552
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1665160, 
                speaker="Patient",
                startTime=1664552
            ),
            TranscriptEntry(
                content="And so that will be. So the TBRA message is for like just specific questions for medical things. When I send you the list of labs or a copy of the lab order it will be through bon secours health system which is like our third party HIPAA secure function for sending information. The emails are really just for scheduling issues. Okay. Or just something very generic. So I'll ask urology for their. For their information and then do you mind if I ask your endocrinologist for their recent labs too?",
                endTime=1698250, 
                speaker="Doctor",
                startTime=1665240
            ),
            TranscriptEntry(
                content="No, that's fine. I actually switched from the endocrinologist I was with because he was a pediatric endocrinologist and I went to primary care doctor who since I switched to him last year has been not very helpful. And I think he only deals with type two diabetics because.",
                endTime=1719730, 
                speaker="Patient",
                startTime=1698950
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=1720838, 
                speaker="Doctor",
                startTime=1720270
            ),
            TranscriptEntry(
                content="How to fill the prescriptions multiple times. So I think I'm going to switch to a full time endocrine.",
                endTime=1726970, 
                speaker="Patient",
                startTime=1720934
            ),
            TranscriptEntry(
                content="Yeah. Yeah.",
                endTime=1728614, 
                speaker="Doctor",
                startTime=1727470
            ),
            TranscriptEntry(
                content="I scheduled. Well my wife scheduled an appointment for August 9 to set up with a new one. So right now I don't currently have an endocrinologist. I have Dr. Bosquet who is the primary care doctor but he not. I don't think he knows much about type 1 diabetics or maybe his nurses don't. I don't know. An interesting feeling I got going to him over there for one appointment.",
                endTime=1754220, 
                speaker="Patient",
                startTime=1728742
            ),
            TranscriptEntry(
                content="So if you need something, I'm happy. I like I said I have a couple of type one because I'm. Because I medicine and pediatrics part of pediatric training was extensive amount of type 1 diabetes management in the hospital. And then I diabetes camp for two years and one. It's like. So I have a lot more understanding of the distinctions between the two. So if you run up against a wall between now and your adult endo appointment, I'm happy to help you out. But having your own endocrinologist because there's so many nuances that I can't keep up with is definitely important. So. But yeah, if you need something like insulin refilled or it's just nothing's coming out in the right. Yeah, I'm pretty comfortable with. With all that.",
                endTime=1798174, 
                speaker="Doctor",
                startTime=1754300
            ),
            TranscriptEntry(
                content="So. Okay.",
                endTime=1799650, 
                speaker="Patient",
                startTime=1798262
            ),
            TranscriptEntry(
                content="Part of my part and parcel of my training, so. So I'll reach out. Who is your peds endocrinologist? You can also just message me about that.",
                endTime=1809838, 
                speaker="Doctor",
                startTime=1800310
            ),
            TranscriptEntry(
                content="But it was.",
                endTime=1812606, 
                speaker="Patient",
                startTime=1809894
            ),
            TranscriptEntry(
                content="Or was that at Cardston Hospital",
                endTime=1814770, 
                speaker="Doctor",
                startTime=1812758
            ),
            TranscriptEntry(
                content="I think it was Cardston Hospital, but now it's with someone else. It's Icker Israelsen.",
                endTime=1822460, 
                speaker="Patient",
                startTime=1816560
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1824060, 
                speaker="Doctor",
                startTime=1823200
            ),
            TranscriptEntry(
                content="Yes. I don't think it. I don't think it's Cardston Hospital now. I think it's something else. But yes, Icker Israelsen is a pediatric endocrinologist.",
                endTime=1835820, 
                speaker="Patient",
                startTime=1824720
            ),
            TranscriptEntry(
                content="Okay. So if I can. If they separating your labs are all at Cardston Hospital, I'll just go through Cardston Hospital so too. Okay. And did I send you the release of information form?",
                endTime=2048709, 
                speaker="Doctor",
                startTime=1836320
            ),
            TranscriptEntry(
                content="Yes, I believe I filled that out and sent it back. Did it not show up or did. There's not on it.",
                endTime=1854190, 
                speaker="Patient",
                startTime=1847722
            ),
            TranscriptEntry(
                content="Probably me trying to keep up with the influx and output of paperwork. So let me double check here. I'm gonna. In all places. You can just assume it's me and make sure I've got this. I've got credit cards, a lot of documents in here. Where's the.",
                endTime=1885930, 
                speaker="Doctor",
                startTime=1854730
            ),
            TranscriptEntry(
                content="Yeah, I can always up when I sent it back to you too.",
                endTime=1898660, 
                speaker="Patient",
                startTime=1893840
            ),
            TranscriptEntry(
                content="No, perfect. You had it done. I got it.",
                endTime=1913480, 
                speaker="Doctor",
                startTime=1910400
            ),
            TranscriptEntry(
                content="Oh, okay, perfect.",
                endTime=1914936, 
                speaker="Patient",
                startTime=1913520
            ),
            TranscriptEntry(
                content="No problem. Perfect. I will get on top of that this afternoon. So. Yeah, there we go. There's. That's it for today. I'm going to get some of this stuff sorted out and so I would imagine by Friday I'll get you the list of labs. Fasting is nothing but water for eight hours or ideally 10, if you're okay with that. You'll have to tweak the insulin a little bit and then drink plenty of water. You can have some black coffee or black tea, but no milk, sugar or dairy, any type of dairy.",
                endTime=1949280, 
                speaker="Doctor",
                startTime=1915048
            ),
            TranscriptEntry(
                content="Okay.",
                endTime=1950000, 
                speaker="Patient",
                startTime=1949440
            ),
            TranscriptEntry(
                content="All right, sounds good, Ocie. Thanks for your time. And then I will also ask Dr. Mozal for specific dosing to be able to give you those instructions.",
                speaker="Doctor",
                endTime=1960580, 
                startTime=1950080
            )
        ]
    )

    patient3 = Patient.find_or_create_by(
        doctor_id=doctor.id,
        name="Samwise Gamgee",
        date_of_birth=datetime.strptime("2019-12-25", "%Y-%m-%d"),
    )

    appointment3 = Appointment.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient3.id,
        appointment_date=datetime.now(),
    )

    # This is real patient data that has been de-identified using the Azure
    # de-identification service.
    AIVisitTranscript.find_or_create_by(
        doctor_id=doctor.id,
        patient_id=patient3.id,
        appointment_id=appointment3.id,
        duration=1256,
        transcript_data=[
            TranscriptEntry(
                content="Rivera Street place. Because you're six. Yeah. Right? Yeah. You want to sit up here? Great. Ms's going to ask you some questions. Okay. All right. Okay. Well, how are you doing? What things do you have to play with? How do you go to school? Do you do school at home?",
                endTime=15032,
                speaker="Doctor",
                startTime=80
            ),
            TranscriptEntry(
                content="I did school at home and at school.",
                endTime=19060,
                speaker="Patient",
                startTime=15216
            ),
            TranscriptEntry(
                content="Cool. He does a hybrid. Yeah. That's awesome. Do you like, do you have friends at your school that you play with, that you go to? Yeah. That's pretty awesome. What do you like to do? You like to play any music or play any sports?",
                endTime=35970,
                speaker="Doctor",
                startTime=19440
            ),
            TranscriptEntry(
                content="We have like a little playground at our school. The back of it.",
                endTime=44926,
                speaker="Patient",
                startTime=36310
            ),
            TranscriptEntry(
                content="That's awesome. What's your favorite thing on the playground?",
                endTime=48690,
                speaker="Doctor",
                startTime=45078
            ),
            TranscriptEntry(
                content="I just say the goggle bar.",
                endTime=53770,
                speaker="Patient",
                startTime=51430
            ),
            TranscriptEntry(
                content="That's a good one. You had your appointment last week. So are you in first grade, is that right? What grade are you in?",
                endTime=66640,
                speaker="Doctor",
                startTime=54700
            ),
            TranscriptEntry(
                content="Kindergarten.",
                endTime=68932,
                speaker="Patient",
                startTime=67900
            ),
            TranscriptEntry(
                content="Kindergarten. Who's your teacher? Yeah.",
                endTime=79508,
                speaker="Doctor",
                startTime=68996
            ),
            TranscriptEntry(
                content="Ms. Palmatary and Mrs. Mumin Danielle.",
                endTime=82100,
                speaker="Patient",
                startTime=79604
            ),
            TranscriptEntry(
                content="Do you like your teachers?",
                endTime=86096,
                speaker="Doctor",
                startTime=84680
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=86816,
                speaker="Patient",
                startTime=86208
            ),
            TranscriptEntry(
                content="Good. I'm so glad. Mom. Have any concerns? Oh, no, no. He's doing really well. Don't have any concerns. I know we're putting Jackie's going to speech. I mean I think his speech is good. I can tell he's got a little bit of like a tongue thrust. I don't know, I mean if we feel like it'll. His speech has improved greatly with that. Like sure. On its own. But I don't know, when you go to speech with him, you maybe just bring him and just be like you just. Yeah. Do you think any, you know, could you take a quick listen and if it's worth an eval, you know, let me know what you think. Okay. Or she may just even be able to give you like a couple tips. You can start at home for a little while. It is mild. It's mild. Yeah. And it's gotten much better. I love it. But yeah, I do notice. I mean he definitely has a little bit of a tongue thrust for sure. But I mean other than that he's doing really well in school. Real social. I love it. Yeah. My Osiris, our 8 year old, he was really mild speech at this age and I never did anything and I wish I had because he still has stuff now at 8 and now it's socially he's more aware. Okay. So I will say it's now I'm like, I've tried. I'm trying to put him as feed, you know, like good to know. Okay. I mean if we're going to take one. I know. Yeah, exactly. It's like well, if we're going or if we're going. Yeah. So we're. I'm happy to put the referral in our. If you want to first. Just, like, get there and get in and see what we like. Yeah, we have him scheduled for the 29th, so. Oh, good. That's great. Perfect.",
                endTime=186678,
                speaker="Doctor",
                startTime=86928
            ),
            TranscriptEntry(
                content="Get down.",
                endTime=187578,
                speaker="Patient",
                startTime=186744
            ),
            TranscriptEntry(
                content="Eating lots of. Yes. His fruit, vegetables diet is great. It's really picked up. He used to be picky, but I feel like we're finally starting to leave that phase behind. Hey, boys. Boys Drinking? Mostly water. Any juice? No, not really. I mean, occasionally, like, it's a treat, but not really. What about milk? He does, like, one glass with dinner, so maybe like. I mean, not even 8 ounce glass. Maybe like 6 or so. All right. And then one soft stool a day normally. Most days, yeah. He's either every day or every other day. Okay. And then go and potty. Go and pee. Normal. No concerns there. Okay. All right. And then sleep well. Good job. I love it. No concerns. You brush your teeth every day?",
                endTime=250930,
                speaker="Doctor",
                startTime=187714
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=252170,
                speaker="Patient",
                startTime=251310
            ),
            TranscriptEntry(
                content="Brushing once or twice or twice a day. Twice a day also.",
                endTime=256290,
                speaker="Doctor",
                startTime=253070
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=260250,
                speaker="Patient",
                startTime=259470
            ),
            TranscriptEntry(
                content="Do you get any, like, a sticker or anything when you go to the dentist?",
                endTime=265030,
                speaker="Doctor",
                startTime=261550
            ),
            TranscriptEntry(
                content="I get a goodie bag.",
                endTime=266614,
                speaker="Patient",
                startTime=265190
            ),
            TranscriptEntry(
                content="A goodie bag. They have the best stuff.",
                endTime=270862,
                speaker="Doctor",
                startTime=266662
            ),
            TranscriptEntry(
                content="Brush and toothpaste and cloth and all that stuff.",
                endTime=276530,
                speaker="Patient",
                startTime=271046
            ),
            TranscriptEntry(
                content="Going twice a year to the dentist? Once. Yeah. Yep. Every six months. Yeah. And then are you active? Do you like playing outside? Yeah. Yeah. Nice.",
                endTime=287870,
                speaker="Doctor",
                startTime=276870
            ),
            TranscriptEntry(
                content="But not when it's raining, of course.",
                endTime=290702,
                speaker="Patient",
                startTime=288030
            ),
            TranscriptEntry(
                content="You get wet. Any behavior concerns? No, no. He does suck. Any screen tone? He does some. I'd say probably like an hour, maybe two hours, but, yes, he does. He loves the show. Loves the. Loves the iPad, but he doesn't do too much. Okay, great. What kind of games do y'all do on the iPad? He does. What do you like? Grandpa got him on Angry Birds. He likes. Grandpa got Austyn's on lots of things. And then we did that Creekside Grill or that Bamboo Books likes that. He likes Dr. Marlowe. I don't know if you know the Dr. Marlowe seepersaud. He likes those.",
                endTime=338910,
                speaker="Doctor",
                startTime=290806
            ),
            TranscriptEntry(
                content="I like Trouble.",
                endTime=342498,
                speaker="Patient",
                startTime=341290
            ),
            TranscriptEntry(
                content="You like Trouble? Trouble, yeah. You got trouble for your birthday. Oh, fun. All right, well, I think that's. That's mostly all I have, unless you. Yeah, no, he's. We don't have any concerns. He's been doing really well. Is he reading and. Yeah, he's. Yes. Yeah. Aren't you. You're reading. Oh, my goodness. He's really good at math. Oh, my God. He's like a little math guy.",
                endTime=367600,
                speaker="Doctor",
                startTime=342594
            ),
            TranscriptEntry(
                content="Four plus four is eight.",
                endTime=371400,
                speaker="Patient",
                startTime=368580
            ),
            TranscriptEntry(
                content="I don't know. He had a lot of, like, ear infection problems early on. We had two. Speech was a little right. And hearing was never a problem, but kind of had just like, a lot of some mouth breathing. We considered taking his adenoids out and decided against it. And, you know, we were gonna put in a second set of tubes and kind of just decided, let's try to just go with it. And he just got the moringoplasty. And so far he's been okay. But I mean, I have noticed. I mean, all my kids just have kind of, like, I don't know, mouthy stuff. Mouth. I don't know. I mean, he looks so wide. Yeah. I mean, very wide. His teeth, like, the more I've kind of dung down the. The rabbit hole. I mean, like, their pallets seem nice and wide. Their teeth are nice and spaced. Like, it doesn't seem like that He. I don't know if he has like, an untreated tongue tie or something that has, like, kind of contributed to some of his issues. But I mean, he. It doesn't cause any, like, problems in his, like, daily living other than a little bit of, like, the speech, which may. I mean, usually. But the mouth breathing has improved. Like, that's what we were considering, the adenoids. But I feel like it has improved. I'll still catch him every once in a while. Like, if I come in before he wakes up, notice that his mouth is open. But I mean, he's not like, snoring. Yeah. Like anything and all. No apnea or anything. Yeah. And like, I'll even tell him, like, close your mouth, try to breathe through your nose. And he. Pretty much. Some of it's just habitual with the language stuff. It's like I started saying with the or suh, you know, and I just created that pattern in my brain. I just gotta retrain it. Yeah. It could have nothing to do with being like, tethered. Yeah. Because usually that's more of a la la. Like I can't reach through my mouth. And not as this sound is completely different. It doesn't really need your tongue that much, you know, so it's probably just more that he stuck with a bad pattern. Yeah. So don't blame yourself. Like, helmets on bikes, scooters, sunscreen outside. And then, you know, safe water play safety around strangers. Great. All right. Awesome. Should we see how big you are, buddy? Look, you think you can step up on that scale? Yeah, we gotta wait till it says zero. Okay. You haven't lost any teeth yet, right? Just one oh, you have. And it's. The one that's coming in is huge. Really? Oh, man. Oh, we gotta take your shoes off. You want to take your shoes off, buddy? Oh, yeah, sorry. You can see how tall you are. And then get on there and stand up real straight.",
                endTime=540550,
                speaker="Doctor",
                startTime=377060
            ),
            TranscriptEntry(
                content="That's weird.",
                endTime=541930,
                speaker="Patient",
                startTime=540710
            ),
            TranscriptEntry(
                content="Dimitra, let's do everything. Hey, stand up real straight. Tag, get your head off of there. We can't lean on there yet. Okay, 46.8. Hey, Tag, step off and do it. Do it. We're. Stand up real tall. Okay, Don't. He's. He's been about 49. Okay. Oh, watch out, buddy. Okay, it's Tag'T's turn. Okay, now go. Just stand up real straight. Oh, well, I don't know. Okay. 46.8. Okay, we weigh him at home, but I mean, it's different still. 55th percentile, so. Yeah, nothing concerning long. And lean all the way back. Okay. And then head straight up. Okay, you're good. You can step out 47 and a little over. 47 and a half. Yeah. Good job. Whoa. You're 35.6. High five, man. Oh, yeah, that's good. 85th percentile for a second. Yeah. Yeah, sounds about right. Oh, my goodness. Naked feet. Do you think you can sit up here so we can listen to your heart?",
                endTime=619980,
                speaker="Doctor",
                startTime=544110
            ),
            TranscriptEntry(
                content="I just gotta get my shoes on.",
                endTime=621728,
                speaker="Patient",
                startTime=620150
            ),
            TranscriptEntry(
                content="No problem. He had it at his old pediatrician. I mean, they did like, you know, the checks every so often, but he's never been to, like, on your arm. Okay. Have you had this time before? Okay. Yeah, because he knows his letters. You had one time. Okay. Okay. So we're gonna roll your sleeve up.",
                endTime=648840,
                speaker="Doctor",
                startTime=621784
            ),
            TranscriptEntry(
                content="I know.",
                endTime=650280,
                speaker="Patient",
                startTime=649300
            ),
            TranscriptEntry(
                content="All right. I'll do anything. For spring break. We don't have plans. We. We might go to Massachusetts and visit some family and things shouldn't get such a long, long break, but no big plan. So. Okay, we turn it on. I don't. Okay. There you go. Hey, it's gonna get a little tight. Okay. Put her some shoes for a short bit. Can you remember some of your friends names from school?",
                endTime=685008,
                speaker="Doctor",
                startTime=652660
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=685680,
                speaker="Patient",
                startTime=685144
            ),
            TranscriptEntry(
                content="Yeah. What are some of their names?",
                endTime=687528,
                speaker="Doctor",
                startTime=685760
            ),
            TranscriptEntry(
                content="Gooley, Bassam. Blane.",
                endTime=692784,
                speaker="Patient",
                startTime=687728
            ),
            TranscriptEntry(
                content="Tyshen. Yeah. So many boys. Yes, there are a lot of boys around us.",
                endTime=698688,
                speaker="Doctor",
                startTime=692912
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=699152,
                speaker="Patient",
                startTime=698744
            ),
            TranscriptEntry(
                content="Yeah. For some of the girls. You remember any of the girls, Larkynn. Oh, yeah. Larkynn. Larkynn. All very musical names. Yes. And. Okay. Did you go to school yesterday? So long ago. Yeah. That's hard to remember. Did you do anything fun at school yesterday? Fun at. Hey, Jammie. Don't touch me. Let's try one more time. Okay. Sometimes if we we just relax like a statue. Did you go to the park yesterday at school? Did we play after school? And Le did too. That's so fun. That's amazing. Don't lean back anymore. I don't want you to fall off. Yeah, don't hit us. Just want wanted you to know so we don't fall off. That would be no good. What's your favorite show to watch?",
                endTime=758010,
                speaker="Doctor",
                startTime=699216
            ),
            TranscriptEntry(
                content="Like Mouse has a Cookie.",
                endTime=761886,
                speaker="Patient",
                startTime=758950
            ),
            TranscriptEntry(
                content="Mouse has a cookie. Did you give a mouse a cookie? That one. That's a good one. Do you ever watch, like, Little Bear? There we go.",
                endTime=772030,
                speaker="Doctor",
                startTime=761998
            ),
            TranscriptEntry(
                content="Yeah.",
                endTime=772782,
                speaker="Patient",
                startTime=772150
            ),
            TranscriptEntry(
                content="Yeah. What should we do? YouTube.",
                endTime=779500,
                speaker="Doctor",
                startTime=772926
            ),
            TranscriptEntry(
                content="I hope that doesn't really hurt you because I'm going to tie up.",
                endTime=785080,
                speaker="Patient",
                startTime=780320
            ),
            TranscriptEntry(
                content="He's so brave. You're brave, too. You did such a good job. All right, bud, you ready? It's going to squeeze. There it goes. 92 over 51. Okay, that's pretty tight. That's kind of funny, isn't it?",
                endTime=805230,
                speaker="Doctor",
                startTime=785120
            ),
            TranscriptEntry(
                content="I never heard that sound.",
                endTime=808546,
                speaker="Patient",
                startTime=806010
            ),
            TranscriptEntry(
                content="Yeah, that's silly. Well, Kimberlie listens to your heart. I'm gonna listen to your lungs. Back here. Two sides. Wow. Can you take a couple deep breaths? Here you go. Good. Good job. Deep breath. Yeah, I know. She letting me do that.",
                endTime=828430,
                speaker="Doctor",
                startTime=808658
            ),
            TranscriptEntry(
                content="Ew.",
                endTime=832714,
                speaker="Patient",
                startTime=832050
            ),
            TranscriptEntry(
                content="Ew. Looks so great. When's your birthday? You remember what month? Not December, but January. Okay. And not the first, but second, the second. Did you have a birthday like celebration?",
                endTime=850590,
                speaker="Doctor",
                startTime=832842
            ),
            TranscriptEntry(
                content="Did you have a birthday party at the oceanview high place?",
                endTime=225490,
                speaker="Patient",
                startTime=851170
            ),
            TranscriptEntry(
                content="That is so fun.",
                endTime=739044,
                speaker="Doctor",
                startTime=854802
            ),
            TranscriptEntry(
                content="Bowling alley.",
                endTime=337804,
                speaker="Patient",
                startTime=856162
            ),
            TranscriptEntry(
                content="Was it fun? Yes.",
                endTime=859444,
                speaker="Doctor",
                startTime=858050
            ),
            TranscriptEntry(
                content="I just got three stripes.",
                endTime=861188,
                speaker="Patient",
                startTime=859492
            ),
            TranscriptEntry(
                content="Three stripes? No way. It was our first time ever bowling to your heart. They loved was a lot of that. We invited some of his little classmates and stuff, and I think it was a lot of kids first time bowling. Did you use the little ramp that you rolled a ball down? Do you remember?",
                endTime=879000,
                speaker="Doctor",
                startTime=861284
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=880116,
                speaker="Patient",
                startTime=879500
            ),
            TranscriptEntry(
                content="You did say some, some with, some without. Tried it both ways. So fun.",
                endTime=885318,
                speaker="Doctor",
                startTime=880228
            ),
            TranscriptEntry(
                content="There's a Will. Will.",
                endTime=888050,
                speaker="Patient",
                startTime=885454
            ),
            TranscriptEntry(
                content="Ta da. You did such a good job. All right, let me see your beautiful eyes. Can you look at my nose right here?",
                endTime=642290,
                speaker="Doctor",
                startTime=891630
            ),
            TranscriptEntry(
                content="Yes.",
                endTime=831144,
                speaker="Patient",
                startTime=899134
            ),
            TranscriptEntry(
                content="Don't let it run away. Good. That was bright, wasn't it? That was so bright. My Oops. I found. Okay, great job. That was so good. Right? We'll see his box now. All right, let's look in those ears. You think we're gonna find anything in there?",
                endTime=920864,
                speaker="Doctor",
                startTime=900070
            ),
            TranscriptEntry(
                content="No. Please watch it.",
                endTime=923020,
                speaker="Patient",
                startTime=921032
            ),
            TranscriptEntry(
                content="Jackie just doesn't want to be left out. I know. Let me see the other side. Good job. All right. Miss's gonna do the same. I'll start over here since you already got this. Nice Big ear canals. Makes it easy. Look good. I haven't looked in his ears in a really long time. Okay. Yeah, perfect. Looks great. All right, other side. You're such a good listener. Are you this good of a listener at home, too?",
                endTime=947910,
                speaker="Doctor",
                startTime=923480
            ),
            TranscriptEntry(
                content="Yeah, but I can do.",
                endTime=950690,
                speaker="Patient",
                startTime=948030
            ),
            TranscriptEntry(
                content="You look so good. You want to try it? You can use it. All right. I'm going to feel your neck with my cold hands, okay? I know. It's kind of tickly, isn't it? It's like a massage. Does your mommy ever give you a massage? Yes, she does. She's the best mom ever. Just relax, buddy. Good job. All right, Miss's gonna feel, too. Okay, really quick, okay? Take a deep breath. There you go. Good job. Okay, I'm finished. Are you a doctor? All right, can you say, there you go. Stick your tongue out for me. Oh, you already say ah. Ah. Kind of go like this. Very good. Got a little tonsils going in there. Good job. Yeah. Might come out eventually. And I'll just hang out for a minute. Okay, buddy? We're gonna lay back so we can see your belly. Good job. Yeah. Oh, wow. There you go. That looks cozy. Is that cozy?",
                endTime=1013502,
                speaker="Doctor",
                startTime=951150
            ),
            TranscriptEntry(
                content="Yeah, it's cozy.",
                endTime=1015934,
                speaker="Patient",
                startTime=1013566
            ),
            TranscriptEntry(
                content="It is cozy. This table's nice and soft. Sometimes, you know, I want to come in here and take a nap because it's such a soft table. All right, Miss's going to press on your belly. And then is it okay if we take a quick peek at privates to make sure everything looks normal and healthy? Is that okay with you? You can check with Mom. That's okay. It's okay. Yeah, I know. It's kind of silly, isn't it? Only because we're at the doctors and because Mommy's here. Right. Otherwise, that would be ridiculous, wouldn't it? Good job.",
                endTime=1045620,
                speaker="Doctor",
                startTime=1015982
            ),
            TranscriptEntry(
                content="Why is there a door on the ceiling?",
                endTime=1049176,
                speaker="Patient",
                startTime=1046880
            ),
            TranscriptEntry(
                content="That's a good question. I guess just to bring some natural light into the halls. All right, we're gonna check your back, my friend, and then your vision, and we'll be all done. Can you stand up nice and tall for me on the floor? Okay. Turn around and face mom if you want. Good job. Shoulders look nice and even. We're gonna go. Hang your arms down. Loosey goosey, like noodles. There you go. And then keep it nice and still. Perfect. Beautiful. All right. Do you know your letters? I heard you were reading. Yeah, a little bit. Okay, here's what we're gonna do. I want you to stand, turn around. You see my letters on the wall there? They're up here. You see Those letters. Can you read the line? Can you read this line with both your eyes? Tell me what letters you see here.",
                endTime=1110250,
                speaker="Doctor",
                startTime=1049288
            ),
            TranscriptEntry(
                content="D, F, C, D, L, T, T. Good job.",
                endTime=1119588,
                speaker="Patient",
                startTime=1110370
            ),
            TranscriptEntry(
                content="How about this line right here?",
                endTime=1121120,
                speaker="Doctor",
                startTime=1119644
            ),
            TranscriptEntry(
                content="E, T, O. Ok. Just a little. How's that?",
                endTime=1127620,
                speaker="Patient",
                startTime=1121740
            ),
            TranscriptEntry(
                content="Over. Okay, go ahead.",
                endTime=1129252,
                speaker="Doctor",
                startTime=1127660
            ),
            TranscriptEntry(
                content="T, O, D, C, F, O.",
                endTime=1135060,
                speaker="Patient",
                startTime=1129356
            ),
            TranscriptEntry(
                content="Very good. I want you to try this sign one more time.",
                endTime=1137476,
                speaker="Doctor",
                startTime=1135180
            ),
            TranscriptEntry(
                content="Try that one one more time. D, F, F, C, D, C, L.",
                endTime=1146170,
                speaker="Patient",
                startTime=1137548
            ),
            TranscriptEntry(
                content="T, T. Okay, can you cover one I for me? Good job. Can you read this line here?",
                endTime=1153486,
                speaker="Doctor",
                startTime=1146630
            ),
            TranscriptEntry(
                content="D, L, P, E, D, T, C.",
                endTime=1162638,
                speaker="Patient",
                startTime=1153638
            ),
            TranscriptEntry(
                content="How about this one?",
                endTime=1163930,
                speaker="Doctor",
                startTime=1162774
            ),
            TranscriptEntry(
                content="N, E.",
                endTime=1167050,
                speaker="Patient",
                startTime=1165030
            ),
            TranscriptEntry(
                content="Very good. Okay, let's try this line with that. I scoot back just a little bit there. Can you stand on the blue for me? Good job.",
                endTime=1197840,
                speaker="Doctor",
                startTime=1180770
            ),
            TranscriptEntry(
                content="I was just worried it was tape.",
                endTime=1200176,
                speaker="Patient",
                startTime=1197880
            ),
            TranscriptEntry(
                content="Yeah. No, no tape on the floor this time. Okay. So you cover the same eye. I think it was your other eye you were covering. It was that one. Okay, this one here.",
                endTime=1210096,
                speaker="Doctor",
                startTime=1200368
            ),
            TranscriptEntry(
                content="C, F, C, Z, L, T, T.",
                endTime=1215440,
                speaker="Patient",
                startTime=1210168
            ),
            TranscriptEntry(
                content="Can you try this line here?",
                endTime=1217104,
                speaker="Doctor",
                startTime=1215560
            ),
            TranscriptEntry(
                content="That's too small.",
                endTime=1219100,
                speaker="Patient",
                startTime=1217272
            ),
            TranscriptEntry(
                content="Can you switch eyes for me? Good job. How about. What's this letter?",
                endTime=1225288,
                speaker="Doctor",
                startTime=1220440
            ),
            TranscriptEntry(
                content="T, C, P, D, C, E, T, D, C, F, F, C, Z, L, T, T, D.",
                endTime=1251120,
                speaker="Patient",
                startTime=1225464
            ),
            TranscriptEntry(
                content="Can't see him. He's 20, 40. It's like right on the line. So 20, 30, you pass a driving test. Okay. And so that's like you don't have to worry about it. He's still on the younger side. Okay. You could go to an optometrist and they'll do a little machine somewhere accurate. They will do like a sit down and cut your eye test too. Yeah, but he's not compensating one eye over the other. They're both exactly the same. So that's good. Okay. It could just be that it needs a little bit more time. Okay. It's not enough unless he's complaining or you notice anything. I haven't, no. Okay. If you want. Okay. What age do they typically start calling an optometrist? Like for, like just. Unless they just. If they need to, like they screen into it. Okay. Yeah. I mean, any. You can. Yeah, of course. They'll absolutely take your money. Yeah, yeah, you can definitely do it. Or, you know, if it's on the line again next year, then I would go. Okay. Or obviously if he starts claiming you anything. Okay. He's just barely didn't pass like you got. If you missed two letters then in the line and he got all the letters but two, so. All right, guys, that's it. And no vaccines. No. Still not playing. I did kind of want to pick your brain about measles? What are you thinking? What am I thinking? It's a great question. I mean, I feel like I've been trying to follow the information coming out. I feel like the information coming out has been scant and hard to kind of decipher just because the details are so limited. Yeah. But yeah, I mean, I think that like, unfortunately, like kids die from different things all the time. Right. And so our perspective becomes really skewed when it's like there's been a death. Right. Like, you know, there's like 4,000 deaths a year from RSV. Right. And like, but we don't hear about it. So we're not like, oh my gosh, I'm freaked out. You know, and so it's not to say that like we're never ever going to get sick and it's going to be fine. Sure. But the risk is still relatively very, very, very low to catch it. And very, very, very low. And I mean, honestly, you know, you read the vaccine insert and it has all the same complications as getting these losses as like a possible side effect. So you're like, which of the two do I want to risk? Yeah. Yeah. And so I still personally would stand on the side of like, I personally would not get the mmr, but obviously that's what I've been reading so much too about like the vitamin A. Yeah. It seems like, I mean, not even that there's any in our area, but just like thinking ahead, like, you know. Yeah. The studies on that show like significant improvement in terms of mortality and pneumonia, which are like the. Too. Lots of things. Yeah. Like at 86%, you know, like this. Yeah. So it is definitely. Nothing's foolproof. Yeah. Certainly. Okay. To me, relatively when you look at everything, you know, four kids die from the flu every year. Yeah. And there's measles outbreaks every year. You can look up CDC website. Yeah. And so. Yeah, that's my bad. Okay. Okay. I just kind of was curious so just to hear your take. I mean, like I said, I've been trying to follow it, but I know they got their 12 months. He did not. He got one dose of MMR. He got everything until his four year old shots. Okay. We stopped. So he may even be immune. Yeah. Because he had the one dose. He had the one and like 80% of the population with only one dose. So he's not had. He had everything through nine months and then we did some 12 months, but we did not do that more. Yeah. Look at you greeting back to Miss Kimberlie, please. What a good reader. He has been doing really well. He has been doing so good. Thank you. I know. All right. Okay, guys, are you ready? See you later. Go enjoy the sunshine. Yeah. We're going to the zoo. Oh, my goodness. That's fun. Can you put it down, please? Okay. Thanks. I like her little outfit. That's. Oh, thanks. Where did you get it? Oh. Oh, gosh. Lavon's. Maybe. Yeah, I think it's Lavon's. Cute. Thank you. Look how strong is. Sorry. I'm not touching you. Thank you. Bye. Keep me posted on the next speech referral, people. I will. Yeah. Yeah, we're scheduled for the 29th. Great. Any feedback? No, I think you did great. Yeah. I think, obviously, you know, you get a rhythm eventually, as you do more, but it's. I mean, I think really. Well, you're great with patient rapport and being comfortable and building trust right out of the gate, which is awesome. Yeah. Comfortable with you. Yeah. So great.",
                endTime=1590190,
                speaker="Doctor",
                startTime=1256620
            )]
        )
