import httpx

from app import log
from app.configuration.recall import RECALL_API_BASE, RECALL_API_KEY


def create_bot(meeting_url: str, zak_token: str, bot_name: str = "Cosma", manual_recording: bool = False) -> dict:
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "zoom": {
            "zak_token": zak_token,
        },
    }
    if manual_recording:
        payload["recording_config"] = None

    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/",
            json=payload,
            headers={
                "Authorization": f"Token {RECALL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if not response.is_success:
            log.error("recall api error", status=response.status_code, body=response.text, payload=payload)
        response.raise_for_status()
        data = response.json()

    log.info("recall bot created", bot_id=data.get("id"), meeting_url=meeting_url)
    return data


def start_recording(bot_id: str) -> dict:
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/start_recording/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error("recall start_recording error", status=response.status_code, body=response.text)
        response.raise_for_status()
        data = response.json()
    log.info("recall recording started", bot_id=bot_id, recording_id=data.get("id"))
    return data


def stop_recording(bot_id: str) -> None:
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/stop_recording/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error("recall stop_recording error", status=response.status_code, body=response.text)
        response.raise_for_status()
    log.info("recall recording stopped", bot_id=bot_id)


def create_async_transcript(recording_id: str, language_code: str = "auto") -> dict:
    payload = {"provider": {"recallai_async": {"language_code": language_code}}}
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/recording/{recording_id}/create_transcript/",
            json=payload,
            headers={
                "Authorization": f"Token {RECALL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if not response.is_success:
            log.error("recall create_transcript error", status=response.status_code, body=response.text)
        response.raise_for_status()
        data = response.json()
    log.info("recall async transcript created", recording_id=recording_id, transcript_id=data.get("id"))
    return data


def get_transcript(transcript_id: str) -> dict:
    """GET /transcript/{id}/ — returns metadata including data.download_url."""
    with httpx.Client() as client:
        response = client.get(
            f"{RECALL_API_BASE}/transcript/{transcript_id}/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error("recall get_transcript error", status=response.status_code, body=response.text)
        response.raise_for_status()
    return response.json()


def download_transcript_data(download_url: str) -> list[dict]:
    """Fetch the pre-signed transcript download URL (no auth required)."""
    with httpx.Client() as client:
        response = client.get(download_url)
        response.raise_for_status()
    return response.json()
