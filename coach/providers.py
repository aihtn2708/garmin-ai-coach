"""Model providers: one small adapter per wire format.

Gemini, OpenRouter, DeepSeek and Ollama all speak the OpenAI chat-completions
format; Anthropic has its own. Model lists are fetched live from each
provider, so no model name in this file can go stale.

The ``data_use`` notes are shown in the UI next to the provider choice. They
summarise each provider's published terms as read in November 2026; the links
are there so the user can check the current wording.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .net import Egress, HttpError

PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini",
        "format": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "needs_key": True,
        "key_page": "https://aistudio.google.com/apikey",
        "cost": "Free tier on Flash and Flash-Lite models, with daily request limits.",
        "data_use": (
            "On the free tier, Google may use your prompts and the replies to improve its "
            "products, and human reviewers may read them. Turning on billing for the "
            "project stops that. Free-tier terms differ in the EEA, UK and Switzerland."
        ),
        "terms": "https://ai.google.dev/gemini-api/terms",
        "caution": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "format": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "needs_key": True,
        "key_page": "https://openrouter.ai/keys",
        "cost": "Free models: 50 requests a day, or 1,000 a day once you have bought $10 of credit.",
        "data_use": (
            "OpenRouter passes your prompt to whichever company hosts the model. Several "
            "free models are hosted by companies whose terms allow training on prompts. "
            "Keep the option below switched on to refuse those hosts."
        ),
        "terms": "https://openrouter.ai/docs/guides/privacy/provider-logging",
        "caution": True,
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "needs_key": True,
        "key_page": "https://platform.claude.com",
        "cost": "Paid. Claude Haiku is the lowest-priced model; a typical question costs a fraction of a cent.",
        "data_use": (
            "Paid API. Check Anthropic's commercial terms for how API inputs are retained "
            "and used."
        ),
        "terms": "https://www.anthropic.com/legal/commercial-terms",
        "caution": False,
    },
    "deepseek": {
        "label": "DeepSeek",
        "format": "openai",
        "base_url": "https://api.deepseek.com",
        "needs_key": True,
        "key_page": "https://platform.deepseek.com/api_keys",
        "cost": "Paid, very low per-token prices; half price outside peak hours.",
        "data_use": (
            "Paid API run by a company in China; prompts are processed there under its "
            "privacy policy. OpenRouter's provider table was reported in October 2026 to "
            "mark DeepSeek as a host that may train on prompts. Read the policy before "
            "sending health data."
        ),
        "terms": "https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html",
        "caution": True,
    },
    "ollama": {
        "label": "Local model (Ollama)",
        "format": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "needs_key": False,
        "key_page": "https://ollama.com/download",
        "cost": "Free. Runs on your own computer; needs a model that supports tools.",
        "data_use": "Nothing is sent to an AI company. Prompts stay on this computer.",
        "terms": "",
        "caution": False,
        "local": True,
    },
}

_GEMINI_SKIP = re.compile(
    r"embedding|image|tts|audio|live|robotics|veo|imagen|aqa|computer-use|learnlm|gemma|nano|lyria",
    re.IGNORECASE,
)
_RETRY_STATUS = {429, 500, 502, 503, 529}


class ProviderError(Exception):
    pass


def public_info() -> dict[str, Any]:
    return {
        name: {k: v for k, v in info.items() if k not in ("format", "base_url")}
        | {"host": info["base_url"].split("/")[2]}
        for name, info in PROVIDERS.items()
    }


def _auth_headers(provider: str, key: str) -> dict[str, str]:
    if PROVIDERS[provider]["format"] == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"} if key else {}


def _friendly(provider: str, exc: HttpError) -> ProviderError:
    label = PROVIDERS[provider]["label"]
    detail = exc.message()
    if exc.status in (401, 403):
        return ProviderError(f"{label} rejected the API key. Check it and try again. ({detail})")
    if exc.status == 402:
        return ProviderError(f"{label} says this account has no credit for that model. ({detail})")
    if exc.status == 404:
        hint = ""
        if provider == "openrouter":
            hint = (
                " If the message mentions data policy or no endpoints, the only host for "
                "this free model may train on prompts: pick another model, or switch off "
                "the training block if you accept that."
            )
        return ProviderError(f"{label} could not find that model or endpoint. ({detail}){hint}")
    if exc.status == 429:
        return ProviderError(
            f"{label} rate limit reached. Free tiers allow only a few requests per minute "
            f"and per day. Wait a little, or pick another model. ({detail})"
        )
    return ProviderError(f"{label} returned an error (HTTP {exc.status}). ({detail})")


def list_models(provider: str, key: str, egress: Egress) -> list[dict[str, Any]]:
    info = PROVIDERS[provider]
    base = info["base_url"]
    egress.allow(base, "local-model" if info.get("local") else "model")
    headers = _auth_headers(provider, key)
    try:
        if provider == "anthropic":
            data = egress.get_json(f"{base}/v1/models?limit=100", headers=headers, timeout=30, note="List models")
            models = [
                {"id": m["id"], "label": m.get("display_name") or m["id"]}
                for m in data.get("data", [])
            ]
            for m in models:
                m["recommended"] = "haiku" in m["id"]
            return _rank(models)
        if provider == "openrouter":
            # /key verifies the key; /models is public and carries prices.
            egress.get_json(f"{base}/key", headers=headers, timeout=30, note="Check API key")
            data = egress.get_json(f"{base}/models", headers=headers, timeout=45, note="List models")
            models = []
            for m in data.get("data", []):
                if "tools" not in (m.get("supported_parameters") or []):
                    continue
                price = m.get("pricing") or {}
                free = _zero(price.get("prompt")) and _zero(price.get("completion"))
                models.append(
                    {
                        "id": m["id"],
                        "label": m.get("name") or m["id"],
                        "free": free,
                        "recommended": free,
                    }
                )
            return _rank(models)
        data = egress.get_json(f"{base}/models", headers=headers, timeout=30, note="List models")
    except HttpError as exc:
        raise _friendly(provider, exc) from None
    except OSError as exc:
        if info.get("local"):
            raise ProviderError(
                "Could not reach Ollama on this computer. Start it, pull a model that "
                "supports tools, then try again."
            ) from None
        raise ProviderError(f"Could not reach {info['label']}: {exc}") from None

    models = []
    for m in (data or {}).get("data", []):
        model_id = str(m.get("id", "")).removeprefix("models/")
        if not model_id:
            continue
        if provider == "gemini":
            if "gemini" not in model_id or _GEMINI_SKIP.search(model_id):
                continue
            free_hint = "flash" in model_id
            models.append(
                {
                    "id": model_id,
                    "label": model_id,
                    "free": free_hint or None,
                    "recommended": "flash" in model_id and "lite" not in model_id and "preview" not in model_id,
                }
            )
        else:
            models.append(
                {"id": model_id, "label": model_id, "recommended": "flash" in model_id or "chat" in model_id}
            )
    if provider == "gemini":
        models.sort(key=lambda m: _version(m["id"]), reverse=True)
    return _rank(models)


def _zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _version(model_id: str) -> tuple[float, int]:
    match = re.search(r"(\d+(?:\.\d+)?)", model_id)
    return (float(match.group(1)) if match else 0.0, -len(model_id))


def _rank(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recommended first, original order otherwise."""
    return sorted(models, key=lambda m: not m.get("recommended"))


# -- chat adapters ---------------------------------------------------------


class Reply:
    def __init__(self, text: str, calls: list[dict[str, Any]], raw: Any, usage: dict[str, int]) -> None:
        self.text = text
        self.calls = calls  # [{"id", "name", "args"}]
        self.raw = raw
        self.usage = usage  # {"in": int, "out": int}


class Chat:
    """One user turn: provider-native message list plus send/append helpers."""

    def __init__(
        self,
        provider: str,
        key: str,
        model: str,
        egress: Egress,
        system: str,
        history: list[tuple[str, str]],
        user_text: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.info = PROVIDERS[provider]
        self.key = key
        self.model = model
        self.egress = egress
        self.options = options or {}
        self.system = system
        self.anthropic = self.info["format"] == "anthropic"
        egress.allow(self.info["base_url"], "local-model" if self.info.get("local") else "model")
        self.messages: list[dict[str, Any]] = []
        if not self.anthropic:
            self.messages.append({"role": "system", "content": system})
        for question, answer in history:
            self.messages.append({"role": "user", "content": question})
            self.messages.append({"role": "assistant", "content": answer})
        self.messages.append({"role": "user", "content": user_text})

    def send(self, tools: list[dict[str, Any]], note: str = "", allow_calls: bool = True) -> Reply:
        body = self._anthropic_body(tools) if self.anthropic else self._openai_body(tools)
        if tools and not allow_calls:
            body["tool_choice"] = {"type": "none"} if self.anthropic else "none"
        url = self.info["base_url"] + ("/v1/messages" if self.anthropic else "/chat/completions")
        headers = _auth_headers(self.provider, self.key)
        data = None
        for attempt in range(3):
            try:
                data = self.egress.post_json(
                    url, body, headers=headers, timeout=180, note=note, keep_detail=True
                )
                break
            except HttpError as exc:
                if exc.status in _RETRY_STATUS and attempt < 2:
                    time.sleep(_retry_after(exc, attempt))
                    continue
                raise _friendly(self.provider, exc) from None
            except OSError as exc:
                raise ProviderError(f"Could not reach {self.info['label']}: {exc}") from None
        if not isinstance(data, dict):
            raise ProviderError(f"{self.info['label']} sent an empty reply.")
        if data.get("error"):
            err = data["error"]
            raise ProviderError(
                f"{self.info['label']}: {err.get('message', err) if isinstance(err, dict) else err}"
            )
        return self._parse_anthropic(data) if self.anthropic else self._parse_openai(data)

    def add_reply(self, reply: Reply) -> None:
        self.messages.append(reply.raw)

    def add_results(self, results: list[tuple[dict[str, Any], str]]) -> None:
        """results: [(call, content string)] for every call in the last reply."""
        if self.anthropic:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call["id"], "content": content}
                        for call, content in results
                    ],
                }
            )
            return
        for call, content in results:
            msg = {"role": "tool", "tool_call_id": call["id"], "content": content}
            if self.provider == "gemini":
                msg["name"] = call["name"]
            self.messages.append(msg)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    # -- OpenAI-compatible -------------------------------------------------------

    def _openai_body(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": self.messages, "max_tokens": 8000}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        if self.provider == "openrouter" and self.options.get("block_training", True):
            body["provider"] = {"data_collection": "deny"}
        return body

    def _parse_openai(self, data: dict[str, Any]) -> Reply:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.info['label']} returned no answer.")
        msg = choices[0].get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except ValueError:
                    args = {"_invalid_json": raw_args[:200]}
            else:
                args = raw_args or {}
            if not tc.get("id"):
                tc["id"] = f"call_{i}"
            calls.append({"id": tc["id"], "name": fn.get("name", ""), "args": args})
        # Echo the assistant message back verbatim (minus nulls): Gemini's
        # thought signatures and DeepSeek's reasoning_content must be returned
        # unchanged while a tool exchange is in progress.
        raw = {k: v for k, v in msg.items() if v is not None}
        raw["role"] = "assistant"
        if calls and not raw.get("content"):
            raw.pop("content", None)
        elif "content" not in raw:
            raw["content"] = ""
        usage = data.get("usage") or {}
        return Reply(
            _text(msg.get("content")),
            calls,
            raw,
            {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0)},
        )

    # -- Anthropic ----------------------------------------------------------------

    def _anthropic_body(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            "messages": self.messages,
        }
        if tools:
            body["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ]
        return body

    def _parse_anthropic(self, data: dict[str, Any]) -> Reply:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            {"id": b["id"], "name": b.get("name", ""), "args": b.get("input") or {}}
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}
        tokens_in = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        return Reply(
            text,
            calls,
            {"role": "assistant", "content": blocks},
            {"in": tokens_in, "out": int(usage.get("output_tokens") or 0)},
        )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _retry_after(exc: HttpError, attempt: int) -> float:
    try:
        return min(12.0, max(1.0, float(exc.headers.get("Retry-After", ""))))
    except (TypeError, ValueError, AttributeError):
        return (2.0, 6.0)[attempt]
