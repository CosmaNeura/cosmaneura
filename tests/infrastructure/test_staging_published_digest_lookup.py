"""Exercise the actual manifest lookup against an offline untagged registry."""

import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64


def lookup(
    *, release: str, available_name: str, observed: str = DIGEST
) -> subprocess.CompletedProcess[str]:
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    start = source.index('ACR_NAME=$(echo "$STAGING_IMAGE"')
    end = source.index("emit_github_output image_digest ", start)
    # This is the complete real selection, lookup, failure and equality block.
    # The only provider is an offline stub with one available manifest.
    values = {
        "STAGING_IMAGE": "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura",
        "STAGING_REPOSITORY": "cosmaneura/cosmaneura",
        "STAGING_IMAGE_DIGEST": DIGEST,
        "GIT_SHA": SOURCE_SHA,
        "RELEASE_V2_JSON": release,
        "AVAILABLE_NAME": available_name,
        "OBSERVED_DIGEST": observed,
    }
    script = "set -euo pipefail\n" + "\n".join(
        key + "=" + shlex.quote(value) for key, value in values.items()
    )
    script += """
az() {
    [[ "$#" -eq 11 && "$1" == acr && "$2" == manifest && "$3" == show-metadata &&
       "$4" == --registry && "$5" == cosmaneurastagingregistry &&
       "$6" == --name && "$8" == --query && "$9" == digest &&
       "${10}" == -o && "${11}" == tsv ]] || return 64
    [[ "$7" == "$AVAILABLE_NAME" ]] || return 65
    printf '%s\n' "$OBSERVED_DIGEST"
}
"""
    return subprocess.run(
        ["/bin/bash", "-c", script + "\n" + source[start:end]],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "release", ['{"publication":"original"}', '{"publication":"reuse"}']
)
def test_v2_accepts_published_digest_without_a_source_tag(release: str) -> None:
    result = lookup(release=release, available_name="cosmaneura/cosmaneura@" + DIGEST)
    assert result.returncode == 0
    assert result.stdout == ""


def test_legacy_release_keeps_source_tag_verification() -> None:
    result = lookup(release="", available_name="cosmaneura/cosmaneura:" + SOURCE_SHA)
    assert result.returncode == 0


def test_v2_refuses_a_wrong_returned_digest() -> None:
    result = lookup(
        release="{}",
        available_name="cosmaneura/cosmaneura@" + DIGEST,
        observed="sha256:" + "c" * 64,
    )
    assert result.returncode != 0
    assert "does not resolve to the approved image digest" in result.stdout


def test_v2_refuses_an_unavailable_digest_even_when_a_tag_exists() -> None:
    result = lookup(release="{}", available_name="cosmaneura/cosmaneura:" + SOURCE_SHA)
    assert result.returncode != 0
    assert "Failed to resolve the staging candidate image digest" in result.stdout


def test_legacy_does_not_silently_fall_back_to_an_untagged_digest() -> None:
    result = lookup(release="", available_name="cosmaneura/cosmaneura@" + DIGEST)
    assert result.returncode != 0
    assert "Failed to resolve the staging candidate image digest" in result.stdout
