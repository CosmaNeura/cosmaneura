use std::time::Duration;

use tauri::{AppHandle, Manager, State};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::{
    error::{CommandError, CommandResult},
    model::{DesktopPhase, DesktopSnapshot},
    runtime::{emit_snapshot, ActiveCapture, DesktopApp},
    secure_store::{delete_session, load_session},
};

const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(30);

#[tauri::command]
pub async fn get_desktop_snapshot(state: State<'_, DesktopApp>) -> CommandResult<DesktopSnapshot> {
    Ok(state.runtime.lock().await.snapshot.clone())
}

#[tauri::command]
pub async fn open_provider_platform(
    app: AppHandle,
    state: State<'_, DesktopApp>,
) -> CommandResult<DesktopSnapshot> {
    use tauri_plugin_opener::OpenerExt;

    let url = state.api.provider_portal_url()?;
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(|_| CommandError::unavailable())?;
    let snapshot = {
        let mut runtime = state.runtime.lock().await;
        if runtime.active_capture.is_some() {
            return Err(CommandError::invalid_state());
        }
        runtime.snapshot.phase = DesktopPhase::WaitingForBrowser;
        runtime.snapshot.status_message =
            "Choose the appointment and record all-party consent in Cosmas.";
        runtime.snapshot.clone()
    };
    emit_snapshot(&app, &snapshot);
    Ok(snapshot)
}

#[tauri::command]
pub async fn start_capture(
    app: AppHandle,
    state: State<'_, DesktopApp>,
) -> CommandResult<DesktopSnapshot> {
    start_capture_inner(&app, &state).await
}

async fn start_capture_inner(
    app: &AppHandle,
    desktop: &DesktopApp,
) -> CommandResult<DesktopSnapshot> {
    let starting_snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        if runtime.snapshot.phase != DesktopPhase::Authorized
            || runtime.active_capture.is_some()
            || !desktop.provider.is_enabled()
        {
            return Err(CommandError::invalid_state());
        }
        runtime.snapshot.phase = DesktopPhase::Starting;
        runtime.snapshot.status_message = "Preparing local audio capture…";
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &starting_snapshot);

    let session = load_session(app)?.ok_or_else(CommandError::authorization_failed)?;
    let mut authorization = match desktop.api.authorize_start(&session).await {
        Ok(value) => value,
        Err(error) => {
            restore_authorized(app, desktop, false).await;
            return Err(error);
        }
    };
    let capability_id = authorization.capture_capability.capability_id.clone();
    let upload_capability =
        Zeroizing::new(std::mem::take(&mut authorization.capture_capability.secret));
    if let Err(error) = desktop.provider.start(upload_capability).await {
        restore_authorized(app, desktop, true).await;
        return Err(error);
    }

    // Record the first heartbeat before representing the session as active. If
    // the backend does not accept it, local capture is stopped immediately.
    if let Err(error) = desktop.api.heartbeat(&session, &capability_id).await {
        if desktop.provider.stop().await.is_err() {
            mark_local_stop_failure(app, desktop).await;
            return Err(CommandError::local_stop_failed());
        }
        restore_authorized(app, desktop, false).await;
        return Err(error);
    }

    let generation = Uuid::new_v4().to_string();
    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.active_capture = Some(ActiveCapture {
            capability_id,
            generation: generation.clone(),
        });
        runtime.snapshot.phase = DesktopPhase::Recording;
        runtime.snapshot.local_capture_active = true;
        runtime.snapshot.backend_reachable = true;
        runtime.snapshot.status_message = "Microphone and system audio are being captured locally.";
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &snapshot);
    spawn_heartbeat_loop(app.clone(), generation);
    Ok(snapshot)
}

#[tauri::command]
pub async fn stop_capture(
    app: AppHandle,
    state: State<'_, DesktopApp>,
) -> CommandResult<DesktopSnapshot> {
    stop_capture_inner(&app, &state).await
}

pub async fn stop_capture_inner(
    app: &AppHandle,
    desktop: &DesktopApp,
) -> CommandResult<DesktopSnapshot> {
    let capability_id = {
        let mut runtime = desktop.runtime.lock().await;
        let capability_id = runtime
            .active_capture
            .as_ref()
            .map(|active| active.capability_id.clone())
            .ok_or_else(CommandError::invalid_state)?;
        runtime.snapshot.phase = DesktopPhase::Stopping;
        runtime.snapshot.status_message = "Stopping local audio first…";
        let snapshot = runtime.snapshot.clone();
        emit_snapshot(app, &snapshot);
        capability_id
    };

    // This is the fail-safe boundary. Never wait for platform reachability
    // before asking the native provider to close its local audio devices.
    if desktop.provider.stop().await.is_err() {
        mark_local_stop_failure(app, desktop).await;
        return Err(CommandError::local_stop_failed());
    }

    let locally_stopped = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.active_capture = None;
        runtime.snapshot.phase = DesktopPhase::Stopped;
        runtime.snapshot.local_capture_active = false;
        runtime.snapshot.status_message =
            "Local audio stopped. Recording the stop receipt with Cosmas…";
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &locally_stopped);

    let backend_result = match load_session(app) {
        Ok(Some(session)) => desktop.api.stop(&session, &capability_id).await,
        _ => Err(CommandError::authorization_failed()),
    };

    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        if backend_result.is_ok() {
            let _ = delete_session(app);
            runtime.snapshot.backend_reachable = true;
            runtime.snapshot.status_message =
                "Capture stopped locally and the platform accepted the stop request.";
        } else {
            // Do not turn a coordination failure into a false local-recording
            // state. The local devices are already stopped.
            runtime.snapshot.backend_reachable = false;
            runtime.snapshot.status_message =
                "Local audio is stopped. The platform stop receipt still needs reconciliation.";
        }
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &snapshot);
    Ok(snapshot)
}

fn spawn_heartbeat_loop(app: AppHandle, generation: String) {
    tauri::async_runtime::spawn(async move {
        let desktop = app.state::<DesktopApp>();
        loop {
            tokio::time::sleep(HEARTBEAT_INTERVAL).await;
            let capability_id = {
                let runtime = desktop.runtime.lock().await;
                match runtime.active_capture.as_ref() {
                    Some(active) if active.generation == generation => active.capability_id.clone(),
                    _ => return,
                }
            };
            let heartbeat_result = match load_session(&app) {
                Ok(Some(session)) => desktop.api.heartbeat(&session, &capability_id).await,
                _ => Err(CommandError::authorization_failed()),
            };
            let snapshot = {
                let mut runtime = desktop.runtime.lock().await;
                match runtime.active_capture.as_ref() {
                    Some(active) if active.generation == generation => {}
                    _ => return,
                }
                if heartbeat_result.is_ok() {
                    runtime.snapshot.phase = DesktopPhase::Recording;
                    runtime.snapshot.backend_reachable = true;
                    runtime.snapshot.status_message =
                        "Microphone and system audio are being captured locally.";
                } else {
                    runtime.snapshot.phase = DesktopPhase::Degraded;
                    runtime.snapshot.backend_reachable = false;
                    runtime.snapshot.status_message =
                        "Platform connection is interrupted. Local stop remains available.";
                }
                runtime.snapshot.clone()
            };
            emit_snapshot(&app, &snapshot);
        }
    });
}

async fn restore_authorized(app: &AppHandle, desktop: &DesktopApp, backend_reachable: bool) {
    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.set_authorized();
        runtime.snapshot.backend_reachable = backend_reachable;
        runtime.snapshot.status_message =
            "Capture did not start. The authorized appointment remains ready.";
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &snapshot);
}

async fn mark_local_stop_failure(app: &AppHandle, desktop: &DesktopApp) {
    let snapshot = {
        let mut runtime = desktop.runtime.lock().await;
        runtime.snapshot.phase = DesktopPhase::Error;
        runtime.snapshot.local_capture_active = true;
        runtime.snapshot.status_message =
            "Local stop did not complete. Keep this application open and try Stop again.";
        runtime.snapshot.clone()
    };
    emit_snapshot(app, &snapshot);
}
