export interface User {
  name: string
  email: string
  avatar: string
}

export interface NavItem {
  title: string
  url: string
  icon: string
  page: string
}

export interface PatientData {
  height: string
  heightMetric: string
  weight: string
  weightMetric: string
  bloodPressure: string
  heartRate: string
  temperature: string
  temperatureMetric: string
}

export interface PatientComplaints {
  list: string[]
}

export interface Mail {
  name: string
  email: string
  subject: string
  date: string
  location: string
  teaser: string
  content: string
  status?: "completed" | "in-progress" | "not-started"
  appointmentDate?: string
  intakeCompletedAt?: string
  complaints?: PatientComplaints
  patientData?: PatientData
}

export const mockData = {
  user: {
    name: "shadcn",
    email: "m@example.com",
    avatar: "/avatars/shadcn.jpg",
  },
  navMain: [
    {
      title: "Intake",
      url: "#",
      icon: "Inbox",
      page: "intake",
    },
    {
      title: "Notetaking",
      url: "#",
      icon: "File",
      page: "notetaking",
    },
    {
      title: "Settings",
      url: "#",
      icon: "Settings",
      page: "settings",
    },
  ],
  mails: [
    {
      name: "John Doe",
      email: "johndoe@example.com",
      subject: "Yearly Physical",
      date: "Mar 24, 8am",
      appointmentDate: "Mar 24, 8am",
      intakeCompletedAt: "Mar 24, 2:30pm",
      location: "Denver, CO",
      status: "completed",
      teaser:
        "John is a 52-year-old male here for his annual physical. He reports no significant issues but wants to discuss recent fatigue and sleep patterns.",
      complaints: {
        list: [
          "Fatigue lasting several weeks",
          "Disrupted sleep patterns",
          "Mild headaches in the morning",
          "Occasional joint stiffness",
        ],
      },
      patientData: {
        height: "5'10\"",
        heightMetric: "178 cm",
        weight: "180 lbs",
        weightMetric: "81.6 kg",
        bloodPressure: "120/80 mmHg",
        heartRate: "72 bpm",
        temperature: "98.6°F",
        temperatureMetric: "37°C",
      },
      content: `Patient presents with:
- Fatigue lasting several weeks
- Disrupted sleep patterns
- No significant pain or discomfort
- No changes in appetite

Physical examination reveals:
- BP: 120/80
- Heart rate: 72 bpm
- Temperature: 98.6°F
- Weight: 180 lbs

Assessment:
- Possible sleep disorder
- Mild fatigue, likely related to poor sleep hygiene

Plan:
1. Sleep study referral
2. Follow-up in 2 weeks
3. Blood work to rule out underlying conditions
4. Sleep hygiene education provided`,
    },
    {
      name: "Emily Smith",
      email: "emilysmith@example.com",
      subject: "Follow-up Visit",
      date: "Mar 24, 8:30am",
      appointmentDate: "Mar 24, 8:30am",
      intakeCompletedAt: "Mar 24, 3:15pm",
      location: "Boulder, CO",
      status: "in-progress",
      teaser:
        "Emily, a 28-year-old female, complains of a sore throat, fever, and mild cough lasting three days. No known allergies or chronic conditions.",
      complaints: {
        list: [
          "Sore throat for 3 days",
          "Fever up to 101.2°F",
          "Mild persistent cough",
          "Fatigue and body aches",
        ],
      },
      patientData: {
        height: "5'6\"",
        heightMetric: "168 cm",
        weight: "135 lbs",
        weightMetric: "61.2 kg",
        bloodPressure: "118/75 mmHg",
        heartRate: "82 bpm",
        temperature: "101.2°F",
        temperatureMetric: "38.4°C",
      },
      content: `Chief Complaint: Sore throat, fever, cough x 3 days

History of Present Illness:
- Gradual onset of symptoms
- Fever max 101.2°F
- No exposure to known COVID-19 cases
- No travel history

Physical Exam:
- Throat: Erythematous with exudates
- Lymph nodes: Mildly enlarged
- Lungs: Clear to auscultation
- Heart: Regular rhythm

Assessment:
Acute pharyngitis, likely viral

Plan:
1. Symptomatic treatment
2. Rest and hydration
3. Return if symptoms worsen
4. COVID-19 test performed`,
    },
    {
      name: "Michael Johnson",
      email: "michaeljohnson@example.com",
      subject: "Initial Consultation",
      date: "Mar 24, 9am",
      appointmentDate: "Mar 24, 9am",
      location: "Fort Collins, CO",
      status: "not-started",
      teaser:
        "Michael brings his 6-year-old daughter for a routine school physical. She is up-to-date on vaccinations and has no current health concerns.",
      complaints: {
        list: [
          "No current complaints",
          "Routine school physical",
          "Up-to-date on vaccinations",
          "Normal growth and development",
        ],
      },
      patientData: {
        height: "4'0\"",
        heightMetric: "122 cm",
        weight: "45 lbs",
        weightMetric: "20.4 kg",
        bloodPressure: "100/60 mmHg",
        heartRate: "88 bpm",
        temperature: "98.4°F",
        temperatureMetric: "36.9°C",
      },
      content: `School Physical Examination

Growth and Development:
- Height: 48 inches (75th percentile)
- Weight: 45 lbs (50th percentile)
- Development appropriate for age

Systems Review:
- Eyes: 20/20 vision
- Ears: Normal
- Heart: Regular rhythm
- Lungs: Clear
- Neurological: Normal

Immunizations:
All up to date per schedule

Recommendations:
1. Continue regular exercise
2. Maintain healthy diet
3. Next visit in 1 year
4. Dental check-up reminder`,
    },
  ],
}
