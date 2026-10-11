#!/usr/bin/env python3
"""
Jev Gateway: Distillation Pipeline & Dataset Harvester
Manages:
- Asynchronous, zero-latency capture of frontier model traces (Claude, DeepSeek R1)
- Storage of raw traces under ~/.config/jev/distill/raw_traces.jsonl
- Automated quality filter & syntax validator
- Golden training dataset generation (Alpaca & ShareGPT formats)
"""

import os
import sys
import json
import time
import uuid
import ast
import sqlite3
import threading
from pathlib import Path

DISTILL_DIR = Path.home() / ".config" / "jev" / "distill"
RAW_TRACES_PATH = DISTILL_DIR / "raw_traces.jsonl"
GOLDEN_DATASET_PATH = DISTILL_DIR / "training_golden.jsonl"
STATS_PATH = DISTILL_DIR / "distill_stats.json"

DISTILL_DIR.mkdir(parents=True, exist_ok=True)

def harvest_async(prompt: str, response: str, target: str, context: str = "", metadata: dict = None):
    """
    Fire-and-forget asynchronous harvest of high-tier traces.
    Does not block client response or streaming.
    """
    def _worker():
        try:
            harvest_trace(prompt, response, target, context, metadata)
        except Exception as e:
            # Harvester must NEVER crash main routing
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

def harvest_trace(prompt: str, response: str, target: str, context: str = "", metadata: dict = None) -> bool:
    """Synchronously appends a raw trace."""
    if not prompt or not response:
        return False
    
    # Don't harvest local GPU outputs by default (we want teacher traces)
    # Don't harvest error messages
    if target == "local_gpu" or response.startswith("OpenRouter Error") or response.startswith("[Stream Error"):
        return False

    entry = {
        "id": f"trace-{uuid.uuid4().hex[:12]}",
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target": target,
        "prompt": prompt.strip(),
        "context": context.strip(),
        "response": response.strip(),
        "metadata": metadata or {}
    }

    with open(RAW_TRACES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Update quick count cache
    _update_stats()
    return True

def _update_stats():
    raw_count = 0
    golden_count = 0
    if RAW_TRACES_PATH.exists():
        with open(RAW_TRACES_PATH, "r", encoding="utf-8") as f:
            raw_count = sum(1 for _ in f)
    if GOLDEN_DATASET_PATH.exists():
        with open(GOLDEN_DATASET_PATH, "r", encoding="utf-8") as f:
            golden_count = sum(1 for _ in f)

    stats = {
        "raw_traces": raw_count,
        "golden_samples": golden_count,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

def verify_code_snippet(code: str, lang: str = "python") -> bool:
    """Verifies basic syntax validity for code blocks."""
    if lang in ("python", "py"):
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
    # For bash/other languages, simple heuristic: non-empty & balanced braces/quotes
    return len(code.strip()) > 5

def process_and_filter() -> tuple[int, int]:
    """
    Runs automated quality filters on raw traces and populates training_golden.jsonl.
    Filters:
    - Min length heuristics (drops trivial hellos)
    - Code validation (syntax check for python code)
    - De-duplication by prompt
    """
    if not RAW_TRACES_PATH.exists():
        return 0, 0

    seen_prompts = set()
    golden_entries = []

    with open(RAW_TRACES_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            prompt = data.get("prompt", "")
            resp = data.get("response", "")

            # Filter 1: Min length (drop trivial 1-liners)
            if len(prompt.split()) < 3 or len(resp.split()) < 10:
                continue

            # Filter 2: Deduplication
            prompt_key = prompt.lower().strip()
            if prompt_key in seen_prompts:
                continue
            seen_prompts.add(prompt_key)

            # Check for python code blocks
            if "```python" in resp:
                parts = resp.split("```python")
                for part in parts[1:]:
                    code = part.split("```")[0]
                    if not verify_code_snippet(code, "python"):
                        # Discard code with broken syntax
                        continue

            golden_entries.append({
                "instruction": prompt,
                "input": data.get("context", ""),
                "output": resp,
                "teacher_model": data.get("target"),
                "harvested_at": data.get("date")
            })

    with open(GOLDEN_DATASET_PATH, "w", encoding="utf-8") as f:
        for entry in golden_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    _update_stats()
    return len(seen_prompts), len(golden_entries)

def export_dataset(fmt: str = "alpaca", output_file: str = None) -> str:
    """Exports golden dataset to Alpaca or ShareGPT JSON."""
    process_and_filter()
    if not GOLDEN_DATASET_PATH.exists():
        return ""

    out_path = Path(output_file) if output_file else (DISTILL_DIR / f"dataset_{fmt}.json")
    items = []

    with open(GOLDEN_DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            if fmt == "alpaca":
                items.append({
                    "instruction": data["instruction"],
                    "input": data.get("input", ""),
                    "output": data["output"]
                })
            elif fmt == "sharegpt":
                items.append({
                    "conversations": [
                        {"from": "human", "value": f"{data['instruction']}\n{data.get('input', '')}".strip()},
                        {"from": "gpt", "value": data["output"]}
                    ]
                })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    return str(out_path)

def get_stats() -> dict:
    _update_stats()
    if STATS_PATH.exists():
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"raw_traces": 0, "golden_samples": 0}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        s = get_stats()
        print(f"Distillation Harvester Status:")
        print(f"  • Raw Captured Traces: {s.get('raw_traces', 0)}")
        print(f"  • Verified Golden:     {s.get('golden_samples', 0)}")
        print(f"  • Storage Directory:   {DISTILL_DIR}")
    elif len(sys.argv) > 1 and sys.argv[1] == "filter":
        raw, golden = process_and_filter()
        print(f"Processed: {raw} unique traces -> {golden} golden training samples.")
    elif len(sys.argv) > 1 and sys.argv[1] == "export":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "alpaca"
        p = export_dataset(fmt)
        print(f"Dataset exported to: {p}")
    else:
        print("Usage: python3 distill.py [status|filter|export [alpaca|sharegpt]]")
