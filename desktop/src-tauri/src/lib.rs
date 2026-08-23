mod api;
mod auth;
mod commands;
mod error;
mod model;
mod provider;
mod runtime;
mod secure_store;

use std::sync::Arc;

use tauri::{Manager, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use url::Url;

use api::DesktopApi;
use commands::{
    get_desktop_snapshot, open_provider_platform, start_capture, stop_capture, stop_capture_inner,
};
use provider::DisabledCaptureAdapter;
use runtime::DesktopApp;

pub fn run() {
    let origin = platform_origin().expect("invalid Cosmas platform origin");
    let api = DesktopApi::new(origin).expect("desktop API client could not be created");
    let desktop = DesktopApp::new(api, Arc::new(DisabledCaptureAdapter));

    let builder =
        tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }));

    builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_keyring_store::Builder::new()
                .service("com.cosmaneura.capture.credentials")
                .build(),
        )
        .manage(desktop)
        .invoke_handler(tauri::generate_handler![
            get_desktop_snapshot,
            open_provider_platform,
            start_capture,
            stop_capture
        ])
        .setup(|app| {
            if let Some(urls) = app.deep_link().get_current()? {
                for url in urls {
                    spawn_deep_link(app.handle().clone(), url);
                }
            }
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    spawn_deep_link(handle.clone(), url.clone());
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let desktop = window.state::<DesktopApp>();
                let active = desktop
                    .runtime
                    .try_lock()
                    .map(|runtime| runtime.active_capture.is_some())
                    .unwrap_or(true);
                if active {
                    api.prevent_close();
                    let handle = window.app_handle().clone();
                    let window = window.clone();
                    tauri::async_runtime::spawn(async move {
                        let desktop = handle.state::<DesktopApp>();
                        if stop_capture_inner(&handle, &desktop).await.is_ok() {
                            let _ = window.destroy();
                        }
                    });
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Cosmas desktop companion failed to run");
}

fn platform_origin() -> Result<Url, url::ParseError> {
    let configured = if cfg!(debug_assertions) {
        std::env::var("COSMAS_PLATFORM_ORIGIN")
            .unwrap_or_else(|_| "https://app.cosmaneura.com".to_owned())
    } else {
        option_env!("COSMAS_PLATFORM_ORIGIN")
            .unwrap_or("https://app.cosmaneura.com")
            .to_owned()
    };
    let mut origin = Url::parse(&configured)?;
    let local_debug = cfg!(debug_assertions)
        && origin.scheme() == "http"
        && matches!(origin.host_str(), Some("127.0.0.1" | "localhost"));
    if (origin.scheme() != "https" && !local_debug)
        || origin.username() != ""
        || origin.password().is_some()
        || origin.query().is_some()
        || origin.fragment().is_some()
    {
        return Err(url::ParseError::IdnaError);
    }
    origin.set_path("/");
    Ok(origin)
}

fn spawn_deep_link(app: tauri::AppHandle, url: Url) {
    tauri::async_runtime::spawn(auth::handle_deep_link(app, url));
}

#[cfg(test)]
mod tests {
    use super::platform_origin;

    #[test]
    fn production_default_origin_is_https() {
        let origin = platform_origin().unwrap();
        assert!(origin.scheme() == "https" || cfg!(debug_assertions));
        assert!(origin.query().is_none());
        assert!(origin.fragment().is_none());
    }
}
