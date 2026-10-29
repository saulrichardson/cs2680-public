"""SDK-free provider configuration shared by host and container."""
import os

ROUTES = {
    "course": ("https://api.cs2680.com/v1", "CS2680_API_KEY", "qwen3.6-35b-a3b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "qwen/qwen3.6-35b-a3b"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-6.1-sol"),
}


def route(name=None, model=None, reasoning=None):
    name = name or os.environ.get("MADS_PROVIDER", "course")
    if name not in ROUTES:
        raise ValueError("MADS_PROVIDER must be course, openrouter, or openai")
    url, key, default_model = ROUTES[name]
    # Optional non-secret proxy/relay endpoint; keys still come from key_env.
    # Provider identity, protocol and default model are not changed by routing.
    url = os.environ.get("MADS_BASE_URL") or url
    model = model or os.environ.get("MADS_MODEL") or default_model
    reasoning = reasoning or os.environ.get("MADS_REASONING")
    config = {"provider": name, "base_url": url, "key_env": key, "model": model}
    if name == 'openai':
        config['protocol'] = 'responses'
        config['reasoning'] = reasoning or 'medium'
    elif reasoning:
        raise ValueError('Explicit reasoning effort is only supported by the Responses adapter')
    if name == "openrouter" and os.environ.get("OPENROUTER_PROVIDER"):
        config["upstream"] = os.environ["OPENROUTER_PROVIDER"]
    return config
