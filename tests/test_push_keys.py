"""VAPID keys from settings (.env) win over data/vapid.json; half a pair is ignored."""
from unittest import mock

from app import push


def test_settings_keys_win_over_file(tmp_path):
    with mock.patch.object(push.settings, "vapid_private_key", "priv"), \
            mock.patch.object(push.settings, "vapid_public_key", "pub"), \
            mock.patch.object(push, "_KEY_PATH", tmp_path / "vapid.json"):
        (tmp_path / "vapid.json").write_text('{"private_key": "file", "public_key": "file"}')
        assert push._load_keys() == {"private_key": "priv", "public_key": "pub"}


def test_half_a_pair_falls_back_to_file(tmp_path):
    with mock.patch.object(push.settings, "vapid_private_key", "priv"), \
            mock.patch.object(push.settings, "vapid_public_key", ""), \
            mock.patch.object(push, "_KEY_PATH", tmp_path / "vapid.json"):
        assert push._load_keys() is None
