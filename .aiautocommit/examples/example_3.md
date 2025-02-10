```
8083521 (17 seconds ago) feat: enhance SAS token generation and add new upload endpoints <Michael Bianco>
- Introduced `UploadType` enum to support multiple upload scenarios.
- Updated SAS token generation to handle different upload types.
- Added new API endpoints for generating signed URLs and handling uploads for example notes and intakes.
- Updated existing visit audio file upload process to align with new structure.
- Included doctor information in upload processes to store metadata and queue processing jobs.
- Modified settings route to fetch and return notes schema for the current doctor.

Generated-by: aiautocommit


diff --git a/app/commands/uploads/sas.py b/app/commands/uploads/sas.py
index cdfde5c..d9d55ab 100644
--- a/app/commands/uploads/sas.py
+++ b/app/commands/uploads/sas.py
@@ -1,4 +1,5 @@
-from datetime import datetime, timedelta
+from datetime import datetime, timedelta, timezone
+from enum import Enum
 
 from azure.storage.blob import (
     AccountSasPermissions,
@@ -12,10 +13,17 @@ from app.configuration.azure import (
     AZURE_UPLOADS_ACCOUNT_NAME,
     AZURE_UPLOADS_ACCOUNT_URL,
     AZURE_UPLOADS_CONTAINER_NAME,
+    AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
 )
 
 
-def perform() -> str:
+class UploadType(Enum):
+    VISIT = AZURE_UPLOADS_CONTAINER_NAME
+    EXAMPLE_NOTE = AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME
+    EXAMPLE_INTAKE = "to_implement"
+
+
+def perform(upload_type: UploadType) -> str:
     \"\"\"
     Generate a SAS token for uploading files, like audio, to a azure storage blob.
 
@@ -28,11 +36,12 @@ def perform() -> str:
         account_key=AZURE_UPLOADS_ACCOUNT_KEY,
         resource_types=ResourceTypes(object=True),
         permission=AccountSasPermissions(write=True),
-        expiry=datetime.utcnow() + timedelta(hours=1),
+        # 1hr is arbitrary
+        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
     )
 
     container_client = ContainerClient(
-        container_name=AZURE_UPLOADS_CONTAINER_NAME,
+        container_name=upload_type.value,
         account_url=AZURE_UPLOADS_ACCOUNT_URL,
         credential=sas_token,
     )
diff --git a/app/routes/internal.py b/app/routes/internal.py
index 60104db..2dc05b4 100644
--- a/app/routes/internal.py
+++ b/app/routes/internal.py
@@ -1,10 +1,13 @@
 from typing import Annotated
 
 from fastapi import APIRouter, Depends, Path, Request
+from openai.types import Upload
 from pydantic import BaseModel, computed_field
 
 import app.commands.uploads.sas
 import app.commands.uploads.upload_visit_audio_file
+import app.jobs.extract
+from app.commands.uploads.sas import UploadType
 
 from activemodel.session_manager import aglobal_session
 from activemodel.types import TypeIDType
@@ -12,6 +15,8 @@ from app.models.ai_visit_note import AIVisitNote
 from app.models.ai_visit_transcript import AIVisitTranscript
 from app.models.appointment import Appointment
 from app.models.doctor import Doctor
+from app.models.doctor_note_file import DoctorNoteFile
+from app.models.doctor_note_schema import DoctorNoteSchema
 from app.models.patient import Patient
 
 from ..configuration.clerk import CLERK_PRIVATE_KEY
@@ -113,22 +118,66 @@ def note_detail(
     return note_detail
 
 
+# unique endpoints are used to generate the signed URLs, this is mostly an arbitrary choice. It probably adds an extra
+# added level of security and ability to evolve the upload flow in the future without modifying the frontend.
 @internal_api_app.post("/visit-audio-file/get-signed-url", response_model=str)
-def generate_signed_upload_url():
-    return app.commands.uploads.sas.perform()
+def generate_signed_visit_recording_upload_url():
+    return app.commands.uploads.sas.perform(UploadType.VISIT)
 
 
 @internal_api_app.post("/visit-audio-file/upload-complete")
 def upload_visit_audio_file(request: Request, file_name: str) -> None:
+    doctor: Doctor = request.state.doctor
+
     return app.commands.uploads.upload_visit_audio_file.perform(
-        user=request.state.user, blob_name=file_name
+        doctor=doctor,
+        blob_name=file_name,
+        # TODO add to upload process
+        patient_name="hi",
+        appointment_date="01-01-20101",
+        date_of_birth="01-01-20101",
     )
 
 
+@internal_api_app.post("/notes/get-signed-url", response_model=str)
+def generate_signed_example_note_upload_url():
+    return app.commands.uploads.sas.perform(UploadType.EXAMPLE_NOTE)
+
+
+@internal_api_app.post("/notes/upload-example")
+def upload_example_note(request: Request, file_name: str, note_type: str) -> None:
+    # TODO maybe accept ID as well?
+    doctor: Doctor = request.state.doctor
+
+    doctor_note_file = DoctorNoteFile(
+        doctor_id=doctor.id, azure_file_name=file_name, note_type=note_type
+    ).save()
+
+    app.jobs.extract.queue(doctor_note_file.id)
+
+
+@internal_api_app.post("/intake/get-signed-url", response_model=str)
+def generate_signed_example_intake_upload_url():
+    return app.commands.uploads.sas.perform(UploadType.EXAMPLE_INTAKE)
+
+
+@internal_api_app.post("/intake/upload-example")
+def upload_example_intake(request: Request, file_name: str) -> None:
+    doctor: Doctor = request.state.doctor
+    raise NotImplementedError()
+
+
 class SettingsData(BaseModel, extra="forbid"):
-    settings: dict
+    notes_schema: list[DoctorNoteSchema]
+    # TODO add intake schema
 
 
 @internal_api_app.get("/settings")
-def settings_data() -> SettingsData:
-    return SettingsData(settings={})
+def settings_data(request: Request) -> SettingsData:
+    doctor: Doctor = request.state.doctor
+
+    notes_schema = (
+        DoctorNoteSchema.select().where(DoctorNoteSchema.doctor_id == doctor.id).all()
+    )
+
+    return SettingsData(notes_schema=list(notes_schema))
diff --git a/web/app/components/VisitNoteViewer.tsx b/web/app/components/VisitNoteViewer.tsx
index a31db12..d487410 100644
--- a/web/app/components/VisitNoteViewer.tsx
+++ b/web/app/components/VisitNoteViewer.tsx
@@ -12,8 +12,6 @@ import {
 import { CopyButton } from "~/components/shared/CopyButton"
 import { AIVisitNote } from "~/configuration/client"
 
-import ReactDOMServer from "react-dom/server"
-
 export default function VisitNoteViewer({
   note,
   defaultOpen = false,
```

This diff is medium sized and should have no extended commit message.

Example commit message:

feat: sas for multiple storage containers, upload endpoints and expanded settings endpoint