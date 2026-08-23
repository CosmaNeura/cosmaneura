use async_trait::async_trait;
use zeroize::Zeroizing;

use crate::error::{CommandError, CommandResult};

#[async_trait]
pub trait NativeCaptureAdapter: Send + Sync {
    fn is_enabled(&self) -> bool;

    async fn start(&self, upload_capability: Zeroizing<String>) -> CommandResult<()>;

    async fn stop(&self) -> CommandResult<()>;
}

/// Runtime default for every build until a provider-owned SDK integration is
/// separately reviewed. Keeping this adapter concrete makes "disabled" an
/// executable boundary instead of an environment convention.
pub struct DisabledCaptureAdapter;

#[async_trait]
impl NativeCaptureAdapter for DisabledCaptureAdapter {
    fn is_enabled(&self) -> bool {
        false
    }

    async fn start(&self, _upload_capability: Zeroizing<String>) -> CommandResult<()> {
        Err(CommandError::unavailable())
    }

    async fn stop(&self) -> CommandResult<()> {
        Ok(())
    }
}

/// Provider-specific names are confined to this adapter shell. No Recall SDK
/// or network dependency is present, and this type cannot be constructed by
/// the runtime provider registry in this tranche.
#[cfg(feature = "recall-provider")]
#[allow(dead_code)]
pub struct RecallCaptureAdapter;

#[cfg(feature = "recall-provider")]
#[async_trait]
impl NativeCaptureAdapter for RecallCaptureAdapter {
    fn is_enabled(&self) -> bool {
        false
    }

    async fn start(&self, _upload_capability: Zeroizing<String>) -> CommandResult<()> {
        Err(CommandError::unavailable())
    }

    async fn stop(&self) -> CommandResult<()> {
        Ok(())
    }
}
