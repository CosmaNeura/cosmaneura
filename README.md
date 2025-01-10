# cosmaneura

This is the primary repo for cosmaneura.com.


[Generated from python-starter-template.](http://github.com/iloveitaly/python-starter-template)

## Suggested Consent Script

> I’m using a new tool to take better notes during our appointment so I can focus fully on you. It requires recording our conversation, but this is entirely optional. The recording is securely stored, follows strict HIPAA guidelines, and is deleted within a few hours after use. Are you okay with me recording?

## LiveSwitch Process

1. In LiveSwitch, start or join a session.
2. Click the Record button
3. Stop the recording, then leave the call.
4. In LiveSwitch’s main dashboard, go to the Recordings tab.
5. Locate your new recording (it will take a while to process, at least 10m for larger videos, could take hours depending on their system).
6. Click “Actions” → “Download” to save the file to your computer.
7. Open the Cosmaneaura app’s Upload page.
8. Select the downloaded file and upload it.
9. Wait for the “Upload successful” message, then check the notes after processing completes.

## PHI Management

Here are the rules we use to manage PHI. You must follow these rules:

- Never store PHI on your machine.
- Prefer maintaining a manually curated, high-quality de-identified data set for all development and testing to reduce security overhead and ensure compliance.
- Utilize Azure’s [De-identification API](https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/overview) to anonymize PHI, simplifying security requirements by removing PHI classification.
- Restrict data access to authorized personnel, encrypt data both in transit and at rest using strong algorithms, and continuously monitor data usage with regular security audits.
