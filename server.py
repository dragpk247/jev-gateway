#!/usr/bin/env python3
"""
Jev Gateway: OpenAI-Compatible HTTP API Server
Endpoint: /v1/chat/completions, /v1/models, /health
Drop-in replacement for any client expecting OpenAI format (Cursor, Aider, Continue, LangChain)
Zero third-party dependencies required (built entirely on Python stdlib http.server)
"""

import os
import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Import core routing logic from main
import sys
sys.path.insert(0, str(Path(__file__).parent))
from main import (
    query_jev_classifier,
    check_privacy_sandbox,
    find_relevant_notes,
    write_back_to_obsidian,
    execute,
    OPENROUTER_MODELS
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

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
            self._send_json(200, {"status": "ok", "service": "jev-gateway", "version": "1.0.0"})
        elif self.path in ("/v1/models", "/models"):
            # Expose supported virtual models
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

            # Extract user prompt (last user message)
            prompt = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    prompt = msg.get("content", "")
                    break

            if not prompt:
                prompt = messages[-1].get("content", "")

            requested_model = payload.get("model", "auto")

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
                routing_note = f"Explicit user override: {requested_model}"
            else:
                # Classify via Jev System One
                info = query_jev_classifier(prompt)
                target = info.get("decision", "local_gpu")
                conf = info.get("confidence", 1.0) * 100
                latency = info.get("latency_ms", 0.0)
                routing_note = f"Jev System One -> {target} ({conf:.0f}% conf in {latency:.1f}ms)"

            print(f"[HTTP Request] Target: {target} | {routing_note}")

            # 4. Execute Query
            t_start = time.time()
            response_text = execute(target, prompt, context)
            duration_s = time.time() - t_start

            # 5. Auto Bi-Directional Write-back to Obsidian for novel cloud outputs
            if target != "local_gpu" and response_text and not response_text.startswith("OpenRouter"):
                write_back_to_obsidian(prompt, response_text, target)

            # 6. Format Standard OpenAI Response
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
                    "prompt_tokens": len(prompt.split()),
                    "completion_tokens": len(response_text.split()),
                    "total_tokens": len(prompt.split()) + len(response_text.split())
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
