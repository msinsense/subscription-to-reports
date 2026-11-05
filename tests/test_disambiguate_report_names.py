import main


def _dash(oid, title):
    return {"oid": oid, "title": title}


class TestDisambiguateReportNames:
    def test_no_collision_returns_empty_overrides(self):
        candidates = [_dash("a", "Sales"), _dash("b", "Marketing")]
        assert main._disambiguate_report_names(candidates) == {}

    def test_single_candidate_gets_no_override(self):
        candidates = [_dash("a", "Sales")]
        assert main._disambiguate_report_names(candidates) == {}

    def test_two_colliding_titles_suffix_only_the_second(self):
        candidates = [_dash("b-oid", "Sales"), _dash("a-oid", "Sales")]
        overrides = main._disambiguate_report_names(candidates)
        # "a-oid" sorts before "b-oid", so it keeps the plain name.
        assert overrides == {"b-oid": "Sales (2)"}

    def test_three_colliding_titles_suffix_second_and_third(self):
        candidates = [_dash("c-oid", "Sales"), _dash("a-oid", "Sales"), _dash("b-oid", "Sales")]
        overrides = main._disambiguate_report_names(candidates)
        assert overrides == {
            "b-oid": "Sales (2)",
            "c-oid": "Sales (3)",
        }

    def test_ordering_is_stable_regardless_of_input_order(self):
        forward = main._disambiguate_report_names([_dash("a-oid", "Sales"), _dash("b-oid", "Sales")])
        backward = main._disambiguate_report_names([_dash("b-oid", "Sales"), _dash("a-oid", "Sales")])
        assert forward == backward == {"b-oid": "Sales (2)"}

    def test_independent_collision_groups_are_handled_separately(self):
        candidates = [
            _dash("a-oid", "Sales"), _dash("b-oid", "Sales"),
            _dash("x-oid", "Marketing"), _dash("y-oid", "Marketing"),
        ]
        overrides = main._disambiguate_report_names(candidates)
        assert overrides == {
            "b-oid": "Sales (2)",
            "y-oid": "Marketing (2)",
        }

    def test_empty_candidates_returns_empty_overrides(self):
        assert main._disambiguate_report_names([]) == {}
