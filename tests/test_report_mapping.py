import main


class TestHasSubscribedRecipient:
    def test_true_when_a_share_is_subscribed(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(subscribe=True)])
        assert main._has_subscribed_recipient(d) is True

    def test_false_when_no_share_is_subscribed(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(subscribe=False)])
        assert main._has_subscribed_recipient(d) is False

    def test_false_when_shares_is_empty(self, dashboard_factory):
        d = dashboard_factory(shares=[])
        assert main._has_subscribed_recipient(d) is False

    def test_true_when_any_of_several_shares_is_subscribed(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[
            share_factory(subscribe=False, share_id="u1"),
            share_factory(subscribe=True, share_id="u2"),
        ])
        assert main._has_subscribed_recipient(d) is True


class TestMapSubscriptionToReport:
    def test_name_and_enabled(self, dashboard_factory):
        d = dashboard_factory(title="Sales Overview")
        report = main.map_subscription_to_report(d, {})
        assert report["name"] == "Sales Overview"
        assert report["enabled"] is True

    def test_name_override_replaces_default_name(self, dashboard_factory):
        d = dashboard_factory(title="Sales Overview")
        report = main.map_subscription_to_report(d, {}, name_override="Sales Overview (2)")
        assert report["name"] == "Sales Overview (2)"

    def test_enabled_defaults_true_when_active_missing(self, dashboard_factory, subscription_factory):
        sub = subscription_factory()
        del sub["active"]
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["enabled"] is True

    def test_enabled_false_when_subscription_inactive(self, dashboard_factory, subscription_factory):
        d = dashboard_factory(subscription=subscription_factory(active=False))
        report = main.map_subscription_to_report(d, {})
        assert report["enabled"] is False

    def test_dashboard_oid_from_context_when_present(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(context={"dashboardid": "ctx-oid"})
        d = dashboard_factory(oid="dash-oid", subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["dashboardOid"] == "ctx-oid"

    def test_dashboard_oid_falls_back_to_dashboard_oid(self, dashboard_factory):
        d = dashboard_factory(oid="dash-oid")
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["dashboardOid"] == "dash-oid"

    def test_dashboard_entry_has_no_custom_filters_enabled_key(self, dashboard_factory):
        # A real, working report never sends this field; a report body
        # with it present (even as False) is not known to be accepted.
        report = main.map_subscription_to_report(dashboard_factory(), {})
        assert "customFiltersEnabled" not in report["dashboards"][0]

    def test_dashboard_entry_includes_owner_and_title(self, dashboard_factory):
        d = dashboard_factory(owner="owner1", title="Sales Overview")
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["owner"] == "owner1"
        assert report["dashboards"][0]["title"] == "Sales Overview"

    def test_dashboard_entry_name_includes_owner_email_when_resolvable(self, dashboard_factory):
        d = dashboard_factory(owner="owner1", title="Sales Overview")
        users_map = {"owner1": {"EMAIL": "owner@example.com"}}
        report = main.map_subscription_to_report(d, users_map)
        assert report["dashboards"][0]["name"] == "Sales Overview (owner@example.com)"

    def test_dashboard_entry_name_falls_back_to_title_when_owner_unresolved(self, dashboard_factory):
        d = dashboard_factory(owner="ghost-owner", title="Sales Overview")
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["name"] == "Sales Overview"

    def test_dashboard_entry_shares_are_passed_through_unmodified(self, dashboard_factory, share_factory):
        shares = [
            share_factory(subscribe=True, share_id="u1"),
            share_factory(subscribe=False, share_id="u2"),
        ]
        d = dashboard_factory(shares=shares)
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["shares"] == shares

    def test_dashboard_entry_shares_default_to_empty_list_when_missing(self, dashboard_factory):
        d = dashboard_factory()
        del d["shares"]
        report = main.map_subscription_to_report(d, {})
        assert report["dashboards"][0]["shares"] == []

    def test_unsubscribed_shares_are_excluded_from_recipients(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(subscribe=False)])
        report = main.map_subscription_to_report(d, {})
        assert report["recipients"] == []

    def test_group_share_resolves_to_bare_id_and_type(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(share_type="group", share_id="group1")])
        report = main.map_subscription_to_report(d, {})
        assert report["recipients"] == [{"_id": "group1", "type": "group"}]

    def test_user_share_resolves_against_users_map(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(share_type="user", share_id="u1")])
        users_map = {
            "u1": {
                "USER_ID": "u1", "EMAIL": "u1@example.com",
                "FIRST_NAME": "Jane", "LAST_NAME": "Doe", "USER_NAME": "jane.doe",
            }
        }
        report = main.map_subscription_to_report(d, users_map)
        assert report["recipients"] == [{
            "_id": "u1", "email": "u1@example.com", "firstName": "Jane",
            "lastName": "Doe", "type": "user",
        }]

    def test_user_share_not_in_users_map_resolves_to_nones(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(share_type="user", share_id="unknown")])
        report = main.map_subscription_to_report(d, {})
        assert report["recipients"] == [{
            "_id": "unknown", "email": None, "firstName": None,
            "lastName": None, "type": "user",
        }]

    def test_user_share_recipient_has_no_username_key(self, dashboard_factory, share_factory):
        # A real, working report's recipients don't carry this field.
        d = dashboard_factory(shares=[share_factory(share_type="user", share_id="u1")])
        users_map = {"u1": {"EMAIL": "u1@example.com", "USER_NAME": "u1"}}
        report = main.map_subscription_to_report(d, users_map)
        assert "userName" not in report["recipients"][0]

    def test_user_share_without_share_id_treated_as_bare_recipient(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[share_factory(share_type="user", share_id=None)])
        report = main.map_subscription_to_report(d, {"u1": {"EMAIL": "x"}})
        assert report["recipients"] == [{"_id": None, "type": "user"}]

    def test_share_type_defaults_to_user_when_missing(self, dashboard_factory):
        d = dashboard_factory(shares=[{"subscribe": True, "shareId": "u1"}])
        report = main.map_subscription_to_report(d, {})
        assert report["recipients"][0]["type"] == "user"

    def test_multiple_shares_mixed_types(self, dashboard_factory, share_factory):
        d = dashboard_factory(shares=[
            share_factory(share_type="user", share_id="u1"),
            share_factory(share_type="group", share_id="g1"),
            share_factory(subscribe=False, share_id="u2"),
        ])
        report = main.map_subscription_to_report(d, {"u1": {"EMAIL": "u1@example.com"}})
        assert [r["_id"] for r in report["recipients"]] == ["u1", "g1"]

    def test_is_data_change_triggered_via_flag_clears_schedule(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(isDataChange=True, schedule="0 0 8 * * 1")
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["schedule"] == {}
        assert report["events"] == ["every_ec_update"]

    def test_is_data_change_triggered_via_type_onupdate(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(type="onUpdate", schedule="0 0 8 * * 1")
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["schedule"] == {}
        assert report["events"] == ["every_ec_update"]

    def test_cron_subscription_builds_schedule_and_no_events(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(schedule="0 0 8 * * 1")
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["schedule"] != {}
        assert report["events"] == []

    def test_no_schedule_string_leaves_schedule_empty(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(schedule=None)
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["schedule"] == {}

    def test_report_type_pdf_true_from_email_settings(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(emailSettings={"isPdf": True})
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["reportType"]["PDF"] is True

    def test_report_type_pdf_true_from_inline_embed(self, dashboard_factory, subscription_factory):
        sub = subscription_factory(reportType={"inline": True})
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert report["reportType"]["PDF"] is True

    def test_report_type_pdf_false_by_default(self, dashboard_factory):
        report = main.map_subscription_to_report(dashboard_factory(), {})
        assert report["reportType"]["PDF"] is False

    def test_report_type_pdf_rendering_settings_are_never_forwarded(self, dashboard_factory, subscription_factory):
        # A real, working report for a subscription with reportType.pdf
        # rendering settings has PDF: true but no nested "pdf" object, so
        # this script doesn't forward the legacy pdf settings at all.
        sub = subscription_factory(reportType={"pdf": {"paperSize": "A4"}})
        d = dashboard_factory(subscription=sub)
        report = main.map_subscription_to_report(d, {})
        assert "pdf" not in report["reportType"]

    def test_report_type_pdf_settings_omitted_when_absent(self, dashboard_factory):
        report = main.map_subscription_to_report(dashboard_factory(), {})
        assert "pdf" not in report["reportType"]

    def test_other_report_type_flags_always_false(self, dashboard_factory):
        report = main.map_subscription_to_report(dashboard_factory(), {})
        for key in ("CSV", "URL", "XLS", "JASPER_PDF", "JASPER_XLS"):
            assert report["reportType"][key] is False

    def test_report_type_has_no_xls_combined_key(self, dashboard_factory):
        # A real, working report doesn't send this key at all.
        report = main.map_subscription_to_report(dashboard_factory(), {})
        assert "XLS_Combined" not in report["reportType"]

    def test_priority_and_run_on_finish_defaults(self, dashboard_factory):
        report = main.map_subscription_to_report(dashboard_factory(), {})
        assert report["priority"] == "normal"
        assert report["runOnFinish"] == {"fileShare": {"overwriteExisting": False}}

    def test_missing_subscription_treated_as_empty(self, dashboard_factory):
        d = dashboard_factory(subscription=None)
        report = main.map_subscription_to_report(d, {})
        assert report["schedule"] == {}
        assert report["enabled"] is True
        assert report["reportType"]["PDF"] is False

    def test_missing_shares_treated_as_empty(self, dashboard_factory):
        d = dashboard_factory()
        del d["shares"]
        report = main.map_subscription_to_report(d, {})
        assert report["recipients"] == []
