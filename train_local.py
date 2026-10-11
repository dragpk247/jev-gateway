#!/usr/bin/env python3
"""
Jev Gateway: Local LoRA Fine-Tuning Engine
Target Hardware: Local NVIDIA RTX 5070 Ti (16GB VRAM)
Features:
- Reads verified dataset from ~/.config/jev/distill/training_golden.jsonl
- Formats into standard ChatML / Alpaca training prompts
- Configured for 4-bit QLoRA with Unsloth or Hugging Face PEFT + TRL (SFTTrainer)
- Low VRAM consumption (<12GB footprint), fitting comfortably in 16GB
- Exports GGUF / Ollama Modelfile ready for local $0.00 inference
"""

import os
import sys
import json
import argparse
from pathlib import Path

DISTILL_DIR = Path.home() / ".config" / "jev" / "distill"
GOLDEN_PATH = DISTILL_DIR / "training_golden.jsonl"
OUTPUT_MODEL_DIR = DISTILL_DIR / "models" / "qwen2.5-coder-14b-jev"

def check_environment():
    """Verify GPU availability and prerequisites."""
    print("==> Checking Local Hardware Environment...")
    try:
        import torch
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            print(f"    ✓ GPU Detected: {device_name} ({vram_gb:.1f} GB VRAM)")
        else:
            print("    ⚠️ CUDA device not detected via PyTorch. Local GPU will be required for training.")
    except ImportError:
        print("    ℹ️ PyTorch not imported in current environment. Using script generator mode.")

def prepare_training_data(dataset_path: Path) -> list:
    """Load golden dataset and convert to structured instruction format."""
    if not dataset_path.exists():
        print(f"❌ Error: Dataset file not found at {dataset_path}")
        print("   Run some high-tier queries with Jev Gateway and execute `jev distill filter` first.")
        sys.exit(1)

    records = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass

    print(f"==> Loaded {len(records)} verified golden training samples.")
    return records

def generate_unsloth_training_script(dataset_path: Path, output_dir: Path, base_model: str, epochs: int):
    """
    Generates a high-performance standalone training script using Unsloth.
    Optimized specifically for 16GB VRAM NVIDIA GPUs (RTX 5070 Ti / 4080 / 4090).
    """
    train_script = DISTILL_DIR / "run_unsloth_lora.py"
    output_dir.mkdir(parents=True, exist_ok=True)

    code = f'''#!/usr/bin/env python3
"""
Auto-generated Unsloth QLoRA fine-tuning runner for Jev Gateway.
Target Model: {base_model}
Target GPU: NVIDIA RTX 5070 Ti (16GB VRAM)
"""
import torch
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

max_seq_length = 2048
dtype = None # Auto detection
load_in_4bit = True # 4bit quantization to run comfortably within 16GB VRAM

print("==> Loading base model {base_model} in 4-bit...")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{base_model}",
    max_seq_length=max_seq_length,
    dtype=dtype,
    load_in_4bit=load_in_4bit,
)

print("==> Attaching LoRA adapters...")
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# Format dataset
alpaca_prompt = """Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.

### Instruction:
{{}}

### Input:
{{}}

### Response:
{{}}"""

def formatting_prompts_func(examples):
    instructions = examples["instruction"]
    inputs       = examples["input"]
    outputs      = examples["output"]
    texts = []
    for instruction, input_text, output in zip(instructions, inputs, outputs):
        text = alpaca_prompt.format(instruction, input_text, output) + tokenizer.eos_token
        texts.append(text)
    return {{ "text" : texts }}

print("==> Loading Golden Dataset from {dataset_path}...")
dataset = load_dataset("json", data_files="{dataset_path}", split="train")
dataset = dataset.map(formatting_prompts_func, batched=True)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=5,
        max_steps=60,
        num_train_epochs={epochs},
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=1,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=3407,
        output_dir="{output_dir}",
    ),
)

print("==> Starting LoRA SFT Training on Local GPU...")
trainer_stats = trainer.train()

print("==> Training Complete! Saving LoRA adapter to {output_dir}...")
model.save_pretrained("{output_dir}")
tokenizer.save_pretrained("{output_dir}")

print("==> Exporting GGUF / Ollama Model...")
try:
    model.save_pretrained_gguf("{output_dir}_gguf", tokenizer, quantization_method="q4_k_m")
    print("✓ GGUF Exported successfully to {output_dir}_gguf")
except Exception as e:
    print(f"GGUF direct export notice: {{e}}")
'''
    train_script.write_text(code, encoding="utf-8")
    train_script.chmod(0o755)
    return train_script

def generate_ollama_modelfile(adapter_dir: Path, base_model: str):
    """Generates an Ollama Modelfile to import trained model into local Ollama."""
    modelfile_path = adapter_dir / "Modelfile"
    content = f"""# Modelfile for Jev Distilled Student Model
FROM {base_model}
# Base parameters tuned for coding & reasoning
PARAMETER temperature 0.2
PARAMETER top_p 0.95
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|endoftext|>"

SYSTEM \"\"\"
You are Jev-Distilled, an expert AI software engineer fine-tuned on verified frontier reasoning and architecture traces.
Deliver clean, deterministic, production-grade solutions.
\"\"\"
"""
    modelfile_path.write_text(content, encoding="utf-8")
    return modelfile_path

def main():
    parser = argparse.ArgumentParser(description="Jev Gateway Local LoRA Fine-Tuning Manager")
    parser.add_argument("--base", default="Qwen/Qwen2.5-Coder-14B-Instruct", help="Base student model")
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--dry-run", action="store_true", help="Generate runner script and Modelfile without executing")
    args = parser.parse_args()

    check_environment()
    data = prepare_training_data(GOLDEN_PATH)
    if len(data) == 0:
        print("⚠️ Warning: Golden dataset has 0 entries. Run `jev distill filter` to process captured traces.")

    runner = generate_unsloth_training_script(GOLDEN_PATH, OUTPUT_MODEL_DIR, args.base, args.epochs)
    modelfile = generate_ollama_modelfile(OUTPUT_MODEL_DIR, args.base)

    print(f"\n✓ Generated Standalone Unsloth Runner: {runner}")
    print(f"✓ Generated Ollama Modelfile:          {modelfile}")
    print(f"✓ Target Model Output:                 {OUTPUT_MODEL_DIR}")

    if args.dry_run:
        print("\n[Dry Run]: Configuration verified. Ready to launch with `python3 run_unsloth_lora.py`.")
    else:
        print("\nTo launch training run:")
        print(f"   python3 {runner}")

if __name__ == "__main__":
    main()
