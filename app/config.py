from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_secret: str
    # Retained only while offline legacy migration tools are being retired.
    database_path: str = ""
    skills_directory: str = str(ROOT / "data" / "skills")
    # mysql://user:pass@127.0.0.1:3306/fuse — when set, this is the only database.
    database_url: str = ""
    database_mariadb_enabled: bool = True
    database_pool_size: int = 48
    host: str = "0.0.0.0"
    port: int = 8088

    allow_register: bool = False
    allow_private_hosts: bool = False
    allow_local_workspace: bool = False
    public_base_url: str = ""
    trusted_hosts: str = ""
    # Serve /static URLs that carry ?v= as immutable for a year. promote-dev.sh
    # raises ?v= for every changed file, so production is always fresh; dev
    # edits files without bumping, so its .env sets this false.
    static_immutable: bool = True
    max_body_bytes: int = 2_097_152
    chat_body_bytes: int = 8_388_608
    max_tokens: int = 16_384
    auth_rate_per_minute: int = 10
    api_rate_per_minute: int = 90
    web_search_queries_per_turn: int = 32
    web_read_successes_per_turn: int = 12
    web_read_attempts_per_turn: int = 18
    web_evidence_chars: int = 24_000
    # Explicit /title regenerations (in-memory sliding windows).
    chat_title_per_thread_per_hour: int = 10
    chat_title_per_user_per_hour: int = 60
    # Optional helper model; normal account access/billing still applies.
    chat_metadata_model: str = ""
    chat_metadata_timeout: int = 30  # Per attempt; capped at 30s for bounded leases.

    # Dashboard context meter + compaction. Ratios are of the model window.
    # Strategy is a name in app.context.STRATEGIES so a later summarizer can
    # replace the default without changing call sites.
    context_default_window: int = 128_000
    context_warn_ratio: float = 0.80
    context_auto_ratio: float = 0.90
    context_auto_compact: bool = True
    context_keep_tail: int = 6
    context_prune_protect: int = 40_000
    context_prune_minimum: int = 20_000
    context_output_reserve: int = 16_384
    context_strategy: str = "summarize"
    # Optional cheaper model id for compaction summaries (e.g. glm-5.3-flash).
    # Empty = summarize with the chat's own model. On failure the chat model
    # is retried once before falling back to the truncate strategy.
    context_summarizer_model: str = ""

    grok_client_id: str = "b1a00492-073a-47ea-816f-4c329264a828"
    # Bootstrap/fallback version; disable auto-refresh to pin this value.
    grok_client_version: str = "1.0.46"
    grok_client_version_auto_refresh: bool = True
    grok_proxy: str = "https://cli-chat-proxy.grok.com/v1"
    grok_token_url: str = "https://auth.x.ai/oauth2/token"
    grok_device_url: str = "https://auth.x.ai/oauth2/device/code"
    grok_scope: str = (
        "openid profile email offline_access grok-cli:access api:access "
        "conversations:read conversations:write workspaces:read workspaces:write"
    )

    openai_auth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    openai_auth_base: str = "https://auth.openai.com"
    openai_auth_originator: str = "opencode"
    openai_auth_user_agent: str = "opencode/1.18.25"
    openai_chatgpt_base: str = "https://chatgpt.com/backend-api/codex"
    # Codex hides a model from `/models` until client_version reaches the
    # model's minimal_client_version; a high value lists every new slug.
    openai_codex_client_version: str = "99.0.0"
    # SOCKS/HTTP proxy for ChatGPT OAuth only (device code, token, refresh).
    # Empty = direct. Use socks5h:// so DNS is resolved by the proxy.
    openai_auth_proxy: str = ""

    llama_base_url: str = ""
    llama_api_key: str = ""

    zai_coding_base: str = "https://api.z.ai/api/coding/paas/v4"
    zai_quota_url: str = "https://api.z.ai/api/monitor/usage/quota/limit"
    zai_cn_coding_base: str = "https://open.bigmodel.cn/api/coding/paas/v4"
    zai_cn_quota_url: str = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"

    # Kimi Code membership (not platform.kimi.ai PAYG). Device-code OAuth
    # plus console API keys share the same coding endpoint.
    kimi_client_id: str = "17e5f671-d194-4dfb-9706-5516cb48c098"
    kimi_oauth_host: str = "https://auth.kimi.ai"
    kimi_coding_base: str = "https://api.kimi.ai/coding/v1"
    kimi_cn_oauth_host: str = "https://auth.kimi.com"
    kimi_cn_coding_base: str = "https://api.kimi.com/coding/v1"
    kimi_platform: str = "fuse"
    kimi_client_version: str = "1.0"
    kimi_user_agent: str = "fuse/1.0"

    # Devin CLI inference (Connect-RPC). Not api.devin.ai sessions.
    # Chat identifies as Devin CLI / Local. GetCliModelConfigs only returns
    # the full Claude/GPT/Gemini catalog when the client is Windsurf.
    devin_api_server: str = "https://server.codeium.com"
    devin_webapp_url: str = "https://app.devin.ai"
    devin_cli_token_url: str = "https://api.devin.ai/auth/cli/token"
    devin_ide_name: str = "devin"
    devin_ide_version: str = "3000.10.21"
    devin_extension_name: str = "cli"
    devin_extension_version: str = "3000.10.21"
    devin_catalog_ide_name: str = "windsurf"
    devin_catalog_ide_version: str = "3.2.23"
    devin_catalog_extension_name: str = "windsurf"
    devin_catalog_extension_version: str = "1.48.2"

    # Xiaomi MiMo Token Plan. Browser login matches MiMo Code (X25519 authorize
    # on the platform). Chat is OpenAI-compat on the regional token-plan host.
    # A console key (tp- / ttp-) is the same credential without the browser step.
    mimo_platform_url: str = "https://platform.xiaomimimo.com"

    # Claude Code subscription OAuth. Same client and manual redirect the CLI
    # uses when the browser cannot reach localhost. Chat is the Messages API.
    claude_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    claude_authorize_url: str = "https://claude.ai/oauth/authorize"
    claude_console_authorize_url: str = "https://platform.claude.com/oauth/authorize"
    claude_token_url: str = "https://platform.claude.com/v1/oauth/token"
    claude_manual_redirect_url: str = "https://platform.claude.com/oauth/code/callback"
    claude_api_base: str = "https://api.anthropic.com"

    brave_search_api_key: str = ""

    # Bug-report triage pipeline. Disabled by default; reports queue in the
    # bug_reports table until ISSUES_PIPELINE_ENABLED=1. The triage model is
    # resolved through the site's own sources (llama / OpenAI-compat / site
    # OpenRouter key) — never the pooled Grok/ChatGPT/Z.AI/Devin subscriptions.
    issues_pipeline_enabled: bool = False
    issues_pipeline_model: str = ""
    issues_pipeline_batch: int = 2
    issues_pipeline_max_attempts: int = 5

    # Optional catalog/install credentials. SKILLS_SH_TOKEN must be a
    # non-expiring skills.sh credential — a Vercel OIDC JWT expires ~12h
    # and is not a valid .env secret. GITHUB_TOKEN raises the process-wide
    # GitHub install cap from 20 to 200 HTTP calls/hour.
    skills_sh_token: str = ""
    github_token: str = ""

    @field_validator("app_secret")
    @classmethod
    def _require_secret(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or v == "change-me" or len(v) < 32:
            raise ValueError("APP_SECRET must be a random string of at least 32 characters")
        return v


settings = Settings()
