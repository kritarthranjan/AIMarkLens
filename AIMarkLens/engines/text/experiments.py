"""Text watermark robustness experiments on synthetic samples.

Synthetic text is generated with the lab green-list watermark, transformed in
ways that mimic editing and evasion attempts, and re-tested. Everything runs
in memory; nothing is written unless you export the report.
"""

from __future__ import annotations

import random
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Callable

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.report import Cell, KeyValues, Paragraph, ReportDocument, Section, Table
from AIMarkLens.engines.text import greenlist
from AIMarkLens.engines.text.cleaning import DEFAULT_OPTIONS, clean_text


@dataclass(frozen=True)
class TextTransform:
    key: str
    label: str
    apply: Callable[[str, random.Random, list[str]], str]


def _words(text: str) -> list[str]:
    return text.split(" ")


def _delete(fraction: float):
    def run(text: str, rng: random.Random, vocab: list[str]) -> str:
        return " ".join(w for w in _words(text) if rng.random() >= fraction)
    return run


def _substitute(fraction: float):
    def run(text: str, rng: random.Random, vocab: list[str]) -> str:
        return " ".join(rng.choice(vocab) if rng.random() < fraction else w for w in _words(text))
    return run


def _swap(fraction: float):
    def run(text: str, rng: random.Random, vocab: list[str]) -> str:
        words = _words(text)
        for i in range(len(words) - 1):
            if rng.random() < fraction:
                words[i], words[i + 1] = words[i + 1], words[i]
        return " ".join(words)
    return run


def _insert_zero_width(text: str, rng: random.Random, vocab: list[str]) -> str:
    out = []
    for w in _words(text):
        if len(w) > 3 and rng.random() < 0.3:
            k = rng.randrange(1, len(w))
            w = w[:k] + "​" + w[k:]
        out.append(w)
    return " ".join(out)


_HOMOGLYPHS = {"a": "а", "e": "е", "o": "о", "c": "с", "p": "р"}


def _homoglyphs(text: str, rng: random.Random, vocab: list[str]) -> str:
    return "".join(_HOMOGLYPHS[c] if c in _HOMOGLYPHS and rng.random() < 0.15 else c for c in text)


def _cleaned(inner):
    def run(text: str, rng: random.Random, vocab: list[str]) -> str:
        attacked = inner(text, rng, vocab)
        return unicodedata.normalize("NFKC", clean_text(attacked, set(DEFAULT_OPTIONS) | {"homoglyph"})[0])
    return run


TEXT_TRANSFORMS: tuple[TextTransform, ...] = (
    TextTransform("none", "No change", lambda t, r, v: t),
    TextTransform("lower", "Lower-case everything", lambda t, r, v: t.lower()),
    TextTransform("truncate", "Keep the first half", lambda t, r, v: " ".join(_words(t)[: len(_words(t)) // 2])),
    TextTransform("delete10", "Delete 10% of words", _delete(0.10)),
    TextTransform("delete30", "Delete 30% of words", _delete(0.30)),
    TextTransform("sub10", "Replace 10% of words (light paraphrase)", _substitute(0.10)),
    TextTransform("sub30", "Replace 30% of words (heavy paraphrase)", _substitute(0.30)),
    TextTransform("swap", "Swap 20% of neighbouring words", _swap(0.20)),
    TextTransform("zw", "Insert zero-width spaces inside words", _insert_zero_width),
    TextTransform("zw-clean", "Zero-width spaces, then cleaned", _cleaned(_insert_zero_width)),
    TextTransform("homoglyph", "Swap 15% of letters for look-alikes", _homoglyphs),
    TextTransform("homoglyph-clean", "Look-alikes, then cleaned", _cleaned(_homoglyphs)),
)


def generate_sample(key: str, gamma: float, delta: float, words: int, seed: int = 7) -> tuple[str, greenlist.GreenResult]:
    text = greenlist.SyntheticGenerator().generate(words, key, gamma, delta, seed)
    return text, greenlist.detect(text, key, gamma)


def run_text_experiment(key: str, gamma: float, delta: float, words: int, trials: int, ctx: TaskContext) -> ReportDocument:
    generator = greenlist.SyntheticGenerator()
    vocab = generator.vocab
    samples = []
    for trial in range(trials):
        ctx.progress(0.3 * trial / trials, f"Generating synthetic sample {trial + 1} of {trials}")
        samples.append(generator.generate(words, key, gamma, delta, seed=1000 + trial))
    baseline = generator.generate(words, key, gamma, 0.0, seed=99)

    rows = []
    for index, transform in enumerate(TEXT_TRANSFORMS):
        ctx.progress(0.3 + 0.65 * index / len(TEXT_TRANSFORMS), f"Testing: {transform.label}")
        scores, detected, counted = [], 0, []
        for trial, text in enumerate(samples):
            rng = random.Random(trial * 7919 + index)
            result = greenlist.detect(transform.apply(text, rng, vocab), key, gamma)
            scores.append(result.z)
            counted.append(result.tokens)
            detected += result.detected
        rate = detected / len(samples)
        tone = "ok" if rate >= 0.9 else "warning" if rate >= 0.5 else "error"
        rows.append([transform.label, f"{statistics.fmean(scores):.2f}",
                     f"{statistics.pstdev(scores):.2f}" if len(scores) > 1 else "–",
                     Cell(f"{rate:.0%}", tone), f"{statistics.fmean(counted):.0f}"])
    control = greenlist.detect(baseline, key, gamma)

    doc = ReportDocument(title="Text watermark robustness experiment", subject=f"Synthetic samples (key '{key}')",
                         kind="experiment")
    doc.integrity = [("Originals", "Not applicable: all samples are synthetic and generated in memory")]
    doc.sections.append(Section("Results", [
        Paragraph(f"{trials} synthetic samples of {words} words were generated with the lab green-list watermark "
                  f"(gamma {gamma:.2f}, bias {delta:.1f}). Each transformation was applied to every sample and "
                  f"the watermark re-tested. Detection means z ≥ {greenlist.Z_THRESHOLD:.0f}."),
        Table(["Transformation", "Mean z", "Spread (σ)", "Detected", "Scored pairs"], rows),
        Paragraph(f"Control: unwatermarked text from the same generator scores z = {control.z:.2f}.", "info"),
    ], "Results"))
    doc.sections.append(Section("What this shows", [Paragraph(
        "Statistical watermarks degrade gradually as words change, and invisible-character tricks that split words "
        "also break detection until the text is cleaned. Real schemes such as SynthID Text behave similarly but "
        "operate on model tokens with secret keys.")], "Results"))
    doc.sections.append(Section("Settings", [KeyValues([("Key", key), ("Gamma", f"{gamma:.2f}"), ("Bias (delta)", f"{delta:.1f}"),
                                                        ("Words per sample", str(words)), ("Trials", str(trials))])], "Results"))
    doc.extra["example"] = samples[0] if samples else ""
    doc.extra["integrity_ok"] = True
    ctx.progress(1.0, "Experiment complete")
    return doc
