"""Execute the template bootstrap only in an owned synthetic project."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_bootstrap_writes_identifiers_without_copying_extra_credentials(
    tmp_path: Path,
) -> None:
    template = tmp_path / ".copier"
    template.mkdir()
    source = Path(__file__).resolve().parents[2] / ".copier/bootstrap.py"
    shutil.copy2(source, template / "bootstrap.py")
    (template / "README.md").write_text("Synthetic template readme\n")
    (tmp_path / "pyproject.toml").write_text('name = "template"\n')
    (tmp_path / ".env.shared").write_text(
        "export EMAIL_FROM_ADDRESS=template@example.invalid\n"
        "export OP_ACCOUNT=template.1password.com\n"
        "export OP_VAULT_UID=template-vault\n"
    )
    (tmp_path / "Justfile").write_text(
        'PROJECT_NAME := "template"\nPYTHON_PRODUCTION_IMAGE_NAME := "template"\n'
    )
    credential = "synthetic-unused-service-account-token"
    (tmp_path / ".copier-answers.yml").write_text(
        json.dumps(
            {
                "project_slug": "synthetic-project",
                "from_email": "template@example.invalid",
                "production_image_name": "example.invalid/synthetic",
                "one_password_account": "synthetic.1password.com",
                "one_password_vault": "syntheticvaultidentifier",
                "one_password_service_account_token": credential,
            }
        )
    )

    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(template / "bootstrap.py")],
        cwd=tmp_path,
        env={},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout == result.stderr == ""
    assert (tmp_path / ".env.shared").read_text() == (
        "export EMAIL_FROM_ADDRESS=template@example.invalid\n"
        "export OP_ACCOUNT=synthetic.1password.com\n"
        "export OP_VAULT_UID=syntheticvaultidentifier\n"
    )
    assert (tmp_path / "pyproject.toml").read_text() == 'name = "synthetic-project"\n'
    assert (tmp_path / "Justfile").read_text() == (
        'PROJECT_NAME := "synthetic-project"\n'
        'PYTHON_PRODUCTION_IMAGE_NAME := "example.invalid/synthetic"\n'
    )
    assert (tmp_path / "README.md").read_text() == "Synthetic template readme\n"
    for relative in (".env.shared", "pyproject.toml", "Justfile", "README.md"):
        assert credential not in (tmp_path / relative).read_text()
