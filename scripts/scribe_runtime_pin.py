"""Reviewed immutable inputs for the unpublished, locally rebuilt runtime OCI."""

SCRIBE_SOURCE_REPOSITORY = "https://github.com/CosmaNeura/cosmaneura-scribe.git"
SCRIBE_SOURCE_REVISION = "45e979fd680ed7b9aa5e3a5a4f6f7e72ff27618f"
# Canonical OCI manifest of the fresh runtime, not a tag or Core image ID.
SCRIBE_RUNTIME_IMAGE_DIGEST = (
    "sha256:42c62756b5a4a62eda41400a8dcd4181499504b33887928d4dd2950cda9ecffc"
)
UV_BUILDER_IMAGE = "ghcr.io/astral-sh/uv@sha256:8f1995ceab97517a621cbdd9c795bba9e8119cf6c472e2b9983bc7a70ac5e3e0"
DOCKERFILE_FRONTEND = "docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e"
RUNTIME_DIGEST_LABEL = "com.cosmaneura.scribe-runtime-image-digest"
