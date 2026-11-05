"""
llm.py — Fonction unique d'appel au LLM local (Ollama)

Utilisé par extract.py, text2sql.py, anomalies.py.
Paramètres fixes — NE PAS MODIFIER (règle méthodologique) :
  température 0, seed 42, num_ctx 4096, num_predict 512.

Délai maximal : 180 s par appel. Au-delà, retourne ("llm_timeout", elapsed)
sans planter le script appelant.
"""

import time
import threading
from typing import NamedTuple

import ollama

MODEL_OPTIONS = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 4096,
    "num_predict": 512,
}

TIMEOUT_S = 180          # secondes max par appel
TIMEOUT_SENTINEL = "llm_timeout"


class LLMResult(NamedTuple):
    text: str            # réponse ou TIMEOUT_SENTINEL
    elapsed: float       # durée réelle en secondes
    timed_out: bool


def call_llm(prompt: str, model: str = "qwen2.5:7b") -> LLMResult:
    """Appelle le LLM avec timeout.

    Returns:
        LLMResult(text, elapsed, timed_out)
        Si timed_out=True, text == TIMEOUT_SENTINEL.
    """
    result_container: list[str] = []
    error_container: list[Exception] = []

    def _run():
        try:
            resp = ollama.generate(model=model, prompt=prompt, options=MODEL_OPTIONS)
            result_container.append(resp["response"])
        except Exception as e:
            error_container.append(e)

    start = time.perf_counter()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=TIMEOUT_S)
    elapsed = round(time.perf_counter() - start, 2)

    if t.is_alive():
        # Le thread est encore en vie après TIMEOUT_S
        return LLMResult(TIMEOUT_SENTINEL, elapsed, True)

    if error_container:
        raise SystemExit(
            f"Erreur LLM : {error_container[0]}\n"
            f"Vérifiez qu'Ollama tourne : ollama serve"
        )

    return LLMResult(result_container[0], elapsed, False)
