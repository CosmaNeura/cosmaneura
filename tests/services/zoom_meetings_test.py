from unittest import mock

from app.services.zoom import get_user_meetings


def test_get_user_meetings_merges_upcoming_and_live_without_duplicates():
    doctor = mock.Mock(id="doctor-test-id")
    integration = mock.Mock(provider_user_id="zoom-user-id")
    upcoming = {
        "id": 123456789,
        "topic": "Scheduled topic",
        "start_time": "2026-10-28T23:30:00Z",
        "duration": 30,
        "join_url": "https://zoom.us/j/123456789",
    }
    live = {
        "id": 123456789,
        "topic": "Live topic",
        "status": "started",
    }
    live_only = {
        "id": 81054466452,
        "topic": "Live-only meeting",
        "start_time": "2026-10-28T23:45:00Z",
        "duration": 45,
        "join_url": "https://zoom.us/j/81054466452",
    }

    with (
        mock.patch(
            "app.services.zoom.get_zoom_integration",
            return_value=integration,
        ),
        mock.patch(
            "app.services.zoom._zoom_get",
            side_effect=[
                {"meetings": [upcoming]},
                {"meetings": [live, live_only]},
            ],
        ) as zoom_get,
    ):
        meetings = get_user_meetings(doctor)

    assert meetings == [
        {
            **upcoming,
            **live,
        },
        live_only,
    ]
    assert zoom_get.call_args_list == [
        mock.call(
            "/users/zoom-user-id/meetings",
            integration=integration,
            operation="get_user_meetings",
            params={"type": "upcoming", "page_size": "100"},
        ),
        mock.call(
            "/users/zoom-user-id/meetings",
            integration=integration,
            operation="get_user_meetings",
            params={"type": "live", "page_size": "100"},
        ),
    ]
