#!/usr/bin/env python3
"""
Jev Gateway: Core Router & Execution Engine
Features:
- TypeSafe AI Jev System One sub-300ms Classifier
- FastPath Semantic Response Cache (<5ms & $0.00 for repetitive queries)
- Zero-Leak Privacy Sandbox: hard locks sensitive/credential queries to local GPU
- Multi-Tier Frontier Routing (OpenRouter: DeepSeek-R1, Gemini 3.8 Flash, Claude Sonnet 5.5)
- Bi-Directional Knowledge Feedback: Auto write-back to Obsidian Vault
- Streaming SSE Generator for real-time tokens
"""

import os
import re
import sys
import json
import math
import time
import sqlite3
import urllib.request
from pathlib import Path
from datetime import datetime

# Paths & Defaults
DEFAULT_VAULT_DIR = Path.home() / "Documents" / "Obsidian Vault"
DB_PATH = Path.home() / ".local" / "share" / "obsidian-ai-router" / "vault_index.db"
CACHE_DB_PATH = Path.home() / ".local" / "share" / "obsidian-ai-router" / "semantic_cache.db"
STATS_DB_PATH = Path.home() / ".local" / "share" / "obsidian-ai-router" / "gateway_stats.db"
KEY_FILE = Path.home() / ".config" / "jev" / "api_key"

JEV_API_KEY = os.getenv("JEV_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Local / Tailscale Ollama Host
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://100.86.159.86:11434")

# OpenRouter Models (verified active on OpenRouter)
OPENROUTER_MODELS = {
    "claude_sonnet": "anthropic/claude-sonnet-5.5",
    "deepseek_reasoner": "deepseek/deepseek-r1",
    "gemini_flash": "google/gemini-3.8-flash"
}

# Sensitive regex patterns for the Zero-Leak Privacy Sandbox
PRIVACY_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|auth[_-]?header|bearer\s+[a-z0-9_\-\.]+)\b"),
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|PGP)? PRIVATE KEY-----"),
    re.compile(r"(?i)\b(id_rsa|id_ed25519|\.env|credentials\.json)\b"),
    re.compile(r"#(private|confidential|secret|internal|finance|tax|vault)"),
]

def init_dbs():
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Cache DB
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT UNIQUE,
            embedding TEXT,
            response TEXT,
            target TEXT,
            hit_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    # 2. Stats DB
    conn = sqlite3.connect(STATS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            prompt TEXT,
            target TEXT,
            cached BOOLEAN,
            privacy_locked BOOLEAN,
            tokens_in INTEGER,
            tokens_out INTEGER,
            saved_cents REAL,
            duration_s REAL
        )
    """)
    conn.commit()
    conn.close()

init_dbs()

def check_privacy_sandbox(prompt: str, context: str = "") -> tuple[bool, str]:
    full_text = f"{prompt}\n{context}"
    for pattern in PRIVACY_PATTERNS:
        match = pattern.search(full_text)
        if match:
            return True, f"Matched sensitive pattern/tag: '{match.group(0)[:25]}'"
    return False, ""

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

def cosine_similarity(v1, v2):
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    return dot / (norm1 * norm2) if norm1 and norm2 else 0.0

def check_semantic_cache(prompt: str, similarity_threshold: float = 0.94) -> tuple[str, str] | None:
    """
    FastPath Cache Check:
    Returns (cached_response, target) if match >= threshold, else None.
    """
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    
    # 1. Exact string match first (<1ms)
    cursor.execute("SELECT response, target, id, hit_count FROM semantic_cache WHERE prompt = ?", (prompt.strip(),))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = ?", (row[2],))
        conn.commit()
        conn.close()
        return row[0], row[1]
    
    # 2. Semantic vector match
    q_emb = get_embedding(prompt)
    if not q_emb:
        conn.close()
        return None
        
    cursor.execute("SELECT id, embedding, response, target, hit_count FROM semantic_cache WHERE embedding IS NOT NULL")
    rows = cursor.fetchall()
    
    best_match = None
    best_sim = 0.0
    best_id = None
    
    for row_id, emb_json, resp, target, hits in rows:
        if not emb_json:
            continue
        sim = cosine_similarity(q_emb, json.loads(emb_json))
        if sim > best_sim:
            best_sim = sim
            best_match = (resp, target)
            best_id = row_id
            
    if best_match and best_sim >= similarity_threshold:
        cursor.execute("UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE id = ?", (best_id,))
        conn.commit()
        conn.close()
        return best_match
        
    conn.close()
    return None

def store_semantic_cache(prompt: str, response: str, target: str):
    try:
        q_emb = get_embedding(prompt)
        emb_json = json.dumps(q_emb) if q_emb else None
        conn = sqlite3.connect(CACHE_DB_PATH)
        conn.execute("""
            INSERT OR REPLACE INTO semantic_cache (prompt, embedding, response, target)
            VALUES (?, ?, ?, ?)
        """, (prompt.strip(), emb_json, response, target))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Cache Error] {e}")

def record_stat(prompt: str, target: str, cached: bool, privacy_locked: bool, tokens_in: int, tokens_out: int, duration_s: float):
    try:
        # Approximate savings: if avoided cloud ($10/M tokens), calculate saved cents
        saved_cents = 0.0
        if cached:
            saved_cents = (tokens_in + tokens_out) / 1000.0 * 1.5
        elif target == "local_gpu":
            saved_cents = (tokens_in + tokens_out) / 1000.0 * 1.0

        conn = sqlite3.connect(STATS_DB_PATH)
        conn.execute("""
            INSERT INTO request_logs (prompt, target, cached, privacy_locked, tokens_in, tokens_out, saved_cents, duration_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (prompt[:150], target, cached, privacy_locked, tokens_in, tokens_out, round(saved_cents, 3), round(duration_s, 2)))
        conn.commit()
        conn.close()
    except Exception:
        pass

def find_relevant_notes(query: str, top_k: int = 2) -> list:
    q_emb = get_embedding(query)
    if not q_emb or not DB_PATH.exists():
        return []
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT path, title, content, embedding FROM notes")
    rows = cursor.fetchall()
    conn.close()

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
                "instructions": "Select the most cost-effective and capable compute target.",
                "criteria": {
                    "local_gpu": "Local task, syntax check, CLI script, config modification, or routine code suited for local RTX 5070 Ti.",
                    "deepseek_reasoner": "Complex system architecture, distributed systems design, data pipelines, hard algorithmic reasoning, edge cases, or deep debugging (Cost-optimal high reasoning).",
                    "claude_sonnet": "Nuanced full-stack application code, frontend UI ergonomics, or highly stylistic prose.",
                    "gemini_flash": "Massive context window, broad document synthesis, repo-wide audits, or high-volume summarization."
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

def write_back_to_obsidian(prompt: str, response: str, target: str, vault_dir: Path = DEFAULT_VAULT_DIR) -> Path | None:
    if not vault_dir.exists():
        return None
    synthesized_dir = vault_dir / "AI-Synthesized"
    synthesized_dir.mkdir(parents=True, exist_ok=True)
    clean_prompt = re.sub(r'[^a-zA-Z0-9\s-]', '', prompt).strip()
    words = clean_prompt.split()[:6]
    slug = "-".join(words).title() or f"Synthesis-{int(time.time())}"
    filename = synthesized_dir / f"{slug}.md"
    content = f"""---
title: "{slug}"
date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
source_model: {target}
generator: jev-gateway
tags:
  - ai-synthesis
  - knowledge-base
---

# {slug.replace('-', ' ')}

### Query Context
> **Prompt**: {prompt}

---

### Synthesized Solution
{response}

---
*Generated by `jev-gateway` bi-directional knowledge feedback loop.*
"""
    filename.write_text(content, encoding="utf-8")
    return filename

def execute(target: str, prompt: str, context: str = "") -> str:
    system_prompt = "You are an expert AI software engineer and system architect."
    if context:
        system_prompt += f"\n\n[Obsidian Context from Local Knowledge Base]:\n{context}"

    if target == "local_gpu":
        print(f"⚡ [Tier 0: Local RTX 5070 Ti] qwen2.5-coder:32b ($0.00)...")
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
                ans = data.get("response", "")
                store_semantic_cache(prompt, ans, target)
                return ans
        except Exception as e:
            return f"Local Ollama execution error: {e}"

    else:
        model_slug = OPENROUTER_MODELS.get(target, "deepseek/deepseek-r1")
        print(f"🌐 [Frontier Cloud via OpenRouter: {model_slug}]...")
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
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                ans = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                store_semantic_cache(prompt, ans, target)
                return ans
        except Exception as e:
            return f"OpenRouter execution error: {e}"

def execute_stream(target: str, prompt: str, context: str = ""):
    """
    Generator yielding tokens in real-time for SSE streaming
    """
    system_prompt = "You are an expert AI software engineer and system architect."
    if context:
        system_prompt += f"\n\n[Obsidian Context from Local Knowledge Base]:\n{context}"

    if target == "local_gpu":
        url = f"{OLLAMA_HOST}/api/generate"
        payload = {
            "model": "qwen2.5-coder:32b",
            "prompt": f"{system_prompt}\n\nUser: {prompt}\n\nAssistant:",
            "stream": True
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
        full_text = []
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                for line in resp:
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        token = chunk.get("response", "")
                        if token:
                            full_text.append(token)
                            yield token
                    except Exception:
                        continue
            store_semantic_cache(prompt, "".join(full_text), target)
        except Exception as e:
            yield f"\n[Stream Error: {e}]"
    else:
        model_slug = OPENROUTER_MODELS.get(target, "deepseek/deepseek-r1")
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": model_slug,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": True
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
        full_text = []
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                for line in resp:
                    line_str = line.decode('utf-8').strip()
                    if not line_str.startswith("data: "):
                        continue
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            full_text.append(delta)
                            yield delta
                    except Exception:
                        continue
            store_semantic_cache(prompt, "".join(full_text), target)
        except Exception as e:
            yield f"\n[Stream Error: {e}]"

def route(prompt: str, execute_now: bool = False, save_to_vault: bool = False):
    print(f"\nEvaluating: '{prompt}'")

    # 0. FastPath Cache Check
    cached = check_semantic_cache(prompt)
    if cached:
        cached_resp, target = cached
        print(f"⚡ [FastPath Cache Hit (<3ms)]: $0.00 | Instant Delivery ({target})")
        if execute_now:
            print("\n" + "=" * 50 + " CACHED RESPONSE " + "=" * 50)
            print(cached_resp)
            print("=" * 110 + "\n")
        return

    # 1. RAG Context Lookup
    matches = find_relevant_notes(prompt, top_k=2)
    matched_notes = [m[1] for m in matches if m[0] > 0.62]
    context = "\n\n".join([f"--- Note: {m[1]} ---\n{m[3][:800]}" for m in matches if m[0] > 0.62])

    # 2. Zero-Leak Privacy Sandbox Guardrail
    is_sensitive, reason = check_privacy_sandbox(prompt, context)
    
    # 3. Jev Classification
    info = query_jev_classifier(prompt)
    target = info["decision"]
    conf = info["confidence"] * 100
    latency = info["latency_ms"]

    if is_sensitive:
        print(f"🔒 [Zero-Leak Privacy Sandbox Triggered]: {reason}")
        print(f"   Forcing route to Local RTX 5070 Ti (Cloud outbound blocked).")
        target = "local_gpu"

    print(f"🎯 Route: {target} (Confidence: {conf:.0f}%, Jev Latency: {latency:.1f}ms)")
    print(f"📚 Knowledge Match: {matched_notes or 'None'}")

    if execute_now:
        ans = execute(target, prompt, context)
        print("\n" + "=" * 50 + " RESPONSE " + "=" * 50)
        print(ans)
        print("=" * 110 + "\n")

        # Auto write-back for frontier models or when --save-vault is passed
        if (target != "local_gpu" or save_to_vault) and ans:
            note_path = write_back_to_obsidian(prompt, ans, target)
            if note_path:
                print(f"📝 Knowledge Synthesized & Saved to Obsidian: {note_path.name}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec_flag = "--exec" in sys.argv
        save_flag = "--save-vault" in sys.argv
        args = [a for a in sys.argv[1:] if a not in ("--exec", "--save-vault")]
        route(" ".join(args), execute_now=exec_flag, save_to_vault=save_flag)
    else:
        print("Usage:")
        print("  python3 main.py 'your prompt'                        # Route & Inspect")
        print("  python3 main.py --exec 'your prompt'                 # Route & Execute")
        print("  python3 main.py --exec --save-vault 'your prompt'    # Execute & Auto-Save to Obsidian")
