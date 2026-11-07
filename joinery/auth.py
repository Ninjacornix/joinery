"""How to sign each supported agent in: subscription or API key.

Joinery does not broker credentials. Each agent authenticates with its own
vendor, by a flat-rate subscription (sign in) or metered API key. This module
is the reference map Joinery shows the user so they can run any agent, and
switch between the paid paths, without hunting through vendor docs. Facts are a
snapshot from `CHECKED_ON`; auth terms change, so treat this as a pointer, not a
guarantee.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

CHECKED_ON = date(2026, 11, 7)


class AuthPath(BaseModel):
    """One way to authenticate an agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str  # "subscription" or "api-key"
    # The command or step that activates this path.
    how: str
    # For api-key: the env var or config key the agent reads the key from.
    key_var: str = ""
    # Where to obtain a key / manage the plan.
    get_at: str = ""
    notes: str = ""


class AgentAuth(BaseModel):
    """The auth paths available for one agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    subscription: AuthPath
    api_key: AuthPath


AUTH: dict[str, AgentAuth] = {
    "claude": AgentAuth(
        agent_id="claude",
        subscription=AuthPath(
            kind="subscription",
            how="run `claude` and sign in with a Claude Pro/Max plan",
            get_at="https://www.anthropic.com/pricing",
        ),
        api_key=AuthPath(
            kind="api-key",
            how="set the key in the environment before launching Claude Code",
            key_var="ANTHROPIC_API_KEY",
            get_at="https://console.anthropic.com/settings/keys",
            notes="Metered Anthropic API billing, separate from a Pro/Max plan.",
        ),
    ),
    "codex": AgentAuth(
        agent_id="codex",
        subscription=AuthPath(
            kind="subscription",
            how="run `codex login` and finish the browser step to use a ChatGPT plan",
            get_at="https://chatgpt.com/#pricing",
            notes="Included in ChatGPT Plus/Pro/Business/Edu/Enterprise.",
        ),
        api_key=AuthPath(
            kind="api-key",
            how="run `codex login --api-key <key>`, or set the env var",
            key_var="OPENAI_API_KEY",
            get_at="https://platform.openai.com/api-keys",
            notes="Metered API billing. Some ChatGPT-workspace features are unavailable.",
        ),
    ),
    "gemini": AgentAuth(
        agent_id="gemini",
        subscription=AuthPath(
            kind="subscription",
            how="run `gemini` and sign in with a Google account for Gemini Code Assist",
            get_at="https://codeassist.google/",
        ),
        api_key=AuthPath(
            kind="api-key",
            how="set the key in the environment before launching Gemini CLI",
            key_var="GEMINI_API_KEY",
            get_at="https://aistudio.google.com/apikey",
            notes="Metered Google AI billing.",
        ),
    ),
    "opencode": AgentAuth(
        agent_id="opencode",
        subscription=AuthPath(
            kind="subscription",
            how="run `opencode auth login` and pick a provider",
            get_at="https://opencode.ai/docs/providers",
            notes=(
                "Subscription use depends on the provider's terms and often needs an "
                "auth plugin; Anthropic blocked consumer-OAuth subscriptions in Jan 2026. "
                "API key is the reliable path."
            ),
        ),
        api_key=AuthPath(
            kind="api-key",
            how="run `opencode auth login` and paste a provider API key",
            key_var="(per provider, e.g. ANTHROPIC_API_KEY / OPENAI_API_KEY)",
            get_at="https://opencode.ai/docs/providers",
            notes="opencode is model-agnostic; the key belongs to whichever provider you choose.",
        ),
    ),
}


def for_agent(agent_id: str) -> AgentAuth:
    try:
        return AUTH[agent_id]
    except KeyError:
        raise KeyError(f"no auth info for agent {agent_id!r}") from None
