"""Thin wrapper around the local Ollama server (http://localhost:11434)."""
import json
import time
import urllib.error
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:8b"
RETRIES = 5


def chat_json(system, user, seed=None, temperature=0.7, model=MODEL):
    """Send one prompt and return (parsed reply, raw reply). The parsed reply is None if it isn't valid JSON."""
    options = {"temperature": temperature, "num_ctx": 8192}
    if seed is not None:
        options["seed"] = seed
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": "json",
        "stream": False,
        "think": False,
        "options": options,
    }
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    # Ollama's model process occasionally crashes or is still loading; that passes, so try a few times.
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                raw = json.loads(response.read())["message"]["content"]
            break
        except urllib.error.URLError as error:
            if attempt == RETRIES - 1:
                raise RuntimeError(f"Cannot reach the language model at {OLLAMA_URL} ({error.reason}). "
                                   f"Start Ollama and make sure the model '{model}' is installed.") from None
            time.sleep(10)
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError:
        return None, raw
