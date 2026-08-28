"""
Extract structured biomarker values from an uploaded lab-report PDF.

Mirrors app/jobs/extract.py: download the PDF from the PHI upload container,
render pages to images, and have the multimodal model read the values, then
normalize each to a canonical biomarker key and persist LabResult rows.

The PDF image goes only to the BAA-covered Azure OpenAI deployment (same path
as note extraction). Content is PHI: logs carry IDs and counts only.
"""

import base64
import io
import json
import os
import uuid
from textwrap import dedent

import PIL.Image
from azure.storage.blob import BlobServiceClient
from pdf2image import convert_from_path
from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
)
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.environments import should_use_test_openai_fallback
from app.services.biomarkers import default_unit, normalize_biomarker
from app.services.doctor_access import doctor_capability_access_in_force
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.lab_panel import LabPanel, LabPanelStatus
from app.models.lab_result import LabResult


def _get_img_uri(img: PIL.Image.Image) -> str:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return "data:image/png;base64," + base64.b64encode(buffer.read()).decode("utf-8")


def download_pages(panel: LabPanel) -> list[str]:
    "download the lab PDF from the PHI container and return page image data-URIs"
    assert panel.azure_file_name
    filename = os.path.basename(panel.azure_file_name)
    temp_path = f"./tmp/lab-{uuid.uuid4()}-{filename}"

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL, credential=AZURE_UPLOADS_ACCOUNT_KEY
    )
    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_CONTAINER_NAME, blob=filename
    )
    try:
        with open(temp_path, "wb") as f:
            f.write(blob_client.download_blob().readall())
        return [_get_img_uri(page) for page in convert_from_path(temp_path, 500)]
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def build_extraction_prompt() -> str:
    return dedent("""
    You are reading a laboratory report. Extract every quantitative biomarker
    result you can see.

    Rules:
    - One entry per biomarker with a numeric value.
    - Use the value and unit exactly as printed.
    - Skip qualitative results (e.g. "negative", "normal") and anything without
      a number.
    - Do not infer or calculate values that are not printed.

    Respond only in JSON:
    {
      "results": [
        { "name": string, "value": number, "unit": string | null }
      ]
    }
    """)


def extract_lab_values(pages: list[str]) -> list[dict]:
    "multimodal extraction of biomarker rows; offline fallback returns []"
    if should_use_test_openai_fallback():
        return []

    content: list = [{"type": "text", "text": build_extraction_prompt()}]
    for page in pages:
        content.append({"type": "image_url", "image_url": {"url": page}})

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )
    raw = response.choices[0].message.content
    if not raw:
        return []
    return json.loads(raw).get("results") or []


def persist_results(panel: LabPanel, raw_results: list[dict]) -> int:
    "normalize + write LabResult rows for a panel; returns count written"
    written = 0
    for item in raw_results:
        raw_name = (item.get("name") or "").strip()
        value = item.get("value")
        if not raw_name or not isinstance(value, (int, float)):
            continue

        key = normalize_biomarker(raw_name)
        unit = item.get("unit") or (default_unit(key) if key else None)

        LabResult(
            lab_panel_id=panel.id,
            doctor_id=panel.doctor_id,
            patient_id=panel.patient_id,
            biomarker=key or raw_name.strip().lower(),
            raw_name=raw_name,
            value=float(value),
            unit=unit,
        ).save()
        written += 1
    return written


def perform(lab_panel_id: str | TypeID | TypeIDType):
    panel_ref = provider_log_ref(f"lab-panel:{lab_panel_id}")
    log.info("extracting labs", panel_ref=panel_ref)

    panel = LabPanel.get(id=lab_panel_id)
    if panel is None:
        log.warning("lab panel not found for extraction")
        return

    doctor = Doctor.get(id=panel.doctor_id)
    if doctor is None or not doctor_capability_access_in_force(doctor, "soap_note"):
        log.info(
            "lab extraction skipped: access unavailable",
            event_code="lab_extraction_access_not_in_force",
            panel_ref=panel_ref,
        )
        return

    if panel.status == LabPanelStatus.completed:
        log.info("lab panel already completed, skipping", panel_ref=panel_ref)
        return

    # Offline/test: no Azure blob and no multimodal model available. Complete
    # the panel empty rather than fail it (a provider can still enter results
    # manually). Live extraction runs in deployed environments.
    if should_use_test_openai_fallback():
        panel.status = LabPanelStatus.completed
        panel.save()
        log.info("lab extraction skipped (offline)", panel_ref=panel_ref)
        return

    try:
        pages = download_pages(panel)
        raw_results = extract_lab_values(pages)
    except Exception as e:
        panel.status = LabPanelStatus.failed
        panel.save()
        # IDs/type only — never lab content
        log.error(
            "lab extraction failed",
            panel_ref=panel_ref,
            error_type=type(e).__name__,
        )
        return

    count = persist_results(panel, raw_results)
    panel.status = LabPanelStatus.completed
    panel.save()

    log.info("extracted labs", panel_ref=panel_ref, count=count)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)
