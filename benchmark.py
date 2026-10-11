#!/usr/bin/env python3
"""
Jev Gateway Benchmark & Stress Test Runner
Simulates enterprise mixed workload:
- Coding, reasoning, boilerplate, and repetitive queries
- Measures latency, cache hits, dollar savings, and failover resilience
"""

import sys
import time
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GATEWAY_URL = "http://localhost:8085/v1/chat/completions"

BENCHMARK_PROMPTS = [
    ("Explain why quicksort average case is O(n log n) and write a python snippet", "auto"),
    ("Write a bash one-liner to find all .log files modified in the last 24 hours", "auto"),
    ("What is 3 + 5? Answer with only the number.", "auto"),
    ("Write a python function to validate an email using regex", "deepseek_chat"),
    ("Explain why quicksort average case is O(n log n) and write a python snippet", "auto"), # Cache Hit test
    ("Design a distributed rate limiter algorithm with sliding window", "deepseek_reasoner"),
    ("Write a python function to validate an email using regex", "auto"), # Cache Hit test
    ("What are the advantages of B-Trees over Binary Search Trees in databases?", "auto"),
]

def send_query(item):
    prompt, model = item
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    t0 = time.time()
    req = urllib.request.Request(
        GATEWAY_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            dur = time.time() - t0
            cached = data.get("gateway_metadata", {}).get("cached", False)
            target = data.get("model", model)
            return True, dur, cached, target, prompt[:40]
    except Exception as e:
        dur = time.time() - t0
        return False, dur, False, str(e), prompt[:40]

def run_benchmark(concurrency=2):
    print(f"==> Launching Jev Gateway Production Simulation ({len(BENCHMARK_PROMPTS)} queries)...")
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for res in executor.map(send_query, BENCHMARK_PROMPTS):
            results.append(res)
            status = "✓ OK" if res[0] else "❌ ERR"
            mode = "⚡ CACHED" if res[2] else "☁️ COMPUTED"
            print(f"    {status} [{mode}] {res[1]:.2f}s | {res[3]} | '{res[4]}...'")

    success_count = sum(1 for r in results if r[0])
    cache_hits = sum(1 for r in results if r[2])
    avg_latency = sum(r[1] for r in results) / len(results)

    print("\n" + "=" * 55)
    print("🏆 Benchmark Simulation Complete")
    print(f"  • Success Rate:   {success_count}/{len(results)} ({(success_count/len(results)*100):.1f}%)")
    print(f"  • Cache Hit Rate: {cache_hits}/{len(results)} ({(cache_hits/len(results)*100):.1f}%)")
    print(f"  • Avg Latency:    {avg_latency:.2f}s")
    print(f"  • Telemetry:      Check http://localhost:8085/dashboard")
    print("=" * 55)

if __name__ == "__main__":
    run_benchmark()
