use std::time::Duration;

use reqwest::{header, Client, StatusCode};
use serde::Serialize;
use url::Url;
use uuid::Uuid;

use crate::{
    error::{CommandError, CommandResult},
    model::{
        BackendCaptureGrant, DesktopTokenResponse, LifecycleResponse, LifecycleSessionStatus,
        StartAuthorizationResponse,
    },
    secure_store::StoredSession,
};

const IDEMPOTENCY_PREFIX: &str = "desktop-companion";

#[derive(Clone)]
pub struct DesktopApi {
    client: Client,
    origin: Url,
}

#[derive(Serialize)]
struct TokenExchangeRequest<'a> {
    grant_type: &'static str,
    code: &'a str,
    code_verifier: &'a str,
    redirect_uri: &'a str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LifecycleRequest<'a> {
    capability_id: &'a str,
}

impl DesktopApi {
    pub fn new(origin: Url) -> CommandResult<Self> {
        let client = Client::builder()
            .https_only(origin.scheme() == "https")
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(20))
            .user_agent("Cosmas-Desktop-Companion/0.1")
            .build()
            .map_err(|_| CommandError::unavailable())?;
        Ok(Self { client, origin })
    }

    pub fn provider_portal_url(&self) -> CommandResult<Url> {
        self.origin
            .join("/")
            .map_err(|_| CommandError::unavailable())
    }

    pub fn browser_handoff_url(&self, fragment: &str) -> CommandResult<Url> {
        let mut url = self
            .origin
            .join("/desktop-capture/connect")
            .map_err(|_| CommandError::authorization_failed())?;
        url.set_fragment(Some(fragment));
        Ok(url)
    }

    pub async fn exchange_code(
        &self,
        code: &str,
        verifier: &str,
        redirect_uri: &str,
    ) -> CommandResult<DesktopTokenResponse> {
        let url = self
            .origin
            .join("/desktop/v1/oauth/token")
            .map_err(|_| CommandError::authorization_failed())?;
        let response = self
            .client
            .post(url)
            .header(header::CACHE_CONTROL, "no-store")
            .json(&TokenExchangeRequest {
                grant_type: "authorization_code",
                code,
                code_verifier: verifier,
                redirect_uri,
            })
            .send()
            .await
            .map_err(|_| CommandError::authorization_failed())?;
        if response.status() != StatusCode::OK {
            return Err(CommandError::authorization_failed());
        }
        let token = response
            .json::<DesktopTokenResponse>()
            .await
            .map_err(|_| CommandError::authorization_failed())?;
        if token.token_type != crate::model::Bearer::Bearer {
            return Err(CommandError::authorization_failed());
        }
        Ok(token)
    }

    pub async fn authorize_start(
        &self,
        session: &StoredSession,
    ) -> CommandResult<StartAuthorizationResponse> {
        let url = self.session_url(&session.grant, "segments/start-authorizations")?;
        let response = self
            .authorized_post(url, session, idempotency_key("start"))
            .send()
            .await
            .map_err(|_| CommandError::unavailable())?;
        if response.status() != StatusCode::OK {
            return Err(CommandError::unavailable());
        }
        let authorization = response
            .json::<StartAuthorizationResponse>()
            .await
            .map_err(|_| CommandError::unavailable())?;
        validate_start_binding(&session.grant, &authorization)?;
        Ok(authorization)
    }

    pub async fn heartbeat(
        &self,
        session: &StoredSession,
        capability_id: &str,
    ) -> CommandResult<()> {
        let url = self.session_url(&session.grant, "heartbeats")?;
        let response = self
            .authorized_post(url, session, idempotency_key("heartbeat"))
            .json(&LifecycleRequest { capability_id })
            .send()
            .await
            .map_err(|_| CommandError::unavailable())?;
        let lifecycle = parse_lifecycle_response(response).await?;
        if !matches!(lifecycle.session_status, LifecycleSessionStatus::Recording) {
            return Err(CommandError::unavailable());
        }
        Ok(())
    }

    pub async fn stop(&self, session: &StoredSession, capability_id: &str) -> CommandResult<()> {
        let url = self.session_url(&session.grant, "stop-requests")?;
        let response = self
            .authorized_post(url, session, idempotency_key("stop"))
            .json(&LifecycleRequest { capability_id })
            .send()
            .await
            .map_err(|_| CommandError::unavailable())?;
        let lifecycle = parse_lifecycle_response(response).await?;
        if !matches!(lifecycle.session_status, LifecycleSessionStatus::Stopping) {
            return Err(CommandError::unavailable());
        }
        Ok(())
    }

    fn session_url(&self, grant: &BackendCaptureGrant, operation: &str) -> CommandResult<Url> {
        if !is_safe_identifier(&grant.capture_session_id) {
            return Err(CommandError::authorization_failed());
        }
        self.origin
            .join(&format!(
                "/desktop/v1/capture-sessions/{}/{}",
                grant.capture_session_id, operation
            ))
            .map_err(|_| CommandError::unavailable())
    }

    fn authorized_post(
        &self,
        url: Url,
        session: &StoredSession,
        idempotency_key: String,
    ) -> reqwest::RequestBuilder {
        self.client
            .post(url)
            .bearer_auth(session.access_token.as_str())
            .header("Idempotency-Key", idempotency_key)
            .header(header::CACHE_CONTROL, "no-store")
    }
}

fn idempotency_key(operation: &str) -> String {
    format!("{IDEMPOTENCY_PREFIX}-{operation}-{}", Uuid::new_v4())
}

fn is_safe_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn validate_start_binding(
    grant: &BackendCaptureGrant,
    authorization: &StartAuthorizationResponse,
) -> CommandResult<()> {
    if authorization.schema_version != 1
        || authorization.decision != crate::model::StartDecision::Authorized
        || authorization.capture_session_id != grant.capture_session_id
        || authorization.actor_id != grant.actor_id
        || authorization.consent_record_id != grant.consent_record_id
        || !matches!(authorization.consent_status, crate::model::Granted::Granted)
        || !matches!(
            authorization.session_status,
            crate::model::Authorized::Authorized
        )
        || !matches!(
            authorization.source,
            crate::model::DesktopCompanion::DesktopCompanion
        )
        || authorization.source_lease_id != grant.source_lease_id
        || authorization.issued_at.is_empty()
        || authorization.issued_at.len() > 64
        || authorization.expires_at.is_empty()
        || authorization.expires_at.len() > 64
        || authorization.capture_capability.capability_id.is_empty()
        || authorization.capture_capability.capability_id.len() > 128
        || authorization.capture_capability.secret.len() < 16
        || authorization.capture_capability.secret.len() > 4096
        || authorization.capture_capability.expires_at.is_empty()
        || authorization.capture_capability.expires_at.len() > 64
    {
        return Err(CommandError::authorization_failed());
    }
    Ok(())
}

async fn parse_lifecycle_response(response: reqwest::Response) -> CommandResult<LifecycleResponse> {
    if response.status() != StatusCode::OK {
        return Err(CommandError::unavailable());
    }
    let lifecycle = response
        .json::<LifecycleResponse>()
        .await
        .map_err(|_| CommandError::unavailable())?;
    if lifecycle.schema_version != 1
        || lifecycle.decision != crate::model::LifecycleDecision::Accepted
    {
        return Err(CommandError::unavailable());
    }
    Ok(lifecycle)
}

#[cfg(test)]
mod tests {
    use super::is_safe_identifier;

    #[test]
    fn capture_session_path_segment_is_restricted() {
        assert!(is_safe_identifier("capturesession_01HZZZ"));
        assert!(!is_safe_identifier("../internal/v1"));
        assert!(!is_safe_identifier("session/value"));
        assert!(!is_safe_identifier(""));
    }
}
