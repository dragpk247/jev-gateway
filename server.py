#!/usr/bin/env python3
"""
Jev Gateway: Production OpenAI-Compatible HTTP API Server
Features:
- /v1/chat/completions (standard JSON & Server-Sent Events SSE streaming)
- FastPath Semantic Response Cache (<3ms & $0.00)
- Zero-Leak Privacy Sandbox Guardrail
- Real-Time Web Telemetry Dashboard (/dashboard)
- Zero third-party dependencies (pure Python standard library)
"""

import os
import json
import time
import uuid
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Import core engine from main
import sys
sys.path.insert(0, str(Path(__file__).parent))
from main import (
    query_jev_classifier,
    check_privacy_sandbox,
    find_relevant_notes,
    write_back_to_obsidian,
    check_semantic_cache,
    store_semantic_cache,
    record_stat,
    execute,
    execute_stream,
    STATS_DB_PATH,
    CACHE_DB_PATH
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Jev Gateway - Real-Time Telemetry</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --accent: #58a6ff;
      --success: #3fb950;
      --purple: #bc8cff;
      --warning: #d29922;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 24px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 24px;
    }
    h1 { margin: 0; font-size: 22px; color: #fff; }
    .badge {
      background: rgba(88, 166, 255, 0.15);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .card-title { font-size: 13px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
    .card-value { font-size: 28px; font-weight: 700; color: #fff; margin-top: 8px; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      font-size: 14px;
    }
    th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
    th { background: #21262d; color: #8b949e; font-weight: 600; }
    tr:last-child td { border-bottom: none; }
    .tag {
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 600;
    }
    .tag-local { background: rgba(63, 185, 80, 0.15); color: var(--success); }
    .tag-deepseek { background: rgba(188, 140, 255, 0.15); color: var(--purple); }
    .tag-gemini { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
    .tag-cache { background: rgba(210, 153, 34, 0.15); color: var(--warning); }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>⚡ Jev Gateway Telemetry</h1>
      <p style="margin: 4px 0 0 0; color: #8b949e; font-size: 13px;">Sub-300ms Deterministic LLM Routing & Privacy Proxy</p>
    </div>
    <span class="badge" id="status-badge">● Live Active (Port 8080)</span>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-title">Total Requests</div>
      <div class="card-value" id="val-requests">--</div>
    </div>
    <div class="card">
      <div class="card-title">Estimated Savings ($)</div>
      <div class="card-value" style="color: var(--success);" id="val-savings">--</div>
    </div>
    <div class="card">
      <div class="card-title">FastPath Cache Hits</div>
      <div class="card-value" style="color: var(--warning);" id="val-cache">--</div>
    </div>
    <div class="card">
      <div class="card-title">Privacy Interceptions</div>
      <div class="card-value" style="color: var(--purple);" id="val-privacy">--</div>
    </div>
  </div>

  <h2>Recent Requests</h2>
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Prompt</th>
        <th>Target</th>
        <th>Mode</th>
        <th>Tokens</th>
        <th>Latency</th>
      </tr>
    </thead>
    <tbody id="logs-body">
      <tr><td colspan="6" style="text-align:center; color:#8b949e;">Loading telemetry...</td></tr>
    </tbody>
  </table>

  <script>
    async function loadStats() {
      try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        document.getElementById('val-requests').innerText = data.total_requests;
        document.getElementById('val-savings').innerText = '$' + (data.total_savings_cents / 100).toFixed(2);
        document.getElementById('val-cache').innerText = data.cache_hits;
        document.getElementById('val-privacy').innerText = data.privacy_locks;

        const tbody = document.getElementById('logs-body');
        if (data.recent_logs.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#8b949e;">No requests recorded yet.</td></tr>';
          return;
        }

        tbody.innerHTML = data.recent_logs.map(log => {
          let targetTag = `<span class="tag tag-local">${log.target}</span>`;
          if (log.target.includes('deepseek')) targetTag = `<span class="tag tag-deepseek">${log.target}</span>`;
          else if (log.target.includes('gemini')) targetTag = `<span class="tag tag-gemini">${log.target}</span>`;

          const modeTag = log.cached ? '<span class="tag tag-cache">⚡ Cached</span>' : 
                          log.privacy_locked ? '<span class="tag" style="background:#f8514922;color:#f85149;">🔒 Private</span>' : 'Standard';

          return `
            <tr>
              <td>${log.time.split(' ')[1] || log.time}</td>
              <td style="max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${log.prompt}</td>
              <td>${targetTag}</td>
              <td>${modeTag}</td>
              <td>${log.tokens_in + log.tokens_out}</td>
              <td>${log.duration_s}s</td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }
    loadStats();
    setInterval(loadStats, 3000);
  </script>
</body>
</html>
"""

class JevGatewayHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "jev-gateway", "version": "1.1.0"})
        elif self.path in ("/dashboard", "/"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/stats":
            conn = sqlite3.connect(STATS_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*), COALESCE(SUM(saved_cents), 0), COALESCE(SUM(cached), 0), COALESCE(SUM(privacy_locked), 0) FROM request_logs")
            total_req, total_saved, cache_hits, priv_locks = c.fetchone()
            
            c.execute("SELECT timestamp, prompt, target, cached, privacy_locked, tokens_in, tokens_out, duration_s FROM request_logs ORDER BY id DESC LIMIT 15")
            rows = c.fetchall()
            conn.close()

            recent = [
                {
                    "time": r[0],
                    "prompt": r[1],
                    "target": r[2],
                    "cached": bool(r[3]),
                    "privacy_locked": bool(r[4]),
                    "tokens_in": r[5],
                    "tokens_out": r[6],
                    "duration_s": r[7]
                }
                for r in rows
            ]
            self._send_json(200, {
                "total_requests": total_req,
                "total_savings_cents": round(total_saved, 1),
                "cache_hits": cache_hits,
                "privacy_locks": priv_locks,
                "recent_logs": recent
            })
        elif self.path in ("/v1/models", "/models"):
            model_data = {
                "object": "list",
                "data": [
                    {"id": "auto", "object": "model", "created": int(time.time()), "owned_by": "jev-gateway"},
                    {"id": "local_gpu", "object": "model", "created": int(time.time()), "owned_by": "ollama-qwen32b"},
                    {"id": "deepseek_reasoner", "object": "model", "created": int(time.time()), "owned_by": "openrouter-r1"},
                    {"id": "gemini_flash", "object": "model", "created": int(time.time()), "owned_by": "openrouter-gemini"},
                    {"id": "claude_sonnet", "object": "model", "created": int(time.time()), "owned_by": "openrouter-claude"},
                ]
            }
            self._send_json(200, model_data)
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_json(400, {"error": "Empty payload"})
                return

            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": f"Invalid JSON: {e}"})
                return

            messages = payload.get("messages", [])
            if not messages:
                self._send_json(400, {"error": "Missing 'messages' array"})
                return

            stream = payload.get("stream", False)

            # Extract user prompt
            prompt = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    prompt = msg.get("content", "")
                    break
            if not prompt:
                prompt = messages[-1].get("content", "")

            requested_model = payload.get("model", "auto")

            # 0. Check FastPath Semantic Cache
            cached = check_semantic_cache(prompt)
            if cached:
                cached_resp, cached_target = cached
                t_in = len(prompt.split())
                t_out = len(cached_resp.split())
                record_stat(prompt, cached_target, cached=True, privacy_locked=False, tokens_in=t_in, tokens_out=t_out, duration_s=0.01)

                if stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    chunk = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": f"jev-gateway:{cached_target}",
                        "choices": [{"index": 0, "delta": {"content": cached_resp}, "finish_reason": "stop"}]
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode("utf-8"))
                    return
                else:
                    response_payload = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": f"jev-gateway:{cached_target}",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": cached_resp}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": t_in, "completion_tokens": t_out, "total_tokens": t_in + t_out},
                        "gateway_metadata": {"routed_target": cached_target, "cached": True, "execution_latency_seconds": 0.01}
                    }
                    self._send_json(200, response_payload)
                    return

            # 1. RAG Context Lookup
            matches = find_relevant_notes(prompt, top_k=2)
            matched_notes = [m[1] for m in matches if m[0] > 0.62]
            context = "\n\n".join([f"--- Note: {m[1]} ---\n{m[3][:800]}" for m in matches if m[0] > 0.62])

            # 2. Zero-Leak Privacy Sandbox Guardrail
            is_sensitive, reason = check_privacy_sandbox(prompt, context)

            # 3. Router Target Resolution
            if is_sensitive:
                target = "local_gpu"
                routing_note = f"Hard-locked to Local GPU (Privacy Sandbox: {reason})"
            elif requested_model != "auto" and requested_model in ("local_gpu", "deepseek_reasoner", "gemini_flash", "claude_sonnet"):
                target = requested_model
                routing_note = f"Explicit override: {requested_model}"
            else:
                info = query_jev_classifier(prompt)
                target = info.get("decision", "local_gpu")
                conf = info.get("confidence", 1.0) * 100
                latency = info.get("latency_ms", 0.0)
                routing_note = f"Jev System One -> {target} ({conf:.0f}% conf in {latency:.1f}ms)"

            print(f"[HTTP] Target: {target} | {routing_note}")

            # 4. Handle Streaming Response (SSE)
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                full_text = []
                t0 = time.time()
                for token in execute_stream(target, prompt, context):
                    full_text.append(token)
                    chunk = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": f"jev-gateway:{target}",
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}]
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                # Send completion chunk
                done_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": f"jev-gateway:{target}",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                }
                self.wfile.write(f"data: {json.dumps(done_chunk)}\n\ndata: [DONE]\n\n".encode("utf-8"))
                self.wfile.flush()

                dur = time.time() - t0
                ans_str = "".join(full_text)
                record_stat(prompt, target, cached=False, privacy_locked=is_sensitive, tokens_in=len(prompt.split()), tokens_out=len(ans_str.split()), duration_s=dur)

                # Obsidian Auto writeback
                if target != "local_gpu" and ans_str and not ans_str.startswith("OpenRouter"):
                    write_back_to_obsidian(prompt, ans_str, target)
                return

            # 5. Standard Non-Streaming JSON Execution
            t_start = time.time()
            response_text = execute(target, prompt, context)
            duration_s = time.time() - t_start

            t_in = len(prompt.split())
            t_out = len(response_text.split())
            record_stat(prompt, target, cached=False, privacy_locked=is_sensitive, tokens_in=t_in, tokens_out=t_out, duration_s=duration_s)

            if target != "local_gpu" and response_text and not response_text.startswith("OpenRouter"):
                write_back_to_obsidian(prompt, response_text, target)

            response_payload = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": f"jev-gateway:{target}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": response_text
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": t_in,
                    "completion_tokens": t_out,
                    "total_tokens": t_in + t_out
                },
                "gateway_metadata": {
                    "routed_target": target,
                    "routing_details": routing_note,
                    "privacy_locked": is_sensitive,
                    "knowledge_injected": matched_notes,
                    "execution_latency_seconds": round(duration_s, 2)
                }
            }
            self._send_json(200, response_payload)
        else:
            self._send_json(404, {"error": "Endpoint not found"})

def run_server():
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, JevGatewayHandler)
    print("=" * 65)
    print(f"🚀 Jev Gateway OpenAI API Server running on http://{HOST}:{PORT}")
    print(f"   - Web Dashboard:   http://{HOST}:{PORT}/dashboard")
    print(f"   - Chat endpoint:   http://{HOST}:{PORT}/v1/chat/completions")
    print(f"   - Models endpoint: http://{HOST}:{PORT}/v1/models")
    print(f"   - Health check:    http://{HOST}:{PORT}/health")
    print("=" * 65)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
