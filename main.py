#!/usr/bin/env python3
"""
Jev Gateway: Core Router & Execution Engine
"""

import os
import sys
import json
import math
import time
import sqlite3
import urllib.request
from pathlib import Path

# Paths & Defaults
DEFAULT_VAULT_DIR = Path.home() / "Documents" / "Obsidian Vault"
DB_PATH = Path.home() / ".local" / "share" / "obsidian-ai-router" / "vault_index.db"
KEY_FILE = Path.home() / ".config" / "jev" / "api_key"

JEV_API_KEY = os.getenv("JEV_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Local / Tailscale Ollama Host
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://100.86.159.86:11434")

# OpenRouter Models
OPENROUTER_MODELS = {
    "claude_sonnet": "anthropic/claude-sonnet-4.6",
    "claude_opus_thinking": "anthropic/claude-opus-4.6",
    "gemini_flash": "google/gemini-2.5-flash"
}

def get_embedding(text: str, host: str = OLLAMA_HOST) -> list:
    url = f"{host}/api/embeddings"
    req_data = json.dumps({"model": "nomic-embed-text", "prompt": text[:2000]}).encode('utf-8')
    req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("embedding", [])
    except Exception:
        if host != "http://localhost:11434":
            return get_embedding(text, host="http://localhost:11434")
        return []

def find_relevant_notes(query: str, top_k: int = 2) -> list:
    q_emb = get_embedding(query)
    if not q_emb or not DB_PATH.exists():
        return []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT path, title, content, embedding FROM notes")
    rows = cursor.fetchall()
    conn.close()

    def cosine_similarity(v1, v2):
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        return dot / (norm1 * norm2) if norm1 and norm2 else 0.0

    results = []
    for path, title, content, emb_json in rows:
        if not emb_json:
            continue
        emb = json.loads(emb_json)
        sim = cosine_similarity(q_emb, emb)
        results.append((sim, title, path, content))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]

def query_jev_classifier(prompt: str) -> dict:
    payload = {
        "model": "jev-latest",
        "state": prompt,
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Determine the optimal compute target and model for this query.",
                "criteria": {
                    "local_gpu": "Routine coding, bash scripts, syntax fixes, unit tests, local dotfiles, or tasks suited for local GPU (RTX 5070 Ti / Qwen 32B).",
                    "claude_sonnet": "Complex architectural system design, intricate refactoring, full-stack application code, or strict instructions.",
                    "claude_opus_thinking": "Mathematical proofs, formal algorithmic reasoning, research logic, or hard edge cases.",
                    "gemini_flash": "Massive context window (>50k tokens), whole repo audits, document processing, or multimodal analysis."
                }
            }
        }
    }

    req = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {JEV_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "jev-gateway/1.0"
        }
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            latency_ms = (time.time() - t0) * 1000
            data = json.loads(resp.read().decode("utf-8"))
            ans = data.get("answers", {}).get("route", {})
            return {
                "decision": ans.get("choice", "local_gpu"),
                "confidence": ans.get("confidence", 1.0),
                "latency_ms": latency_ms
            }
    except Exception as e:
        return {"decision": "local_gpu", "confidence": 0.5, "latency_ms": 0, "error": str(e)}

def execute(target: str, prompt: str, context: str = "") -> str:
    system_prompt = "You are an expert AI assistant."
    if context:
        system_prompt += f"\n\n[Obsidian Context from User's Knowledge Base]:\n{context}"

    if target == "local_gpu":
        print("⚡ Executing on [Local GPU via Ollama: qwen2.5-coder:32b] ($0.00)...")
        url = f"{OLLAMA_HOST}/api/generate"
        payload = {
            "model": "qwen2.5-coder:32b",
            "prompt": f"{system_prompt}\n\nUser: {prompt}\n\nAssistant:",
            "stream": False
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("response", "")
        except Exception as e:
            return f"Ollama execution error: {e}"

    else:
        model_slug = OPENROUTER_MODELS.get(target, "anthropic/claude-sonnet-4.6")
        print(f" Executing on [Cloud via OpenRouter: {model_slug}]...")
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": model_slug,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/dragpk247/jev-gateway",
                "X-Title": "Jev Gateway"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"OpenRouter execution error: {e}"

def route(prompt: str, execute_now: bool = False):
    print(f"\nEvaluating: '{prompt}'")
    info = query_jev_classifier(prompt)
    target = info["decision"]
    conf = info["confidence"] * 100
    latency = info["latency_ms"]

    matches = find_relevant_notes(prompt, top_k=2)
    matched_notes = [m[1] for m in matches if m[0] > 0.62]
    context = "\n\n".join([f"--- Note: {m[1]} ---\n{m[3][:800]}" for m in matches if m[0] > 0.62])

    print(f" Route: {target} (Confidence: {conf:.0f}%, Jev Latency: {latency:.1f}ms)")
    print(f" Knowledge Match: {matched_notes or 'None'}")

    if execute_now:
        ans = execute(target, prompt, context)
        print("\n" + "=" * 50 + " RESPONSE " + "=" * 50)
        print(ans)
        print("=" * 110 + "\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec_flag = "--exec" in sys.argv
        args = [a for a in sys.argv[1:] if a != "--exec"]
        route(" ".join(args), execute_now=exec_flag)
    else:
        print("Usage: python3 main.py [--exec] 'your prompt'")
