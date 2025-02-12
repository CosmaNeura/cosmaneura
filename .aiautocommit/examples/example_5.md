## Example 5

```
diff --git a/tests/job/extract_test.py b/tests/job/extract_test.py
index 3777392..221f634 100644
--- a/tests/job/extract_test.py
+++ b/tests/job/extract_test.py
@@ -1,7 +1,6 @@
 import re

-from app import log
-from app.jobs.extract import extract
+from app.jobs.extract import perform

 from app.models.doctor_note_example import DoctorNoteExample
 from app.models.doctor_note_file import DoctorNoteFile
@@ -23,7 +22,7 @@ def test_telehealth_SOAP_note_extraction():
     )
     assert schema is None

-    extract(file.id)
+    perform(file.id)

     schema = DoctorNoteSchema.get(
         doctor_id=file.doctor_id,
@@ -140,7 +139,7 @@ def test_telehealth_H_and_P_note_extraction():
     )
     assert schema is None

-    extract(file.id)
+    perform(file.id)

     schema = DoctorNoteSchema.get(
         doctor_id=file.doctor_id,
diff --git a/tests/routes/notes_test.py b/tests/routes/notes_test.py
index 83bf2ef..169f955 100644
--- a/tests/routes/notes_test.py
+++ b/tests/routes/notes_test.py
@@ -15,10 +15,11 @@ def test_notes_list(authenticated_client: TestClient):


 def test_note_detail(authenticated_client: TestClient):
-    # TODO will break as the seed process becomes more complex
-    assert Appointment.count() == 1
+    # more than one, because of the db seed
+    assert Appointment.count() > 1

     appointment = Appointment.first()
+    assert appointment

     response = authenticated_client.get(
         api_app.url_path_for("note_detail", appointment_id=appointment.id)
```

test: fix perform reference in extraction job

Also updated assertions to reflect expected behavior after database seeding adjustments.
