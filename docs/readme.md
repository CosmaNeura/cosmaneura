# Cosma External API Documentation

This documentation describes the External API endpoints available for integrating with the Cosma platform. The API allows external systems to create intake appointments and verify API connectivity.

## Authentication

All API endpoints require authentication. Authentication is handled via a Bearer token that should be included in the Authorization header of all requests.

```
Authorization: Bearer sk_live_<your_api_key>
```

API keys are prefixed with `sk_live_` and are associated with a doctor account. Contact Cosma support to obtain an API key.

## Endpoints

### `GET /external/v1/ping`

A simple endpoint to check API connectivity and authentication.

#### Response

```json
{
  "status": "ok"
}
```

### `POST /external/v1/intake`

Create a new intake appointment in the Cosma system.

#### Request Body

```json
{
  "id": "ext-12345",
  "date": "2023-07-25T14:30:00Z",
  "doctor_email": "doctor@example.com",
  "intake_json": {
    "symptoms": "Headache, dizziness",
    "duration": "3 days"
  },
  "patient": {
    "name": "John Doe",
    "dob": "1980-01-01"
  },
  "appointment_type": "follow-up"
}
```

Note that `intake_json` can be in whatever structure you'd like, as long as it's valid JSON.

| Field | Type | Description |
|-------|------|-------------|
| id | string | Required. ID of the appointment in the external system |
| date | datetime | Required. Date and time of the appointment (must be in the past) |
| doctor_email | email | Required. Email of the doctor the appointment is scheduled with |
| intake_json | object | Required. JSON data from intake form |
| patient | object | Required. Patient information |
| appointment_type | string | Required. Type of appointment (e.g., "follow up", "new patient") |

#### Patient Object

| Field | Type | Description |
|-------|------|-------------|
| name | string | Required. Patient's full name |
| dob | date | Required. Patient's date of birth (YYYY-MM-DD) |

#### Response

```json
{
  "id": "intake_1234567890abcdef",
  "url": "http://chat.cosmaneura.com/intake/1234567890abcdef"
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | ID of the intake in the Cosma system |
| url | string | URL to access the intake form |

#### Error Responses

- `401 Unauthorized`: Invalid API key
- `410 Gone`: API access has been disabled
- `422 Validation Error`: Request validation failed

## Processing

When an intake is created:

1. The system checks if the doctor exists in Cosma
2. The patient is located or created based on name and date of birth
3. An appointment record is created or updated
4. Intake data is stored and processed
5. A URL is returned that can be used to access the intake

## Implementation Notes

- Appointment dates must be in the past
- The doctor must have previously signed into Cosma
- The API validates that either doctor_email or doctor_id is provided
