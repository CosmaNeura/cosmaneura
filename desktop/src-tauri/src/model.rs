use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DesktopPhase {
    #[default]
    Idle,
    WaitingForBrowser,
    Authorized,
    Starting,
    Recording,
    Degraded,
    Stopping,
    Stopped,
    Error,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AppointmentSelection {
    #[default]
    None,
    Confirmed,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConsentStatus {
    #[default]
    Unknown,
    Granted,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSnapshot {
    pub phase: DesktopPhase,
    pub appointment_selection: AppointmentSelection,
    pub consent_status: ConsentStatus,
    pub local_capture_active: bool,
    pub backend_reachable: bool,
    pub provider_enabled: bool,
    pub status_message: &'static str,
}

impl Default for DesktopSnapshot {
    fn default() -> Self {
        Self {
            phase: DesktopPhase::Idle,
            appointment_selection: AppointmentSelection::None,
            consent_status: ConsentStatus::Unknown,
            local_capture_active: false,
            backend_reachable: true,
            provider_enabled: false,
            status_message: "Choose an appointment in the Cosmas provider platform.",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BackendCaptureGrant {
    pub capture_session_id: String,
    pub actor_id: String,
    pub consent_record_id: String,
    pub consent_status: Granted,
    pub session_status: Authorized,
    pub source: DesktopCompanion,
    pub source_lease_id: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub enum Granted {
    #[serde(rename = "granted")]
    Granted,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub enum Authorized {
    #[serde(rename = "authorized")]
    Authorized,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub enum DesktopCompanion {
    #[serde(rename = "desktop_companion")]
    DesktopCompanion,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DesktopTokenResponse {
    pub access_token: Zeroizing<String>,
    #[serde(default = "bearer_default")]
    pub token_type: Bearer,
    pub expires_in: u32,
    pub grant: BackendCaptureGrant,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum Bearer {
    #[serde(rename = "Bearer")]
    Bearer,
}

fn bearer_default() -> Bearer {
    Bearer::Bearer
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CaptureCapability {
    pub capability_id: String,
    pub secret: String,
    pub expires_at: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StartAuthorizationResponse {
    pub schema_version: u8,
    pub decision: StartDecision,
    pub capture_session_id: String,
    pub actor_id: String,
    pub consent_record_id: String,
    pub consent_status: Granted,
    pub session_status: Authorized,
    pub source: DesktopCompanion,
    pub source_lease_id: String,
    pub issued_at: String,
    pub expires_at: String,
    pub capture_capability: CaptureCapability,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum StartDecision {
    #[serde(rename = "authorized")]
    Authorized,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LifecycleResponse {
    pub schema_version: u8,
    pub decision: LifecycleDecision,
    pub session_status: LifecycleSessionStatus,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum LifecycleDecision {
    #[serde(rename = "accepted")]
    Accepted,
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub enum LifecycleSessionStatus {
    #[serde(rename = "recording")]
    Recording,
    #[serde(rename = "stopping")]
    Stopping,
}

#[cfg(test)]
mod tests {
    use super::{DesktopTokenResponse, StartAuthorizationResponse};

    fn grant_json() -> serde_json::Value {
        serde_json::json!({
            "actorId": "doctor_01HZZZ",
            "captureSessionId": "capturesession_01HZZZ",
            "consentRecordId": "7e10d680-c8b1-47ff-a794-22f1945fe24f",
            "consentStatus": "granted",
            "sessionStatus": "authorized",
            "source": "desktop_companion",
            "sourceLeaseId": "capturesourcelease_01HZZZ"
        })
    }

    #[test]
    fn token_shape_matches_contract_and_accepts_default_token_type() {
        let payload = serde_json::json!({
            "access_token": "a".repeat(43),
            "expires_in": 3600,
            "grant": grant_json()
        });
        let parsed = serde_json::from_value::<DesktopTokenResponse>(payload);
        assert!(parsed.is_ok());
    }

    #[test]
    fn start_shape_matches_contract_and_rejects_extra_authority() {
        let mut payload = serde_json::json!({
            "schemaVersion": 1,
            "decision": "authorized",
            "actorId": "doctor_01HZZZ",
            "captureSessionId": "capturesession_01HZZZ",
            "consentRecordId": "7e10d680-c8b1-47ff-a794-22f1945fe24f",
            "consentStatus": "granted",
            "sessionStatus": "authorized",
            "source": "desktop_companion",
            "sourceLeaseId": "capturesourcelease_01HZZZ",
            "issuedAt": "2026-08-23T12:00:00Z",
            "expiresAt": "2026-08-23T12:05:00Z",
            "captureCapability": {
                "capabilityId": "bd75c563-dd58-471d-a3eb-63ec90b6b7b2",
                "secret": "synthetic_sdk_capability",
                "expiresAt": "2026-08-23T12:05:00Z"
            }
        });
        assert!(serde_json::from_value::<StartAuthorizationResponse>(payload.clone()).is_ok());
        payload["tenantId"] = serde_json::json!("attacker-selected-tenant");
        assert!(serde_json::from_value::<StartAuthorizationResponse>(payload).is_err());
    }
}
