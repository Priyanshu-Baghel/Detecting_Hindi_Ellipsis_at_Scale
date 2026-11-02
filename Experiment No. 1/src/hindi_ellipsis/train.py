"""
Training the neural ellipsis detector on silver data.

    from hindi_ellipsis.train import Trainer, TrainConfig
    Trainer(TrainConfig(data="silver", encoder="google/muril-base-cased", out="models/detector")).run()

Steps: fine-tune (soft-label BCE + gap tagging) -> per-type threshold tuning ->
per-type temperature calibration -> optional self-training rounds -> evaluation
(silver + manual gold) -> active-learning export (to_annotate.csv).
"""
import json
import math
import os
import random
import time
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

from .preprocessing import tokenize


@dataclass
class TrainConfig:
    data: str = "silver"
    encoder: str = "google/muril-base-cased"
    out: str = "models/detector"
    epochs: int = 4
    batch_size: int = 32
    grad_accum: int = 1
    lr: float = 3e-5
    head_lr: float = 1e-3
    warmup: float = 0.06
    dropout: float = 0.1
    att_dim: int = 256
    max_len: int = 128
    gap_lambda: float = 0.5
    gap_o_weight: float = 0.2
    max_pos_weight: float = 20.0
    min_pos_per_type: int = 2000
    patience: int = 2
    fp16: bool = False
    self_train_rounds: int = 0
    pseudo_pos: float = 0.95
    pseudo_neg: float = 0.02
    pseudo_max: int = 100000
    n_annotate: int = 500
    max_train: int = 0
    log_every: int = 200
    seed: int = 42
    device: str = "auto"          # auto | cuda | mps | cpu
    freeze_embeddings: bool = True  # saves ~1.8 GB on MuRIL (large vocabulary)


def prf(y, p):
    tp = int(((p == 1) & (y == 1)).sum()); fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    P, R = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return P, R, 2 * P * R / max(1e-9, P + R), tp + fn


def evaluate_probs(probs, rows, types, thresholds, use_gold=False):
    res, f1s = {}, []
    for ti, t in enumerate(types):
        if use_gold:
            idx = [i for i, r in enumerate(rows) if "gold" in r and t in r["gold"]]
            if not idx:
                continue
            y, pr = np.array([rows[i]["gold"][t] for i in idx]), probs[idx, ti]
        else:
            y, pr = np.array([int(r["probs"][ti] >= 0.5) for r in rows]), probs[:, ti]
        P, R, F, sup = prf(y, (pr >= thresholds[t]).astype(int))
        res[t] = {"precision": round(P, 4), "recall": round(R, 4), "f1": round(F, 4), "support": sup}
        if sup:
            f1s.append(F)
    res["_macro_f1"] = round(float(np.mean(f1s)) if f1s else 0.0, 4)
    return res


def gap_accuracy(G):
    tot = cor = gt = gc = 0
    for pred, gold in G:
        m = gold != -100
        tot += int(m.sum()); cor += int(((pred == gold) & m).sum())
        gm = m & (gold != 0)
        gt += int(gm.sum()); gc += int(((pred == gold) & gm).sum())
    return {"token_acc": round(cor / max(1, tot), 4), "gap_recall": round(gc / max(1, gt), 4), "n_gaps": gt}


def tune_thresholds(probs, rows, types, min_pos=10):
    th = {}
    for ti, t in enumerate(types):
        y = np.array([int(r["probs"][ti] >= 0.5) for r in rows])
        if y.sum() < min_pos:
            th[t] = 0.5
            continue
        best, bt = -1, 0.5
        for c in np.arange(0.3, 0.91, 0.05):
            F = prf(y, (probs[:, ti] >= c).astype(int))[2]
            if F > best:
                best, bt = F, float(round(c, 2))
        th[t] = bt
    return th


def fit_temperatures(logits, rows, types, min_pos=20):
    """Per-type temperature scaling (Guo et al. 2017) against the soft labels -> calibrated confidences.
    Types with too few positives in dev keep T=1 (fitting would just sharpen everything)."""
    temps = {}
    Y = np.array([r["probs"] for r in rows])
    for ti, t in enumerate(types):
        z, y = logits[:, ti], Y[:, ti]
        if y.sum() < min_pos:
            temps[t] = 1.0
            continue
        best, bt = 1e18, 1.0
        for T in np.exp(np.linspace(np.log(0.5), np.log(4.0), 31)):
            p = 1 / (1 + np.exp(-z / T))
            nll = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
            if nll < best:
                best, bt = nll, float(T)
        temps[t] = round(bt, 4)
    return temps


class Trainer:
    def __init__(self, config: TrainConfig, log=print):
        self.c, self.log = config, log

    def _set_seed(self):
        import torch
        random.seed(self.c.seed); np.random.seed(self.c.seed); torch.manual_seed(self.c.seed)

    def predict(self, model, collator, rows, device, bs=64):
        import torch
        model.eval()
        L, G = [], []
        amp = self.c.fp16 and device.startswith("cuda")
        with torch.no_grad():
            for b in range(0, len(rows), bs):
                batch = collator(rows[b:b + bs])
                inp = {k: v.to(device) for k, v in batch.items() if k in ("input_ids", "attention_mask", "token_type_ids")}
                with torch.autocast("cuda", enabled=amp):
                    out = model(**inp)
                L.append(out["logits"].float().cpu())
                G.append((out["gap_logits"].argmax(-1).cpu(), batch["gap"]))
        logits = torch.cat(L).numpy() if L else np.zeros((0, 1))
        return logits, 1 / (1 + np.exp(-logits)), G

    def train_once(self, train_rows, dev_rows, types, gap_labels, device, round_id=0):
        import torch
        from torch.utils.data import DataLoader
        from transformers import AutoTokenizer, get_linear_schedule_with_warmup
        from .model import EllipsisModel, Collator, joint_loss
        c = self.c
        tok = AutoTokenizer.from_pretrained(c.encoder)
        collator = Collator(tok, gap_labels, c.max_len)
        model = EllipsisModel(c.encoder, len(types), len(gap_labels), dropout=c.dropout, att_dim=c.att_dim).to(device)
        from .model import freeze_embeddings as _freeze, empty_cache
        if c.freeze_embeddings:
            self.log(f"frozen word embeddings: {_freeze(model.encoder) / 1e6:.0f}M parameters")
        Y = np.array([r["probs"] for r in train_rows])
        prev = np.clip(Y.mean(0), 1e-4, 1)
        pos_weight = torch.tensor(np.clip((1 - prev) / prev, 1, c.max_pos_weight), dtype=torch.float, device=device)
        gap_w = torch.ones(len(gap_labels), device=device)
        gap_w[0] = c.gap_o_weight
        rows = list(train_rows)
        for ti in range(len(types)):                       # oversample rare types
            pos = [r for r in train_rows if r["probs"][ti] >= 0.5]
            need = c.min_pos_per_type - len(pos)
            if pos and need > 0:
                rows += [random.choice(pos) for _ in range(need)]
        dl = DataLoader(rows, batch_size=c.batch_size, shuffle=True, collate_fn=collator)
        enc_p = [p for n, p in model.named_parameters() if n.startswith("encoder.") and p.requires_grad]
        head_p = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
        opt = torch.optim.AdamW([{"params": enc_p, "lr": c.lr}, {"params": head_p, "lr": c.head_lr}], weight_decay=0.01)
        steps = math.ceil(len(dl) / c.grad_accum) * c.epochs
        sch = get_linear_schedule_with_warmup(opt, int(c.warmup * steps), steps)
        amp = c.fp16 and device.startswith("cuda")
        from .model import make_grad_scaler
        scaler = make_grad_scaler(amp)
        best_f1, best_state, bad = -1, None, 0
        th = {t: 0.5 for t in types}
        for ep in range(c.epochs):
            model.train()
            t0, tot, n = time.time(), 0.0, 0
            for it, batch in enumerate(dl):
                batch = {k: v.to(device) for k, v in batch.items()}
                inp = {k: batch[k] for k in ("input_ids", "attention_mask", "token_type_ids") if k in batch}
                with torch.autocast("cuda", enabled=amp):
                    out = model(**inp)
                loss, ls, lg = joint_loss(out, batch, pos_weight, gap_w, c.gap_lambda)
                scaler.scale(loss / c.grad_accum).backward()
                if (it + 1) % c.grad_accum == 0 or it + 1 == len(dl):
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
                    scaler.step(opt); scaler.update(); opt.zero_grad(set_to_none=True); sch.step()
                tot += float(loss.detach()); n += 1
                if it % c.log_every == 0:
                    self.log(f"  [r{round_id} ep{ep + 1} {it}/{len(dl)}] loss={tot / n:.4f}")
            if dev_rows:
                empty_cache(device)
                _, probs, G = self.predict(model, collator, dev_rows, device)
                empty_cache(device)
                th = tune_thresholds(probs, dev_rows, types)
                m = evaluate_probs(probs, dev_rows, types, th)
                self.log(f"  epoch {ep + 1}: dev macro-F1 {m['_macro_f1']} | gap {gap_accuracy(G)} | {time.time() - t0:.0f}s")
                if m["_macro_f1"] > best_f1:
                    best_f1, bad = m["_macro_f1"], 0
                    best_state = ({k: v.detach().cpu().clone() for k, v in model.state_dict().items()}, dict(th))
                else:
                    bad += 1
                    if bad >= c.patience:
                        self.log("  early stopping"); break
        if best_state:
            model.load_state_dict(best_state[0]); th = best_state[1]
        return model, tok, collator, th

    def run(self):
        import torch
        from .model import read_jsonl, save_model
        c = self.c
        self._set_seed()
        from .device import get_device
        device = get_device(c.device)
        meta = json.load(open(os.path.join(c.data, "meta.json"), encoding="utf-8"))
        types, gap_labels = meta["types"], meta["gap_labels"]
        train = read_jsonl(os.path.join(c.data, "train.jsonl"))
        dev = read_jsonl(os.path.join(c.data, "dev.jsonl"))
        test = read_jsonl(os.path.join(c.data, "test.jsonl"))
        if c.max_train:
            random.shuffle(train); train = train[: c.max_train]
        self.log(f"device {device} | train {len(train):,} dev {len(dev):,} test {len(test):,}")
        unl = []
        p = os.path.join(c.data, "unlabeled.txt")
        if os.path.exists(p):
            unl = [{"text": s.strip(), "words": tokenize(s.strip()), "probs": [0.0] * len(types)}
                   for s in open(p, encoding="utf-8") if s.strip()]
        model, tok, collator, th = self.train_once(train, dev, types, gap_labels, device)
        for r in range(1, c.self_train_rounds + 1):
            if not unl:
                break
            _, probs, _ = self.predict(model, collator, unl, device)
            pseudo = [{"text": row["text"], "words": row["words"],
                       "probs": [1.0 if x >= c.pseudo_pos else 0.0 for x in pr], "gap_labels": None,
                       "weight": 0.5, "source": "pseudo"}
                      for row, pr in zip(unl, probs) if all(x >= c.pseudo_pos or x <= c.pseudo_neg for x in pr)]
            pseudo = sorted(pseudo, key=lambda x: -sum(x["probs"]))[: c.pseudo_max]
            self.log(f"self-training round {r}: +{len(pseudo):,} pseudo-labelled sentences")
            model, tok, collator, th = self.train_once(train + pseudo, dev, types, gap_labels, device, r)
        temps = {t: 1.0 for t in types}
        if dev:
            logits, _, _ = self.predict(model, collator, dev, device)
            temps = fit_temperatures(logits, dev, types)
        out_meta = {"types": types, "gap_labels": gap_labels, "type2gap": meta["type2gap"], "thresholds": th,
                    "temperatures": temps, "encoder": c.encoder, "max_len": c.max_len, "att_dim": c.att_dim,
                    "teachers": meta.get("teachers"), "train_config": asdict(c)}
        save_model(model, tok, c.out, out_meta)
        metrics = {}
        for name, rows in (("dev", dev), ("test", test)):
            if not rows:
                continue
            logits, _, G = self.predict(model, collator, rows, device)
            T = np.array([temps[t] for t in types])
            probs = 1 / (1 + np.exp(-logits / T))
            m = {"vs_silver": evaluate_probs(probs, rows, types, th), "gap": gap_accuracy(G)}
            if any("gold" in r for r in rows):
                m["vs_manual_gold"] = evaluate_probs(probs, rows, types, th, use_gold=True)
            json.dump(m, open(os.path.join(c.out, f"{name}_metrics.json"), "w"), indent=2)
            metrics[name] = m
            self.log(f"{name}: silver macro-F1 {m['vs_silver']['_macro_f1']}" +
                     (f" | gold macro-F1 {m['vs_manual_gold']['_macro_f1']}" if "vs_manual_gold" in m else ""))
        if unl and c.n_annotate:
            import pandas as pd
            _, probs, _ = self.predict(model, collator, unl, device)
            unc = np.max(np.minimum(probs, 1 - probs), axis=1)
            rows = []
            for i in np.argsort(-unc)[: c.n_annotate]:
                ti = int(np.argmax(np.minimum(probs[i], 1 - probs[i])))
                rows.append({"sentence": unl[i]["text"], "pred_type": types[ti],
                             "confidence": round(float(probs[i, ti]), 3), "uncertainty": round(float(unc[i]), 3),
                             "gold_types": "", "gold_is_ellipsis": "", "gold_type": "", "notes": ""})
            pd.DataFrame(rows).to_csv(os.path.join(c.out, "to_annotate.csv"), index=False, encoding="utf-8-sig")
            self.log(f"active learning: {len(rows)} uncertain sentences -> {c.out}/to_annotate.csv")
        return metrics
