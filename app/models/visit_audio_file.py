# class VisitAudioFile(TimestampMixin, DeletedAtMixin):
#     """
#     Link to the blob for a specific visit audio file

#     1. User drops file into upload area
#     2. User hits upload which hits the backend API which generates a signed upload URL
#     3. User uploads file to signed upload URL, does not hit the backend API for upload
#     """

#     azure_signed_upload_id: str
#     file_name: str
#     upload_complete: datetime

#     appointment_id: uid
#     azure_blob_id: uid
