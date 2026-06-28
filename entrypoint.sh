#!/bin/bash
set -e

mkdir -p /app/data

python - <<'PY'
import httpx
import os
import sys
import time

ollama_url = os.environ.get("OLLAMA_URL", "http://ollama:11434")
llm_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
embed_model = os.environ.get("EMBED_MODEL", "bge-m3")

print(f"Waiting for Ollama at {ollama_url}...")
for _ in range(90):
    try:
        httpx.get(f"{ollama_url}/api/tags", timeout=3).raise_for_status()
        print("Ollama is ready.")
        break
    except Exception:
        time.sleep(2)
else:
    print("Ollama did not start in time.", file=sys.stderr)
    sys.exit(1)


def ensure_model(name: str) -> None:
    tags = httpx.get(f"{ollama_url}/api/tags", timeout=10).json().get("models", [])
    names = [m.get("name", "") for m in tags]
    base = name.split(":")[0]
    if any(base in n for n in names):
        print(f"Model {name} already available.")
        return
    print(f"Pulling model {name} (may take several minutes on first run)...")
    httpx.post(
        f"{ollama_url}/api/pull",
        json={"name": name, "stream": False},
        timeout=3600,
    ).raise_for_status()
    print(f"Model {name} ready.")


try:
    ensure_model(llm_model)
    ensure_model(embed_model)
except Exception as exc:
    print(f"Warning: could not verify/pull model: {exc}", file=sys.stderr)
PY

if [ "$#" -gt 0 ]; then
    exec "$@"
else
    exec python bot.py
fi
