"""
LLM teacher: an instruction-tuned large language model annotates ellipsis.

The model gets the taxonomy (definitions + Hindi examples) and an indexed sentence
("0:राम 1:ने 2:चाय ...") and must return JSON:

  {"items": [{"type": "GAPPING_FORWARD", "gap_before": 8, "elided": "पी",
              "antecedent": "पी", "confidence": 0.9}]}

Back-ends
  * "openai"        any OpenAI-compatible chat endpoint: vLLM, Ollama, LM Studio, TGI,
                    or a hosted API (set --llm_base_url, --llm_model, env LLM_API_KEY)
  * "transformers"  a local HF chat model (e.g. Qwen/Qwen2.5-7B-Instruct)

Use it on a sample (a few thousand sentences); its votes enter the label model
like any other teacher, and its reliability per type is estimated there.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from ..types import TYPES, DEFINITIONS

SYSTEM = (
    "You are an expert in Hindi syntax. You annotate ELLIPSIS: material that is understood but not "
    "pronounced. Use ONLY these types:\n" +
    "\n".join(f"- {t}: {d}" for t, d in DEFINITIONS.items()) +
    "\nRules: a sentence may have zero, one or several ellipses. 'gap_before' is the index of the word "
    "BEFORE which the elided material belongs (use the number of words for the end). Overt pronouns, "
    "ordinary complete clauses and lists are NOT ellipsis. Answer with JSON only: "
    '{"items": [{"type": ..., "gap_before": int, "elided": "<the missing Hindi words>", '
    '"antecedent": "<where it is recovered from, or empty>", "confidence": 0-1}]} '
    'and {"items": []} when there is no ellipsis.')

FEW_SHOT = [
    ("राम ने चाय पी और सीता ने कॉफ़ी ।",
     {"items": [{"type": "GAPPING_FORWARD", "gap_before": 8, "elided": "पी", "antecedent": "पी", "confidence": 0.95}]}),
    ("कल सुबह दिल्ली जाऊँगा ।",
     {"items": [{"type": "PRO_DROP_SUBJECT", "gap_before": 0, "elided": "मैं", "antecedent": "", "confidence": 0.9}]}),
    ("सरकार ने नई योजना शुरू की है ।", {"items": []}),
]

JSON_RE = re.compile(r"\{.*\}", re.S)


def indexed(words: List[str]) -> str:
    return " ".join(f"{i}:{w}" for i, w in enumerate(words))


def parse_response(text: str, n_words: int, min_conf: float = 0.5) -> Tuple[Dict[str, int], List[Tuple[int, str]], list]:
    votes = {t: 0 for t in TYPES}
    gaps, items = [], []
    m = JSON_RE.search(text or "")
    if not m:
        return votes, gaps, items
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return votes, gaps, items
    for it in obj.get("items", []) if isinstance(obj, dict) else []:
        t = str(it.get("type", "")).strip().upper()
        if t not in votes:
            continue
        try:
            conf = float(it.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        if conf < min_conf:
            continue
        votes[t] = 1
        g = it.get("gap_before")
        if isinstance(g, (int, float)) and 0 <= int(g) <= n_words:
            gaps.append((int(g), t))
        items.append(it)
    return votes, gaps, items


class LLMTeacher:
    name = "llm"
    types = TYPES

    def __init__(self, backend="openai", model="qwen2.5:7b-instruct", base_url=None, api_key=None,
                 max_workers=8, temperature=0.0, max_new_tokens=300, min_conf=0.5):
        self.backend, self.model, self.min_conf = backend, model, min_conf
        self.temperature, self.max_new_tokens, self.max_workers = temperature, max_new_tokens, max_workers
        if backend == "openai":
            self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
            self.api_key = api_key or os.environ.get("LLM_API_KEY", "none")
        elif backend == "transformers":
            from transformers import pipeline
            self.pipe = pipeline("text-generation", model=model, device_map="auto")
        else:
            raise ValueError(backend)

    def _messages(self, words):
        msgs = [{"role": "system", "content": SYSTEM}]
        for s, ans in FEW_SHOT:
            msgs.append({"role": "user", "content": indexed(s.split())})
            msgs.append({"role": "assistant", "content": json.dumps(ans, ensure_ascii=False)})
        msgs.append({"role": "user", "content": indexed(words)})
        return msgs

    def _call_openai(self, words):
        import urllib.request
        body = json.dumps({"model": self.model, "messages": self._messages(words), "temperature": self.temperature,
                           "max_tokens": self.max_new_tokens}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
        except Exception as e:                          # network/model errors -> abstain
            return f"__error__ {e}"

    def _call_local(self, words):
        out = self.pipe(self._messages(words), max_new_tokens=self.max_new_tokens, do_sample=False)
        return out[0]["generated_text"][-1]["content"]

    def label(self, words_batch: List[List[str]]):
        """Returns [(votes, gaps, items) or None-if-failed] per sentence."""
        call = self._call_openai if self.backend == "openai" else self._call_local
        if self.backend == "openai":
            with ThreadPoolExecutor(self.max_workers) as ex:
                texts = list(ex.map(call, words_batch))
        else:
            texts = [call(w) for w in words_batch]
        res = []
        for w, t in zip(words_batch, texts):
            res.append(None if t.startswith("__error__") else parse_response(t, len(w), self.min_conf))
        return res
