#!/usr/bin/env python3
"""
Jev Gateway Diagnostic Engine & Bug Finder
Performs end-to-end health audits, detects timeouts, dead hosts, missing models, and API bottlenecks.
"""

import os
import sys
import time
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

# Color styling for CLI
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PROJECT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_DIR))

import main

class JevDoctor:
    def __init__(self, verbose=True):
        self.verbose = verbose
        self.results = []
        self.bugs_found = []

    def log(self, category: str, name: str, status: str, details: str = "", fix: str = ""):
        color = GREEN if status == "PASS" else (YELLOW if status == "WARN" else RED)
        icon = "✓" if status == "PASS" else ("⚠" if status == "WARN" else "✗")
        self.results.append({
            "category": category,
            "name": name,
            "status": status,
            "details": details,
            "fix": fix
        })
        if status in ("WARN", "FAIL"):
            self.bugs_found.append({
                "name": f"[{category}] {name}",
                "status": status,
                "details": details,
                "fix": fix
            })
        if self.verbose:
            print(f"  {color}{icon} [{status}]{RESET} {BOLD}{name}{RESET}: {details}")
            if fix and status != "PASS":
                print(f"      {CYAN}Fix Recommendation:{RESET} {fix}")

    def check_ollama_hosts(self):
        if self.verbose:
            print(f"\n{BOLD}1. Auditing Local GPU & Ollama Endpoints...{RESET}")
        
        hosts = [
            ("Loopback Interface (Fastest)", "http://127.0.0.1:11434"),
            ("Localhost Interface", "http://localhost:11434"),
            ("Tailscale Host", main.OLLAMA_HOST)
        ]

        active_hosts = []
        for label, host in hosts:
            t0 = time.time()
            try:
                url = f"{host}/api/tags"
                req = urllib.request.Request(url, headers={"User-Agent": "jev-doctor/1.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    dur = (time.time() - t0) * 1000
                    models = [m.get("name", "") for m in data.get("models", [])]
                    has_qwen = any("qwen2.5-coder:32b" in m for m in models)
                    active_hosts.append((host, models))
                    detail = f"Responsive in {dur:.1f}ms ({len(models)} models available"
                    if has_qwen:
                        detail += ", qwen2.5-coder:32b installed)"
                    else:
                        detail += ", qwen2.5-coder:32b missing)"
                    self.log("Ollama", f"{label} ({host})", "PASS" if has_qwen else "WARN", detail,
                             fix="" if has_qwen else "Run `ollama pull qwen2.5-coder:32b` or configure 14b fallback")
            except Exception as e:
                dur = (time.time() - t0) * 1000
                self.log("Ollama", f"{label} ({host})", "FAIL", f"Connection failed after {dur:.1f}ms: {e}",
                         fix="Verify Ollama daemon is running with `ollama serve` or update OLLAMA_HOST in .env")

        return active_hosts

    def check_openrouter(self):
        if self.verbose:
            print(f"\n{BOLD}2. Auditing OpenRouter API Key & Provider Connectivity...{RESET}")

        key = main.OPENROUTER_API_KEY
        if not key:
            self.log("OpenRouter", "API Key Present", "FAIL", "Missing OPENROUTER_API_KEY",
                     fix="Save key to ~/.config/jev/openrouter_key or set OPENROUTER_API_KEY environment variable")
            return

        self.log("OpenRouter", "API Key Format", "PASS", f"Active key prefix: {key[:8]}...")

        # Test streaming ping on cheap tier
        t0 = time.time()
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": "deepseek/deepseek-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/dragpk247/jev-gateway",
                "X-Title": "Jev Doctor"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                first_line = resp.readline().decode("utf-8")
                dur = (time.time() - t0) * 1000
                if "data: " in first_line:
                    self.log("OpenRouter", "Streaming Connectivity", "PASS", f"TTFT response in {dur:.1f}ms")
                else:
                    self.log("OpenRouter", "Streaming Connectivity", "WARN", f"Unexpected response header: {first_line[:60]}")
        except Exception as e:
            dur = (time.time() - t0) * 1000
            self.log("OpenRouter", "Streaming Connectivity", "FAIL", f"Failed after {dur:.1f}ms: {e}",
                     fix="Check OpenRouter balance or network firewall outbound to openrouter.ai")

    def check_typesafe_classifier(self):
        if self.verbose:
            print(f"\n{BOLD}3. Auditing TypeSafe AI Jev System One Classifier...{RESET}")

        key = main.JEV_API_KEY
        if not key:
            self.log("Classifier", "JEV_API_KEY Present", "WARN", "Missing JEV_API_KEY; defaulting to local heuristic",
                     fix="Save key to ~/.config/jev/api_key or set JEV_API_KEY environment variable")
            return

        t0 = time.time()
        try:
            info = main.query_jev_classifier("Write a python function to compute fibonacci numbers")
            dur = (time.time() - t0) * 1000
            if "error" in info:
                self.log("Classifier", "Classification API", "WARN", f"Returned error: {info['error']}",
                         fix="Verify TypeSafe AI balance or token quota")
            else:
                self.log("Classifier", "Classification API", "PASS",
                         f"Decision: '{info.get('decision')}' ({info.get('confidence', 0)*100:.0f}% conf in {dur:.1f}ms)")
        except Exception as e:
            self.log("Classifier", "Classification API", "FAIL", f"Error: {e}", fix="Check https://api.typesafe.ai status")

    def check_streaming_sandbox_engine(self):
        if self.verbose:
            print(f"\n{BOLD}4. Auditing Real-Time SSE Streaming Sandbox Execution...{RESET}")

        # Test local GPU streaming directly through execute_stream
        t0 = time.time()
        tokens = []
        try:
            for token in main.execute_stream("local_gpu", "Count to 3: 1, 2, 3", ""):
                tokens.append(token)
                if len(tokens) >= 5:
                    break
            dur = (time.time() - t0) * 1000
            res = "".join(tokens)
            if res.startswith("\n[Stream Error:"):
                self.log("Streaming Sandbox", "Local GPU Streaming", "FAIL", f"Stream Error: {res.strip()}",
                         fix="Ensure local Ollama fallback loop exists in execute_stream without hard-binding to dead IP")
            else:
                self.log("Streaming Sandbox", "Local GPU Streaming", "PASS", f"Streamed tokens in {dur:.1f}ms")
        except Exception as e:
            self.log("Streaming Sandbox", "Local GPU Streaming", "FAIL", f"Exception: {e}",
                     fix="Wrap execute_stream in resilient multi-host loop and failover chain")

    def check_privacy_sandbox(self):
        if self.verbose:
            print(f"\n{BOLD}5. Auditing Zero-Leak Privacy Sandbox Guardrail...{RESET}")

        sensitive_prompt = "Connect to postgresql://admin:secret_pass_123@internal.db/production"
        is_sensitive, reason = main.check_privacy_sandbox(sensitive_prompt)
        if is_sensitive:
            self.log("Privacy Sandbox", "Secret Credential Interception", "PASS", f"Triggered: {reason}")
        else:
            self.log("Privacy Sandbox", "Secret Credential Interception", "FAIL", "Failed to detect database password",
                     fix="Add regex pattern to PRIVACY_PATTERNS in main.py")

        safe_prompt = "Explain quicksort average case"
        is_safe, _ = main.check_privacy_sandbox(safe_prompt)
        if not is_safe:
            self.log("Privacy Sandbox", "Safe Query Non-Interference", "PASS", "Safe prompt allowed through")
        else:
            self.log("Privacy Sandbox", "Safe Query Non-Interference", "WARN", "False positive on benign query")

    def check_databases(self):
        if self.verbose:
            print(f"\n{BOLD}6. Auditing Semantic Cache & Stats SQLite Databases...{RESET}")

        # Check Cache DB
        try:
            conn = sqlite3.connect(main.CACHE_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM semantic_cache")
            count = c.fetchone()[0]
            conn.close()
            self.log("Storage", "Semantic Cache DB", "PASS", f"Integrity nominal ({count} cached entries)")
        except Exception as e:
            self.log("Storage", "Semantic Cache DB", "FAIL", f"Database error: {e}",
                     fix="Run main.init_dbs() or recreate ~/.local/share/obsidian-ai-router/semantic_cache.db")

        # Check Stats DB
        try:
            conn = sqlite3.connect(main.STATS_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM request_logs")
            count = c.fetchone()[0]
            conn.close()
            self.log("Storage", "Gateway Stats DB", "PASS", f"Integrity nominal ({count} logged invocations)")
        except Exception as e:
            self.log("Storage", "Gateway Stats DB", "FAIL", f"Database error: {e}",
                     fix="Run main.init_dbs() or recreate ~/.local/share/obsidian-ai-router/gateway_stats.db")

    def check_gateway_server(self):
        if self.verbose:
            print(f"\n{BOLD}7. Auditing Gateway Daemon & HTTP Endpoints...{RESET}")

        port = int(os.getenv("PORT", "8085"))
        url = f"http://localhost:{port}/health"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.log("Gateway Daemon", "Health Endpoint", "PASS", f"Status: {data.get('status')} (v{data.get('version')})")
        except Exception as e:
            self.log("Gateway Daemon", "Health Endpoint", "WARN", f"Daemon offline on port {port}: {e}",
                     fix="Start daemon with `./jev start`")

    def run_all(self):
        if self.verbose:
            print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
            print(f"{BOLD}{CYAN}        ⚡ JEV GATEWAY PRODUCTION DIAGNOSTIC & BUG FINDER        {RESET}")
            print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")

        self.check_ollama_hosts()
        self.check_openrouter()
        self.check_typesafe_classifier()
        self.check_streaming_sandbox_engine()
        self.check_privacy_sandbox()
        self.check_databases()
        self.check_gateway_server()

        if self.verbose:
            print(f"\n{BOLD}═════════════════════════════════════════════════════════════════{RESET}")
            if not self.bugs_found:
                print(f"{GREEN}{BOLD}🎉 ALL SYSTEMS NOMINAL: No critical bugs or network deadlocks found!{RESET}")
            else:
                print(f"{RED}{BOLD}🚨 {len(self.bugs_found)} ISSUE(S) DETECTED:{RESET}")
                for b in self.bugs_found:
                    print(f"  • {BOLD}{b['name']}{RESET}: {b['details']}")
                    if b['fix']:
                        print(f"    {CYAN}↳ Recommended Fix:{RESET} {b['fix']}")
            print(f"{BOLD}═════════════════════════════════════════════════════════════════{RESET}\n")

        return {
            "total_checks": len(self.results),
            "bugs_count": len(self.bugs_found),
            "bugs": self.bugs_found,
            "results": self.results
        }

if __name__ == "__main__":
    doctor = JevDoctor(verbose=True)
    report = doctor.run_all()
    sys.exit(0 if report["bugs_count"] == 0 else 1)
