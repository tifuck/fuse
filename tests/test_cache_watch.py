"""Prompt-cache miss detection (app/chat_generation/cache_watch.py). No DB."""
import unittest

from app import context_compact as context
from app.chat_generation import cache_watch as watch


def usage(prompt, cached, status="known"):
    return {"prompt_tokens": prompt, "cached_tokens": cached, "cache_usage_status": status}


def probe(prompt, cached, *, at=1000.0, prior=None, model="m", source="chatgpt", cred=1, status="known"):
    return watch.probe(usage(prompt, cached, status), model=model, source=source, cred=cred, prior=prior, now=at)


class CacheWatchTest(unittest.TestCase):
    def setUp(self):
        self.warm = probe(50_000, 45_000)

    def test_miss_inside_the_cache_lifetime(self):
        current = probe(52_000, 1_000, at=1100, prior=self.warm)
        event = watch.miss(self.warm, current)
        self.assertEqual(event, {"event": "cache", "state": "miss", "prompt": 52_000, "cached": 1_000, "idle_s": 100, "reason": ""})

    def test_hit_is_not_a_miss(self):
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 49_000, at=1100, prior=self.warm)))

    def test_expired_cache_is_ordinary_expiry(self):
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 0, at=1000 + watch.DEFAULT_TTL_S + 1, prior=self.warm)))

    def test_claude_lifetime_is_an_hour(self):
        warm = probe(50_000, 45_000, source="claude")
        self.assertIsNotNone(watch.miss(warm, probe(52_000, 0, at=1000 + 1800, prior=warm, source="claude")))

    def test_first_turn_and_never_cached_chats_stay_silent(self):
        # Nothing was ever served from cache, so there is nothing to lose.
        cold = probe(50_000, 0)
        self.assertFalse(cold["hit"])
        self.assertIsNone(watch.miss(cold, probe(52_000, 0, at=1100, prior=cold)))
        self.assertIsNone(watch.miss(None, probe(52_000, 0, at=1100)))

    def test_hit_is_sticky_across_requests(self):
        missed = probe(52_000, 0, at=1100, prior=self.warm)
        self.assertTrue(missed["hit"])
        self.assertIsNotNone(watch.miss(missed, probe(53_000, 0, at=1200, prior=missed)))

    def test_model_or_provider_change_is_not_judged(self):
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 0, at=1100, prior=self.warm, model="other")))
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 0, at=1100, prior=self.warm, source="zai")))

    def test_small_prompts_and_small_shared_prefixes_are_ignored(self):
        small = probe(5_000, 4_000)
        self.assertIsNone(watch.miss(small, probe(3_000, 0, at=1100, prior=small)))
        # A big undo leaves little to share; only the shared prefix counts.
        self.assertIsNone(watch.miss(self.warm, probe(3_000, 0, at=1100, prior=self.warm)))

    def test_unknown_cache_split_and_devin_are_never_probed(self):
        self.assertIsNone(probe(52_000, 0, status="unknown"))
        self.assertIsNone(probe(52_000, 0, source="devin"))

    def test_reason_names_an_account_switch_or_settings_change(self):
        current = probe(52_000, 0, at=1100, prior=self.warm, cred=2)
        self.assertEqual(watch.miss(self.warm, current)["reason"], "account")
        self.assertEqual(watch.miss(self.warm, current, settings_changed=True)["reason"], "settings")

    def test_public_view_hides_the_account(self):
        view = watch.public(self.warm)
        self.assertEqual(view, {"at": 1000.0, "prompt": 50_000, "model": "m", "ttl": watch.DEFAULT_TTL_S, "hit": True})

    def test_probe_survives_compaction_state_and_a_checkpoint_clears_it(self):
        state = context.parse_state(context.dump_state({"cache": self.warm}))
        self.assertEqual(state["cache"], self.warm)
        self.assertIsNone(context.parse_state({"cache": {"at": "nope"}})["cache"])
        self.assertIsNone(context.append_checkpoint(state, {"id": "c", "summary": "s"})["cache"])


if __name__ == "__main__":
    unittest.main()
