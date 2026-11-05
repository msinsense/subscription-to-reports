import main


class FakeAccess:
    def __init__(self, users):
        self._users = users

    def get_users_all(self):
        return self._users


class TestGenUsersMap:
    def test_indexes_by_user_id(self):
        users = [
            {"USER_ID": "u1", "EMAIL": "a@example.com"},
            {"USER_ID": "u2", "EMAIL": "b@example.com"},
        ]
        result = main.gen_users_map(FakeAccess(users))
        assert result == {"u1": users[0], "u2": users[1]}

    def test_skips_non_dict_entries(self):
        result = main.gen_users_map(FakeAccess([{"USER_ID": "u1"}, "not-a-dict", None]))
        assert result == {"u1": {"USER_ID": "u1"}}

    def test_skips_dicts_missing_user_id(self):
        result = main.gen_users_map(FakeAccess([{"EMAIL": "no-id@example.com"}]))
        assert result == {}

    def test_skips_dicts_with_falsy_user_id(self):
        result = main.gen_users_map(FakeAccess([{"USER_ID": ""}, {"USER_ID": None}]))
        assert result == {}

    def test_empty_users_list(self):
        assert main.gen_users_map(FakeAccess([])) == {}
