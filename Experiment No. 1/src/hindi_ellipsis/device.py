"""
Device selection: NVIDIA CUDA -> Apple Metal (MPS: Apple silicon OR AMD Radeon GPUs on Intel Macs) -> CPU.

Override with the environment variable HINDI_ELLIPSIS_DEVICE=cuda|mps|cpu or the CLI flag --device.
On MPS, PYTORCH_ENABLE_MPS_FALLBACK=1 is set so the few operations Metal lacks run on the CPU
instead of crashing.
"""
import os
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


def mps_available() -> bool:
    import torch
    return hasattr(torch.backends, "mps") and torch.backends.mps.is_available() and torch.backends.mps.is_built()


def get_device(preferred=None) -> str:
    import torch
    pref = (preferred or os.environ.get("HINDI_ELLIPSIS_DEVICE") or "auto").lower()
    if pref != "auto":
        if pref.startswith("cuda") and not torch.cuda.is_available():
            print("warning: CUDA requested but not available -> cpu")
            return "cpu"
        if pref == "mps" and not mps_available():
            print("warning: MPS requested but not available -> cpu")
            return "cpu"
        return pref
    if torch.cuda.is_available():
        return "cuda"
    if mps_available():
        return "mps"
    return "cpu"


def synchronize(device: str):
    import torch
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    elif device == "mps" and hasattr(torch, "mps"):
        torch.mps.synchronize()


def check_device(device=None, encoder=None, batch_size=8, seq_len=64, steps=5):
    """Compare a forward/backward pass on the chosen device against the CPU: correctness + speed."""
    import torch
    dev = get_device(device)
    print(f"torch {torch.__version__} | cuda={torch.cuda.is_available()} | mps={mps_available()} | selected: {dev}")
    if dev == "cpu":
        print("No GPU backend available; everything will run on the CPU.")
        return dev
    torch.manual_seed(0)
    if encoder:
        from transformers import AutoModel
        model_cpu = AutoModel.from_pretrained(encoder).eval()
    else:
        from transformers import BertConfig, BertModel
        model_cpu = BertModel(BertConfig(hidden_size=768, num_hidden_layers=6, num_attention_heads=12,
                                         intermediate_size=3072, vocab_size=30000)).eval()
    ids = torch.randint(5, 1000, (batch_size, seq_len))
    mask = torch.ones_like(ids)
    with torch.no_grad():
        ref = model_cpu(input_ids=ids, attention_mask=mask).last_hidden_state
    import copy
    model_dev = copy.deepcopy(model_cpu).to(dev)
    with torch.no_grad():
        out = model_dev(input_ids=ids.to(dev), attention_mask=mask.to(dev)).last_hidden_state.cpu()
    diff = (out - ref).abs().max().item()
    ok = diff < 1e-2
    print(f"max |{dev} - cpu| difference: {diff:.2e} -> {'OK' if ok else 'MISMATCH: do not use this device'}")

    def bench(model, d):
        model.train()
        opt = torch.optim.AdamW(model.parameters(), lr=1e-5)
        x, m = ids.to(d), mask.to(d)
        model(input_ids=x, attention_mask=m).last_hidden_state.mean().backward()      # warm-up
        opt.zero_grad(); synchronize(d)
        t0 = time.time()
        for _ in range(steps):
            model(input_ids=x, attention_mask=m).last_hidden_state.mean().backward()
            opt.step(); opt.zero_grad()
        synchronize(d)
        return (time.time() - t0) / steps

    t_cpu = bench(model_cpu, "cpu")
    t_dev = bench(model_dev, dev)
    print(f"training step (batch {batch_size} x {seq_len} tokens): cpu {t_cpu:.2f}s | {dev} {t_dev:.2f}s "
          f"-> {t_cpu / max(t_dev, 1e-9):.1f}x {'faster' if t_dev < t_cpu else 'slower'} on {dev}")
    if not ok or t_dev >= t_cpu:
        print(f"Recommendation: use --device cpu")
    else:
        print(f"Recommendation: use --device {dev} (the default 'auto' already picks it)")
    return dev
