# 2025-01-21 - Note-taking System Now Live!

## User Announcement

We are excited to announce that our note-taking system is now live in production! This release includes several key features that we're thrilled to share with you:

1. Example Notes Upload
   - Drag and drop past patient visit notes
   - System automatically extracts the schema
   - Future notes will be generated following your preferred style

2. Intake Example Upload
   - While the intake system is still in development, you can now upload intake examples
   - All uploads are processed in a HIPAA-compliant manner
   - Your examples will help us craft the final intake system

3. Visit Audio File Upload
   - Upload audio or video files from patient visits
   - Automatic transcription of audio content
   - Summarization based on your uploaded note templates
   - Specify patient name and appointment date
   - System automatically creates patient and appointment records

## Internal Notes

Application is ready for expanded doctor rollout!

### Testing plan

* Test note upload with existing beta doctors first
* Target 10+ uploads over next week. John, we'll look to you to notify Doctors and encourage uploads.
* If successful, expand to larger beta group
* Begin recruiting additional doctors via MCD

### Operational Systems

* Sentry is hooked up and is fully operational.
* There's a job monitoring system in place to ensure that all jobs are running as expected. It's in 1password.
* Clerk is still in development mode, but I'll be switching it over to production tomorrow.

### Daily Operations

* Error log monitoring needed as application scales. We'll keep an eye on it, but @John, it's important that you can
  loosely understand this system and be able to respond to errors.
* Important that John + team understand how Clerk operates.
* General comment is someone outside of David + Mike needs handle daily operational tasks, general user support issues,
  and monitor the system.

### Additional Testing Needs

* Full application testing sweep needed. Login, upload stuff, etc.
* UI review and feedback collection. @John, setup ruttl.com and collect feedback through the app feature on their tool.
  will be better than a mess up slack messages.
