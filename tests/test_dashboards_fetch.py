import pytest

import main

ENDPOINT = "/api/v1/dashboards/admin"


def _dash(oid):
    return {"oid": oid, "title": f"dash-{oid}"}


class TestGetAllDashboardsPaginated:
    def test_single_short_page_stops_immediately(self, fake_api_client, fake_response):
        client = fake_api_client()
        page = [_dash("1"), _dash("2")]
        client.queue_get(ENDPOINT, [fake_response(json_data=page)])

        result = main.get_all_dashboards_paginated(client, page_size=50)

        assert [d["oid"] for d in result] == ["1", "2"]
        assert client.get_calls == [(ENDPOINT, {"dashboardType": "owner", "skip": 0, "limit": 50})]

    def test_pages_until_short_page(self, fake_api_client, fake_response):
        client = fake_api_client()
        page1 = [_dash(str(i)) for i in range(3)]
        page2 = [_dash(str(i)) for i in range(3, 5)]  # short page -> stop
        client.queue_get(ENDPOINT, [
            fake_response(json_data=page1),
            fake_response(json_data=page2),
        ])

        result = main.get_all_dashboards_paginated(client, page_size=3)

        assert len(result) == 5
        assert [call[1]["skip"] for call in client.get_calls] == [0, 3]

    def test_duplicate_oids_across_pages_are_deduplicated_and_stop(self, fake_api_client, fake_response):
        client = fake_api_client()
        # A server that ignores `skip` and resends the same full page forever
        page = [_dash("1"), _dash("2"), _dash("3")]
        client.queue_get(ENDPOINT, [
            fake_response(json_data=page),
            fake_response(json_data=page),
        ])

        result = main.get_all_dashboards_paginated(client, page_size=3)

        assert [d["oid"] for d in result] == ["1", "2", "3"]
        # Stopped after the second page added nothing new, not looping forever
        assert len(client.get_calls) == 2

    def test_none_response_raises_runtime_error(self, fake_api_client):
        client = fake_api_client()
        client.queue_get(ENDPOINT, [None])

        with pytest.raises(RuntimeError, match="no response"):
            main.get_all_dashboards_paginated(client)

    def test_non_ok_response_raises_runtime_error_with_status_and_body(self, fake_api_client, fake_response):
        client = fake_api_client()
        client.queue_get(ENDPOINT, [fake_response(status_code=500, text="server exploded")])

        with pytest.raises(RuntimeError, match="HTTP 500"):
            main.get_all_dashboards_paginated(client)

    def test_non_list_json_raises_type_error(self, fake_api_client, fake_response):
        client = fake_api_client()
        client.queue_get(ENDPOINT, [fake_response(json_data={"error": "nope"})])

        with pytest.raises(TypeError, match="Unexpected response"):
            main.get_all_dashboards_paginated(client)

    def test_empty_first_page_returns_empty_list(self, fake_api_client, fake_response):
        client = fake_api_client()
        client.queue_get(ENDPOINT, [fake_response(json_data=[])])

        result = main.get_all_dashboards_paginated(client)

        assert result == []
