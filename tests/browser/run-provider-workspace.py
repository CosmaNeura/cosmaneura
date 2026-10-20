"""Run fictional browser/Core/Scribe acceptance in a new private PostgreSQL cluster."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "core-python",
        "scribe-root",
        "scribe-python",
        "node",
        "pg-bin",
        "playwright-module",
        "playwright-test-module",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    # Keep venv executable paths: following their symlinks loses venv activation.
    paths = {key: value.absolute() for key, value in vars(args).items()}
    if not all(value.exists() for value in paths.values()):
        parser.error("Every supplied tool and module path must exist")
    core = Path(__file__).resolve().parents[2]
    pg = paths["pg_bin"]
    version = subprocess.check_output([str(pg / "postgres"), "--version"], text=True)
    if not version.startswith("postgres (PostgreSQL) 16."):
        parser.error("This isolated rehearsal requires PostgreSQL 16")
    if not (paths["scribe_root"] / "tests/test_provider_workspace_wire.py").is_file():
        parser.error("Scribe checkout lacks the provider-workspace receiver")
    output = Path(
        tempfile.mkdtemp(prefix="scribe-configured-core.provider.", dir="/private/tmp")
    )
    data = output / "data"
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}

    def run(command):
        subprocess.run([str(part) for part in command], check=True, cwd=core, env=env)

    print(f"Synthetic rehearsal receipts and stopped database: {output}", flush=True)
    try:
        run(
            [
                pg / "initdb",
                "-D",
                data,
                "-U",
                "scribe_configured_owner",
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        run(
            [
                pg / "pg_ctl",
                "-D",
                data,
                "-l",
                output / "postgres.log",
                "-o",
                f'-h "" -k {output} -p 55458',
                "-w",
                "start",
            ]
        )
        for database in (
            "scribe_configured_core_test",
            "scribe_provider_browser_run_test",
        ):
            run(
                [
                    pg / "createdb",
                    "-h",
                    output,
                    "-p",
                    "55458",
                    "-U",
                    "scribe_configured_owner",
                    "-T",
                    "template0",
                    "-E",
                    "UTF8",
                    database,
                ]
            )
        env.update(
            {
                "PYTHONPATH": ".",
                "CI": "false",
                "PROVIDER_WORKSPACE_BROWSER": "1",
                "SCRIBE_REVIEW_RECEIVER_ROOT": str(paths["scribe_root"]),
                "SCRIBE_PYTHON": str(paths["scribe_python"]),
                "SCRIBE_NODE": str(paths["node"]),
                "SCRIBE_PLAYWRIGHT_MODULE": str(paths["playwright_module"]),
                "SCRIBE_PLAYWRIGHT_TEST_MODULE": str(paths["playwright_test_module"]),
                "SCRIBE_CONFIGURED_CORE_TEST_URL": f"postgresql://scribe_configured_owner@/scribe_configured_core_test?host={output}&port=55458",
                "COSMANEURA_SCRIBE_TEST_DATABASE_URL": f"postgresql+asyncpg://scribe_configured_owner@/scribe_provider_browser_run_test?host={output}&port=55458",
            }
        )
        run(
            [
                paths["core_python"],
                "-m",
                "pytest",
                "-q",
                "tests/authority_kernel/provider_workspace_browser_test.py",
                "--confcutdir=tests/authority_kernel",
                f"--basetemp={output / 'browser'}",
                f"--junitxml={output / 'browser.xml'}",
            ]
        )
    finally:
        if (data / "PG_VERSION").exists():
            subprocess.run(
                [str(pg / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                check=True,
                cwd=core,
                env=env,
            )
    revisions = {}
    for name, checkout in (("core", core), ("scribe", paths["scribe_root"])):
        revisions[name] = {
            "sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
            ).strip(),
            "dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=checkout, text=True
                ).strip()
            ),
        }
    (output / "result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "synthetic_only": True,
                "database_stopped": True,
                "sources": revisions,
            }
        )
    )


if __name__ == "__main__":
    main()
