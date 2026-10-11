# Jev Gateway - Agent Guidelines & Development Rules

## 1. Embedded Frontend Strings & JavaScript in Python (`server.py`)
- **String Escaping in Python Literals:**
  When writing inline JavaScript inside Python multi-line triple-quoted strings (e.g. `DASHBOARD_HTML = """..."""`), always double-escape backslashes for string sequences like `\n` (`\\n`), `\r` (`\\r`), `\t` (`\\t`), or regex backslashes. Single backslashes (`\n`) will be evaluated as literal newlines by Python, generating multi-line string syntax errors in the browser's JavaScript engine (e.g., `buffer.split('\n')` -> `buffer.split('\n')` broken across two lines).
- **Syntax Validation:**
  Whenever modifying inline HTML/JavaScript embedded inside Python files, extract and validate the script using Node.js syntax checks (`node -c`) before testing or deploying.

## 2. Dashboard UI & Tab Navigation Reliability
- **Independent Elements & Fallbacks:**
  Tab buttons must maintain explicit `id` attributes (`btn-<tab_name>`) and fallback selectors so tab switching never relies on fragile DOM event cascades.
- **Fail-Safe Exception Handling:**
  Tab navigation functions (`switchTab`) must be isolated from API data fetches so network errors or pending telemetry requests never block tab transitions.
- **Client Cache-Busting:**
  Serve dashboard HTML responses with explicit cache-invalidation headers:
  ```python
  self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
  self.send_header("Pragma", "no-cache")
  self.send_header("Expires", "0")
  ```

## 3. Server Architecture & Concurrency
- `server.py` uses `ThreadingHTTPServer` (`httpd.daemon_threads = True`) to prevent single-threaded head-of-line blocking on concurrent client requests.
- Vercel deployments use the serverless handler pattern in `api/index.py` with rewrite routing in `vercel.json`.
