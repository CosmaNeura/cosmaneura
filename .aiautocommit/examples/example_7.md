## Example 7

```
diff --git a/TODO b/TODO
index 692c0ba..d429064 100644
--- a/TODO
+++ b/TODO
@@ -75,16 +75,20 @@ user flow

 ## v3: cleanup

-- [ ] admin login_as to clerk hack workout
+- [ ] https://github.com/microsoft/azure-container-apps/issues/1307
+- [ ] use azure openai api instead of openai
+- [ ] admin login_as to clerk hack
+  - [ ] need role on user and way to switch session
 - [ ] added to our list to let them know in the upload that there are no schemas/notes uploaded
 - [ ] add email to user record so we can tell who is who
 - [x] add console script to import all models and all jobs
-- [ ] add contraint to prevent duplicate schemas of the same type
-  - [ ] I think there was something about fks not being properly named...
+- [ ] add contraint to prevent duplicate schemas of the same type https://github.com/fastapi/sqlmodel/issues/114
+  - [ ] I think there was something about fks not being properly named that we should fix as well
 - [ ] multiple schemas seem to be created for the same input note. Should try to make these idempotent.
-- [ ] azure deid logs are super verbose
+- [ ] azure deid logs are super verbose, can we turn these down and decrease HTTP logging?
 - [ ] cron job to delete transcripts from the database
   - [ ] soft delete, actually remove the transcripts
+  - [ ] need to implement this in activemodel
 - [ ] alerting
   - [ ] create two public endpoints on our web site, e.g. /status and /healthcheck
   - [ ] for /status: run a Clerk query to find the last login time, if it’s within the last day, return 200
```

docs: update TODO
