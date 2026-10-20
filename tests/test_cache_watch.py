"""Prompt-cache miss detection (app/chat_generation/cache_watch.py). No DB."""
import unittest

from app import context_compact as context
from app.chat_generation import cache_watch as watch


def usage(prompt, cached, status="known"):
    return {"prompt_tokens": prompt, "cached_tokens": cached, "cache_usage_status": status}


def probe(prompt, cached, *, at=1000.0, prior=None, model="m", source="zai", cred=1, status="known", run=None, request=None):
    return watch.probe(usage(prompt, cached, status), model=model, source=source, cred=cred, prior=prior, now=at,
                       run=run, request=request)


SYSTEM = {"role": "system", "content": "You are Fuse."}
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]


def sent(*messages, tools=TOOLS, effort="medium"):
    return watch.fingerprint([SYSTEM, *messages], tools, effort)


def user(text):
    return {"role": "user", "content": text}


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

    def test_openrouter_anthropic_models_get_claudes_hour(self):
        # Fuse sends OpenRouter cache_control ttl "1h" for anthropic/ models only.
        self.assertEqual(watch.ttl_for("openrouter", "openrouter/anthropic/claude-sonnet-5"), 3600)
        self.assertEqual(watch.ttl_for("openrouter", "openrouter/z-ai/glm-5.3"), watch.DEFAULT_TTL_S)

    def test_chatgpt_keeps_gpt_5_6_and_later_for_thirty_minutes(self):
        # OpenAI: GPT-5.6+ prompts are cached 30 min by default; older GPT-5
        # models keep them in memory for 5-10 minutes.
        for model in ("gpt-5.6-sol", "gpt-6.1-sol", "gpt-6-astra"):
            self.assertEqual(watch.ttl_for("chatgpt", model), 1800, model)
        for model in ("gpt-5", "gpt-5-codex", "gpt-5.4-mini", "gpt-5.5"):
            self.assertEqual(watch.ttl_for("chatgpt", model), watch.DEFAULT_TTL_S, model)
        warm = probe(50_000, 45_000, source="chatgpt", model="gpt-6.1-sol")
        self.assertIsNotNone(watch.miss(warm, probe(52_000, 0, at=1000 + 1200, prior=warm, source="chatgpt", model="gpt-6.1-sol")))
        self.assertIsNone(watch.miss(warm, probe(52_000, 0, at=1000 + 1801, prior=warm, source="chatgpt", model="gpt-6.1-sol")))

    def test_first_turn_and_never_cached_chats_stay_silent(self):
        # Nothing was ever served from cache, so there is nothing to lose.
        cold = probe(50_000, 0)
        self.assertFalse(cold["hit"])
        self.assertIsNone(watch.miss(cold, probe(52_000, 0, at=1100, prior=cold)))
        self.assertIsNone(watch.miss(None, probe(52_000, 0, at=1100)))

    def test_a_token_cached_count_is_not_a_hit(self):
        # Grok reports 128 cached tokens on a cold prompt. A new chat whose
        # first two rounds both came back with 128 was never warm.
        first = probe(13_204, 128, source="grok")
        self.assertFalse(first["hit"])
        second = probe(16_213, 128, at=1009, prior=first, source="grok")
        self.assertIsNone(watch.miss(first, second))
        self.assertTrue(probe(13_204, watch.HIT_MIN_TOKENS)["hit"])

    def test_hit_is_sticky_across_requests(self):
        missed = probe(52_000, 0, at=1100, prior=self.warm)
        self.assertTrue(missed["hit"])
        self.assertIsNotNone(watch.miss(missed, probe(53_000, 0, at=1200, prior=missed)))

    def test_model_or_provider_change_is_not_judged(self):
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 0, at=1100, prior=self.warm, model="other")))
        self.assertIsNone(watch.miss(self.warm, probe(52_000, 0, at=1100, prior=self.warm, source="grok")))

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

    def test_one_warning_per_run_survives_a_resume(self):
        warm = probe(50_000, 45_000, run="r1")
        missed = {**probe(52_000, 0, at=1100, prior=warm, run="r1"), "warned": True}
        self.assertTrue(watch.warned(missed, "r1"))
        self.assertFalse(watch.warned(missed, "r2"))
        self.assertFalse(watch.warned(warm, "r1"))
        # The flag rides along on later rounds of the same run (stored on the
        # thread), so a resumed run does not warn again; a new run starts clean.
        later = probe(53_000, 52_000, at=1200, prior=missed, run="r1")
        self.assertTrue(watch.warned(later, "r1"))
        self.assertFalse(probe(54_000, 0, at=1300, prior=later, run="r2")["warned"])

    def test_resending_the_same_prefix_blames_the_provider(self):
        first = sent(user("question"))
        warm = probe(50_000, 45_000, request=first)
        # A later round extends the request it follows...
        grown = sent(user("question"), {"role": "assistant", "content": "answer"}, user("more"))
        event = watch.miss(warm, probe(52_000, 128, at=1100, prior=warm, request=grown))
        self.assertEqual(event["reason"], "provider")
        # ...and a retry resends a prefix of it.
        retry = probe(30_000, 0, at=1100, prior=probe(50_000, 45_000, request=grown), request=first)
        self.assertEqual(retry["changed"], "")
        self.assertEqual(watch.miss(probe(50_000, 45_000, request=grown), retry)["reason"], "provider")

    def test_an_edit_that_explains_the_loss_names_the_history(self):
        long = "x" * 4000
        before = sent(user("first " + long), {"role": "assistant", "content": long}, user("second " + long))
        warm = probe(50_000, 45_000, request=before)
        # The first user message was edited: only the system prompt survives.
        early = sent(user("FIRST " + long), {"role": "assistant", "content": long}, user("second " + long))
        current = probe(50_000, 2_000, at=1100, prior=warm, request=early)
        self.assertEqual(current["changed"], "history")
        self.assertLess(current["shared"], 4_000)
        self.assertEqual(watch.miss(warm, current)["reason"], "history")
        # Only the newest message changed: the provider should still have
        # served the rest, so a cold request is the provider's miss.
        late = sent(user("first " + long), {"role": "assistant", "content": long}, user("SECOND " + long))
        cold = probe(50_000, 128, at=1100, prior=warm, request=late)
        self.assertEqual(cold["changed"], "history")
        self.assertEqual(watch.miss(warm, cold)["reason"], "provider")

    def test_tools_or_system_changes_are_settings(self):
        warm = probe(50_000, 45_000, request=sent(user("q")))
        for request in (sent(user("q"), tools=[]),
                        watch.fingerprint([{"role": "system", "content": "New rules."}, user("q")], TOOLS, "medium")):
            current = probe(50_000, 0, at=1100, prior=warm, request=request)
            self.assertEqual(current["changed"], "settings")
            self.assertEqual(watch.miss(warm, current)["reason"], "settings")

    def test_an_effort_change_is_named(self):
        # Thread 472: a wake turn sent no effort (Claude's default, low), the
        # user's next turn sent xhigh, and only tools + system stayed cached.
        warm = probe(50_000, 45_000, request=sent(user("q"), effort=None))
        current = probe(52_000, 20_000, at=1100, prior=warm, request=sent(user("q"), user("go"), effort="xhigh"))
        self.assertEqual(current["changed"], "settings")
        event = watch.miss(warm, current)
        self.assertEqual((event["reason"], event["effort_from"], event["effort_to"]), ("effort", "", "xhigh"))
        state = context.parse_state(context.dump_state({"cache": current}))
        self.assertEqual(state["cache"]["effort"], "xhigh")
        # A refreshed system prompt outranks the effort.
        self.assertEqual(watch.miss(warm, current, settings_changed=True)["reason"], "settings")
        # A probe stored before efforts were recorded cannot name one.
        legacy = {k: v for k, v in warm.items() if k != "effort"}
        self.assertIsNone(watch.parse(legacy)["effort"])
        self.assertEqual(watch.miss(legacy, current)["reason"], "settings")

    def test_probes_without_a_fingerprint_keep_an_unexplained_reason(self):
        self.assertEqual(watch.miss(self.warm, probe(52_000, 0, at=1100, prior=self.warm))["reason"], "")

    def test_images_weigh_like_text_when_estimating_the_shared_prefix(self):
        image = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200_000}}]}
        weights = watch.fingerprint([SYSTEM, image])["weights"]
        self.assertLess(weights[2], 5_000)

    def test_public_view_hides_the_account(self):
        view = watch.public(self.warm)
        self.assertEqual(view, {"at": 1000.0, "prompt": 50_000, "model": "m", "ttl": watch.DEFAULT_TTL_S, "hit": True, "effort": None})
        # The effort the cache was built at, so the picker can warn before a change.
        warm = probe(50_000, 45_000, request=sent(user("q"), effort="xhigh"))
        self.assertEqual(watch.public(warm)["effort"], "xhigh")

    def test_probe_survives_compaction_state_and_a_checkpoint_clears_it(self):
        state = context.parse_state(context.dump_state({"cache": self.warm}))
        self.assertEqual(state["cache"], self.warm)
        self.assertIsNone(context.parse_state({"cache": {"at": "nope"}})["cache"])
        self.assertIsNone(context.append_checkpoint(state, {"id": "c", "summary": "s"})["cache"])


if __name__ == "__main__":
    unittest.main()
