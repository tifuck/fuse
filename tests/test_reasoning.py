import unittest

from app.web import _opencode_model_config, _reasoning_default, _reasoning_efforts


class OpenCodeReasoningConfigTest(unittest.TestCase):
    def test_grok_has_only_native_efforts(self):
        config = _opencode_model_config("Grok", "grok")
        self.assertTrue(config["reasoning"])
        self.assertEqual(config["interleaved"], {"field": "reasoning_content"})
        self.assertEqual(list(config["variants"]), ["low", "medium", "high", "xhigh"])

    def test_zai_exposes_binary_thinking(self):
        config = _opencode_model_config("GLM", "zai")
        self.assertEqual(list(config["variants"]), ["none", "high"])

    def test_openai_compatible_exposes_full_effort_range(self):
        config = _opencode_model_config("Model", "openai")
        self.assertEqual(
            list(config["variants"]),
            ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        )

    def test_openrouter_round_trips_its_native_reasoning_field(self):
        config = _opencode_model_config("OpenRouter model", "openrouter")
        self.assertEqual(config["interleaved"], {"field": "reasoning"})

    def test_defaults_prefer_medium_when_available(self):
        self.assertEqual(_reasoning_default("grok"), "medium")
        self.assertEqual(_reasoning_default("chatgpt"), "medium")
        self.assertEqual(_reasoning_default("zai"), "high")
        self.assertEqual(_reasoning_efforts("zai"), ("none", "high"))

    def test_devin_uses_catalog_efforts(self):
        from app.sources import devin as devin_mod

        devin_mod.catalog_ids_from_records(
            [
                {"id": "claude-sonnet-5-medium", "name": "Claude Sonnet 5 Medium"},
                {"id": "claude-sonnet-5-high", "name": "Claude Sonnet 5 High"},
            ]
        )
        self.assertEqual(_reasoning_efforts("devin", "devin/claude-sonnet-5"), ("medium", "high"))
        self.assertEqual(_reasoning_default("devin", "devin/claude-sonnet-5"), "medium")
        config = _opencode_model_config("Claude Sonnet 5", "devin", "devin/claude-sonnet-5")
        self.assertEqual(list(config["variants"]), ["medium", "high"])

    def test_devin_defaults_to_high_when_medium_is_absent(self):
        from app.sources import devin as devin_mod

        devin_mod.catalog_ids_from_records(
            [
                {"id": "deepseek-v4-1-flash-high", "name": "DeepSeek V4.1 Flash High"},
                {"id": "deepseek-v4-1-flash-max", "name": "DeepSeek V4.1 Flash Max"},
                {"id": "glm-5-2", "name": "GLM-5.2 High"},
                {"id": "glm-5-2-max", "name": "GLM-5.2 Max"},
                {"id": "glm-5-2-none", "name": "GLM-5.2 No Thinking"},
            ]
        )
        self.assertEqual(_reasoning_efforts("devin", "devin/deepseek-v4-1-flash"), ("high", "max"))
        self.assertEqual(_reasoning_default("devin", "devin/deepseek-v4-1-flash"), "high")
        self.assertEqual(_reasoning_default("devin", "devin/glm-5-2"), "high")

    def test_devin_display_names_use_dotted_versions(self):
        from app.sources import devin as devin_mod
        from app.web.models import _humanize_model_id, _model_display_name, _standardize_display_name

        devin_mod.catalog_ids_from_records([])
        self.assertEqual(_humanize_model_id("devin/gpt-5-6-luna"), "GPT 5.6 Luna")
        self.assertEqual(_humanize_model_id("devin/gpt-6-astra"), "GPT 6 Astra")
        self.assertEqual(_humanize_model_id("claude-fable-5-1"), "Claude Fable 5.1")
        self.assertEqual(_humanize_model_id("swe-1-6-fast"), "SWE-1.6 Fast")
        self.assertEqual(_standardize_display_name("GPT-5.4"), "GPT 5.4")
        self.assertEqual(_standardize_display_name("GPT-6 Astra"), "GPT 6 Astra")
        self.assertEqual(_standardize_display_name("Grok-4.7"), "Grok 4.7")
        self.assertEqual(_standardize_display_name("Gpt 5 6 Luna"), "GPT 5.6 Luna")
        self.assertEqual(_standardize_display_name("GLM-5.2"), "GLM 5.2")
        self.assertEqual(_standardize_display_name("GPT-5.3-Codex"), "GPT 5.3 Codex")
        self.assertEqual(_model_display_name("devin/gpt-5-6-luna", "devin"), "GPT 5.6 Luna")
        self.assertEqual(_model_display_name("devin/gpt-6-astra", "devin"), _model_display_name("gpt-6-astra", "chatgpt"))
        self.assertEqual(_model_display_name("devin/gpt-6-sol", "devin"), _model_display_name("gpt-6-sol", "chatgpt"))
        self.assertEqual(_model_display_name("devin/grok-4.7", "devin"), _model_display_name("grok-4.7", "grok"))
        self.assertEqual(_model_display_name("devin/gpt-5-4", "devin"), "GPT 5.4")
        self.assertEqual(_model_display_name("gpt-6-sol", "chatgpt"), "GPT 6 Sol")
        self.assertEqual(_model_display_name("gpt-6-luna", "chatgpt"), "GPT 6 Luna")
        self.assertEqual(_model_display_name("grok-4.7", "grok"), "Grok 4.7")
        self.assertEqual(
            _model_display_name("openrouter/openai/gpt-6-sol", "openrouter", {"by_id": {"openai/gpt-6-sol": {"name": "OpenAI: GPT-6 Sol"}}}),
            "GPT 6 Sol",
        )
        self.assertEqual(
            _standardize_display_name("Z.ai: GLM-5.3 Flash"),
            "GLM 5.3 Flash",
        )

    def test_devin_opencode_config_is_one_model_with_variants(self):
        from app.sources import devin as devin_mod

        names, _labels = devin_mod.catalog_ids_from_records(
            [
                {"id": "MODEL_GPT_5_2_LOW", "name": "GPT-5.2 Low Thinking"},
                {"id": "MODEL_GPT_5_2_HIGH", "name": "GPT-5.2 High Thinking"},
                {"id": "deepseek-v4-1-flash-high", "name": "DeepSeek V4.1 Flash High"},
                {"id": "deepseek-v4-1-flash-max", "name": "DeepSeek V4.1 Flash Max"},
            ]
        )
        self.assertIn("devin/gpt-5-2", names)
        self.assertIn("devin/deepseek-v4-1-flash", names)
        self.assertNotIn("devin/MODEL_GPT_5_2_LOW", names)
        self.assertNotIn("devin/deepseek-v4-1-flash-high", names)
        gpt = _opencode_model_config("GPT 5.2", "devin", "devin/gpt-5-2")
        self.assertEqual(list(gpt["variants"]), ["low", "high"])
        ds = _opencode_model_config("DeepSeek V4.1 Flash", "devin", "devin/deepseek-v4-1-flash")
        self.assertEqual(list(ds["variants"]), ["high", "max"])


if __name__ == "__main__":
    unittest.main()
