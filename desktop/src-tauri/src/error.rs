use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CommandError {
    code: &'static str,
    message: &'static str,
}

impl CommandError {
    pub const fn new(code: &'static str, message: &'static str) -> Self {
        Self { code, message }
    }

    pub const fn unavailable() -> Self {
        Self::new(
            "desktop_unavailable",
            "Desktop capture is not available in this build.",
        )
    }

    pub const fn invalid_state() -> Self {
        Self::new(
            "invalid_capture_state",
            "The capture workflow is not ready for that action.",
        )
    }

    pub const fn authorization_failed() -> Self {
        Self::new(
            "authorization_failed",
            "The secure desktop authorization could not be completed.",
        )
    }

    pub const fn local_stop_failed() -> Self {
        Self::new(
            "local_stop_failed",
            "Local audio capture could not be stopped safely.",
        )
    }
}

pub type CommandResult<T> = Result<T, CommandError>;
