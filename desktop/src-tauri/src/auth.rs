use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;
use url::{form_urlencoded, Url};
use zeroize::Zeroizing;

use crate::{
    error::{CommandError, CommandResult},
    model::DesktopPhase,
    runtime::{emit_snapshot, DesktopApp, PendingAuthorization},
    secure_store::{installation_secret, save_session, StoredSession},
};

const REDIRECT_URI: &str = "cosmaneura-capture://oauth/callback";

enum DeepLink {
    Launch { ticket: String },
    Callback { code: String, state: String },
}

pub async fn handle_deep_link(app: AppHandle, url: Url) {
    let result = match parse_deep_link(&url) {
        Ok(DeepLink::Launch { ticket }) => begin_browser_authorization(&app, ticket).await,
        Ok(DeepLink::Callback { code, state }) => {
            finish_browser_authorization(&app, code, state).await
        }
        Err(error) => Err(error),
    };
    if result.is_err() {
        let state = app.state::<DesktopApp>();
        let snapshot = {
            let mut runtime = state.runtime.lock().await;
            runtime.pending_authorization = None;
            runtime.snapshot.phase = DesktopPhase::Error;
            runtime.snapshot.backend_reachable = false;
            runtime.snapshot.status_message =
                "Secure authorization failed. Return to Cosmas and try again.";
            runtime.snapshot.clone()
        };
        emit_snapshot(&app, &snapshot);
    }
}

async fn begin_browser_authorization(app: &AppHandle, ticket: String) -> CommandResult<()> {
    let verifier = random_secret(64);
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    let state_secret = random_secret(32);
    let installation = installation_secret(app)?;

    // Phase 2 requires the thumbprint today. The secret stays in the OS vault,
    // but the server does not yet challenge possession; activation remains
    // blocked until proof-of-possession is added or this field is removed.
    let installation_thumbprint = URL_SAFE_NO_PAD.encode(Sha256::digest(&*installation));
    let fragment = form_urlencoded::Serializer::new(String::new())
        .append_pair("launch_ticket", &ticket)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("redirect_uri", REDIRECT_URI)
        .append_pair("installation_thumbprint", &installation_thumbprint)
        .append_pair("state", state_secret.as_str())
        .finish();

    let desktop = app.state::<DesktopApp>();
    let browser_url = desktop.api.browser_handoff_url(&fragment)?;
    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.pending_authorization = Some(PendingAuthorization {
            verifier,
            state: state_secret,
            redirect_uri: REDIRECT_URI.to_owned(),
        });
        runtime.snapshot.phase = DesktopPhase::WaitingForBrowser;
        runtime.snapshot.status_message =
            "Finish the secure sign-in and consent handoff in your browser.";
        runtime.snapshot.clone()
    };
    if app
        .opener()
        .open_url(browser_url.as_str(), None::<&str>)
        .is_err()
    {
        return Err(CommandError::authorization_failed());
    }
    emit_snapshot(app, &snapshot);
    Ok(())
}

async fn finish_browser_authorization(
    app: &AppHandle,
    code: String,
    returned_state: String,
) -> CommandResult<()> {
    let desktop = app.state::<DesktopApp>();
    let pending = {
        let mut runtime = desktop.runtime.lock().await;
        let pending = runtime
            .pending_authorization
            .take()
            .ok_or_else(CommandError::authorization_failed)?;
        if pending
            .state
            .as_bytes()
            .ct_eq(returned_state.as_bytes())
            .unwrap_u8()
            != 1
        {
            return Err(CommandError::authorization_failed());
        }
        pending
    };

    let response = desktop
        .api
        .exchange_code(&code, pending.verifier.as_str(), &pending.redirect_uri)
        .await?;
    if response.access_token.len() < 43
        || response.access_token.len() > 128
        || !response
            .access_token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err(CommandError::authorization_failed());
    }
    let session = StoredSession::new(response.access_token, response.expires_in, response.grant)?;
    save_session(app, &session)?;

    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.set_authorized();
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &snapshot);
    Ok(())
}

fn parse_deep_link(url: &Url) -> CommandResult<DeepLink> {
    if url.scheme() != "cosmaneura-capture"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.fragment().is_some()
    {
        return Err(CommandError::authorization_failed());
    }
    match (url.host_str(), url.path()) {
        (Some("launch"), "" | "/") => {
            let ticket = exact_query_value(url, "launch_ticket")?;
            if !valid_opaque_secret(&ticket) || query_count(url) != 1 {
                return Err(CommandError::authorization_failed());
            }
            Ok(DeepLink::Launch { ticket })
        }
        (Some("oauth"), "/callback") => {
            let code = exact_query_value(url, "code")?;
            let state = exact_query_value(url, "state")?;
            if !valid_opaque_secret(&code) || !valid_opaque_secret(&state) || query_count(url) != 2
            {
                return Err(CommandError::authorization_failed());
            }
            Ok(DeepLink::Callback { code, state })
        }
        _ => Err(CommandError::authorization_failed()),
    }
}

fn exact_query_value(url: &Url, key: &str) -> CommandResult<String> {
    let values = url
        .query_pairs()
        .filter_map(|(name, value)| (name == key).then_some(value.into_owned()))
        .collect::<Vec<_>>();
    if values.len() != 1 {
        return Err(CommandError::authorization_failed());
    }
    Ok(values.into_iter().next().unwrap_or_default())
}

fn query_count(url: &Url) -> usize {
    url.query_pairs().count()
}

fn valid_opaque_secret(value: &str) -> bool {
    (43..=128).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn random_secret(byte_count: usize) -> Zeroizing<String> {
    let mut bytes = Zeroizing::new(vec![0_u8; byte_count]);
    OsRng.fill_bytes(bytes.as_mut_slice());
    Zeroizing::new(URL_SAFE_NO_PAD.encode(bytes.as_slice()))
}

#[cfg(test)]
mod tests {
    use super::{parse_deep_link, DeepLink};
    use url::Url;

    #[test]
    fn accepts_exact_launch_shape() {
        let url = Url::parse(&format!(
            "cosmaneura-capture://launch?launch_ticket={}",
            "a".repeat(43)
        ))
        .unwrap();
        assert!(matches!(parse_deep_link(&url), Ok(DeepLink::Launch { .. })));
    }

    #[test]
    fn rejects_duplicate_or_extra_callback_authority() {
        let url = Url::parse(&format!(
            "cosmaneura-capture://oauth/callback?code={}&state={}&tenant_id=attacker",
            "a".repeat(43),
            "b".repeat(43)
        ))
        .unwrap();
        assert!(parse_deep_link(&url).is_err());
    }

    #[test]
    fn rejects_credentials_and_fragments() {
        let url = Url::parse(&format!(
            "cosmaneura-capture://user@launch?launch_ticket={}#secret",
            "a".repeat(43)
        ))
        .unwrap();
        assert!(parse_deep_link(&url).is_err());
    }
}
