use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_keyring_store::KeyringExt;
use zeroize::Zeroizing;

use crate::{
    error::{CommandError, CommandResult},
    model::BackendCaptureGrant,
};

const SESSION_ACCOUNT: &str = "desktop.oauth.session.v1";
const INSTALLATION_SECRET_ACCOUNT: &str = "desktop.installation.secret.v1";
const MAX_SESSION_TTL_SECONDS: u32 = 6 * 60 * 60;

#[derive(Deserialize, Serialize)]
pub struct StoredSession {
    pub access_token: Zeroizing<String>,
    pub expires_at_unix: u64,
    pub grant: BackendCaptureGrant,
}

impl StoredSession {
    pub fn new(
        access_token: Zeroizing<String>,
        expires_in: u32,
        grant: BackendCaptureGrant,
    ) -> CommandResult<Self> {
        if expires_in == 0 || expires_in > MAX_SESSION_TTL_SECONDS {
            return Err(CommandError::authorization_failed());
        }
        let expires_at_unix = unix_now()
            .checked_add(u64::from(expires_in))
            .ok_or_else(CommandError::authorization_failed)?;
        Ok(Self {
            access_token,
            expires_at_unix,
            grant,
        })
    }

    pub fn is_expired(&self) -> bool {
        self.expires_at_unix <= unix_now()
    }
}

pub fn save_session(app: &AppHandle, session: &StoredSession) -> CommandResult<()> {
    let serialized = Zeroizing::new(
        serde_json::to_string(session).map_err(|_| CommandError::authorization_failed())?,
    );
    app.keyring()
        .store
        .set_password(SESSION_ACCOUNT, serialized.as_str())
        .map_err(|_| CommandError::authorization_failed())
}

pub fn load_session(app: &AppHandle) -> CommandResult<Option<StoredSession>> {
    let Some(serialized) = app
        .keyring()
        .store
        .get_password(SESSION_ACCOUNT)
        .map_err(|_| CommandError::authorization_failed())?
    else {
        return Ok(None);
    };
    let serialized = Zeroizing::new(serialized);
    let session: StoredSession = serde_json::from_str(serialized.as_str())
        .map_err(|_| CommandError::authorization_failed())?;
    if session.is_expired() {
        delete_session(app)?;
        return Ok(None);
    }
    Ok(Some(session))
}

pub fn delete_session(app: &AppHandle) -> CommandResult<()> {
    app.keyring()
        .store
        .delete(SESSION_ACCOUNT)
        .map_err(|_| CommandError::authorization_failed())
}

pub fn installation_secret(app: &AppHandle) -> CommandResult<Zeroizing<Vec<u8>>> {
    if let Some(secret) = app
        .keyring()
        .store
        .get_bytes(INSTALLATION_SECRET_ACCOUNT)
        .map_err(|_| CommandError::authorization_failed())?
    {
        if secret.len() != 32 {
            return Err(CommandError::authorization_failed());
        }
        return Ok(Zeroizing::new(secret));
    }

    let mut secret = Zeroizing::new(vec![0_u8; 32]);
    OsRng.fill_bytes(secret.as_mut_slice());
    app.keyring()
        .store
        .set_bytes(INSTALLATION_SECRET_ACCOUNT, secret.as_slice())
        .map_err(|_| CommandError::authorization_failed())?;
    Ok(secret)
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_secs())
}
