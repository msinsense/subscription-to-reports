import main


class TestIsExistingReportError:
    def test_true_for_client_message_id_match(self):
        body = {"error": {"clientMessage": {"id": "@reportManager/error/api/report/exist"}}}
        assert main._is_existing_report_error(body) is True

    def test_true_for_more_info_existing_reports_error(self):
        body = {"error": {"moreInfo": {"ExistingReportsError": [{"_id": "abc"}]}}}
        assert main._is_existing_report_error(body) is True

    def test_false_for_unrelated_error(self):
        body = {"error": {"clientMessage": {"id": "@reportManager/error/something/else"}, "moreInfo": {}}}
        assert main._is_existing_report_error(body) is False

    def test_false_for_none_body(self):
        assert main._is_existing_report_error(None) is False

    def test_false_for_empty_body(self):
        assert main._is_existing_report_error({}) is False

    def test_false_when_error_key_missing(self):
        assert main._is_existing_report_error({"something": "else"}) is False

    def test_false_when_client_message_missing(self):
        body = {"error": {"moreInfo": {}}}
        assert main._is_existing_report_error(body) is False


class TestExistingReportId:
    def test_extracts_id_from_more_info(self):
        body = {"error": {"moreInfo": {"ExistingReportsError": [{"_id": "existing-id-1"}, {"_id": "other"}]}}}
        assert main._existing_report_id(body) == "existing-id-1"

    def test_falls_back_to_client_message_values_ids(self):
        body = {"error": {"clientMessage": {"values": {"ids": ["fallback-id"]}}}}
        assert main._existing_report_id(body) == "fallback-id"

    def test_more_info_takes_precedence_over_client_message(self):
        body = {
            "error": {
                "moreInfo": {"ExistingReportsError": [{"_id": "more-info-id"}]},
                "clientMessage": {"values": {"ids": ["client-message-id"]}},
            }
        }
        assert main._existing_report_id(body) == "more-info-id"

    def test_none_when_nothing_present(self):
        assert main._existing_report_id({"error": {}}) is None

    def test_none_for_none_body(self):
        assert main._existing_report_id(None) is None

    def test_none_when_more_info_list_empty(self):
        body = {"error": {"moreInfo": {"ExistingReportsError": []}}}
        assert main._existing_report_id(body) is None

    def test_none_when_client_message_ids_empty(self):
        body = {"error": {"clientMessage": {"values": {"ids": []}}}}
        assert main._existing_report_id(body) is None
