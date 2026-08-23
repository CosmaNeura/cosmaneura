use std::sync::Arc;

use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex;
use zeroize::Zeroizing;

use crate::{
    api::DesktopApi,
    model::{AppointmentSelection, ConsentStatus, DesktopPhase, DesktopSnapshot},
    provider::NativeCaptureAdapter,
};

pub const DESKTOP_STATE_EVENT: &str = "desktop-state-changed";

pub struct PendingAuthorization {
    pub verifier: Zeroizing<String>,
    pub state: Zeroizing<String>,
    pub redirect_uri: String,
}

pub struct ActiveCapture {
    pub capability_id: String,
    pub generation: String,
}

pub struct RuntimeState {
    pub snapshot: DesktopSnapshot,
    pub pending_authorization: Option<PendingAuthorization>,
    pub active_capture: Option<ActiveCapture>,
}

impl RuntimeState {
    pub fn new(provider_enabled: bool) -> Self {
        let snapshot = DesktopSnapshot {
            provider_enabled,
            ..DesktopSnapshot::default()
        };
        Self {
            snapshot,
            pending_authorization: None,
            active_capture: None,
        }
    }

    pub fn set_authorized(&mut self) {
        self.snapshot.phase = DesktopPhase::Authorized;
        self.snapshot.appointment_selection = AppointmentSelection::Confirmed;
        self.snapshot.consent_status = ConsentStatus::Granted;
        self.snapshot.backend_reachable = true;
        self.snapshot.status_message =
            "The platform verified the selected appointment and current consent.";
    }
}

pub struct DesktopApp {
    pub api: DesktopApi,
    pub provider: Arc<dyn NativeCaptureAdapter>,
    pub runtime: Mutex<RuntimeState>,
}

impl DesktopApp {
    pub fn new(api: DesktopApi, provider: Arc<dyn NativeCaptureAdapter>) -> Self {
        let provider_enabled = provider.is_enabled();
        Self {
            api,
            provider,
            runtime: Mutex::new(RuntimeState::new(provider_enabled)),
        }
    }
}

pub fn emit_snapshot(app: &AppHandle, snapshot: &DesktopSnapshot) {
    let _ = app.emit(DESKTOP_STATE_EVENT, snapshot.clone());
}
