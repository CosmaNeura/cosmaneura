"""Exercise the updated schema generator without app bootstrap or external services."""

import importlib.util
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

codegen = pytest.importorskip(
    "datamodel_code_generator", reason="Requires the debugging-extras group"
)
Error, InputFileType, generate = codegen.Error, codegen.InputFileType, codegen.generate
FileLock = import_module("filelock").FileLock

# The legacy generator re-exported this enum but did not have an enums module.
DataModelType = getattr(codegen, "DataModelType")


def _generate(schema: dict, output: Path) -> None:
    generate(
        input_=json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
        disable_timestamp=True,
    )


def test_generated_model_preserves_validation(tmp_path: Path) -> None:
    output = tmp_path / "model.py"
    _generate(
        {
            "title": "SyntheticVisit",
            "type": "object",
            "required": ["duration_minutes"],
            "properties": {"duration_minutes": {"type": "integer", "minimum": 1}},
        },
        output,
    )
    spec = importlib.util.spec_from_file_location("synthetic_generated_model", output)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = module.SyntheticVisit
    model.model_rebuild(_types_namespace=vars(module))
    assert model(duration_minutes=30).model_dump() == {"duration_minutes": 30}
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        model(duration_minutes=0)


@pytest.mark.parametrize("extension", ["x-python-import", "customTypePath"])
def test_schema_import_extensions_reject_statement_injection(
    tmp_path: Path, extension: str
) -> None:
    # Harmless sentinel; never execute code generated from the rejected input.
    injected_name = "Decimal\nprint('SYNTHETIC_INJECTION_SENTINEL')"
    definition: dict[str, Any] = {"type": "object"}
    if extension == "x-python-import":
        definition[extension] = {"module": "decimal", "name": injected_name}
    else:
        definition[extension] = f"decimal.{injected_name}"
    schema = {
        "title": "SyntheticInput",
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/External"}},
        "$defs": {"External": definition},
    }
    if extension == "customTypePath":
        schema["properties"] = {"value": definition}
    with pytest.raises(Error, match="identifier"):
        _generate(schema, tmp_path / "rejected.py")


def test_virtualenv_creates_an_isolated_offline_interpreter(tmp_path: Path) -> None:
    target = tmp_path / "isolated"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "virtualenv",
            "--no-seed",
            "--no-download",
            "--no-periodic-update",
            "--app-data",
            str(tmp_path / "cache"),
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = subprocess.run(
        [
            str(target / "bin/python"),
            "-I",
            "-c",
            "import json,sys; print(json.dumps([sys.prefix,sys.base_prefix]))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    prefix, base = json.loads(result.stdout)
    assert Path(prefix).resolve() == target.resolve()
    assert prefix != base


def test_filelock_excludes_a_second_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "synthetic.lock"
    script = (
        "import sys; from filelock import FileLock, Timeout\n"
        "try:\n"
        "    with FileLock(sys.argv[1], timeout=0): pass\n"
        "except Timeout: sys.exit(23)\n"
    )
    with FileLock(lock_path):
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(lock_path)], timeout=10
        )
        assert blocked.returncode == 23
    acquired = subprocess.run(
        [sys.executable, "-c", script, str(lock_path)], timeout=10
    )
    assert acquired.returncode == 0
