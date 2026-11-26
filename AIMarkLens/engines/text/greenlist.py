"""Statistical text watermarking lab (green-list scheme, after Kirchenbauer et al., 2023).

Real LLM watermarks such as SynthID Text bias token choices with secret keys
and the model's own tokenizer, so they cannot be checked without the provider.
This module implements a transparent word-level version so you can study the
idea: generate synthetic watermarked text with a key, transform it, and watch
the detection z-score respond.

A word is "green" when a keyed hash of (previous word, word) falls below gamma.
Unwatermarked text has about gamma green words; watermarked text has more,
measured by z = (G - gamma*T) / sqrt(T * gamma * (1 - gamma)).
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass

TOKEN = re.compile(r"[A-Za-zÀ-ɏ]+(?:'[A-Za-z]+)?|\d+")
Z_THRESHOLD = 4.0
DEFAULT_KEY = "AIMarkLens-demo"
DEFAULT_GAMMA = 0.25
DEFAULT_DELTA = 1.0

BUILTIN_CORPUS = """
The river runs slowly through the old town and the morning light falls across the water. People walk along
the bank with their dogs, and the cafes open their shutters before the first train arrives. In the market the
traders set out fruit, bread and flowers, and the smell of coffee drifts over the square. A small boat waits
by the bridge while the ferryman checks the ropes and the weather. Clouds gather over the hills to the west,
but the sun still warms the stone walls of the library. Children run to school with their bags, and the bells
of the church ring for the hour. Later in the day the wind turns cold and the light grows soft and golden.
The gardens near the station are quiet in the afternoon, and an old man reads a newspaper on a green bench.
Students meet in the library to work on their projects, sharing notes and ideas about history, music and
science. The museum on the hill holds maps, paintings and tools from many centuries, and visitors move from
room to room in careful silence. When evening comes the streets fill again with people going home, and the
lamps along the river glow in long reflections. A cyclist crosses the bridge, a train leaves the station, and
somewhere a window opens to let in the cool air. The town has changed over the years, yet the river, the
market and the hills remain, and each season brings its own colours and sounds. In winter the frost covers the
fields and the roads are quiet; in spring the trees along the river come into flower and the market is busy
again. Summer brings long evenings, music in the square and boats on the water. Autumn returns with rain,
fallen leaves and the warm light of shop windows. Through all of it the people of the town keep their simple
routines, walking, working, talking and resting, and the river carries on towards the sea.
"""


@dataclass
class GreenResult:
    tokens: int
    green: int
    gamma: float
    z: float
    p_value: float

    @property
    def detected(self) -> bool:
        return self.tokens >= 16 and self.z >= Z_THRESHOLD

    @property
    def green_fraction(self) -> float:
        return self.green / self.tokens if self.tokens else 0.0


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text)]


def is_green(key: str, previous: str, word: str, gamma: float) -> bool:
    digest = hashlib.blake2b(f"{key}\x1f{previous}\x1f{word}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2 ** 64 < gamma


def detect(text: str, key: str = DEFAULT_KEY, gamma: float = DEFAULT_GAMMA, ignore_repeats: bool = True) -> GreenResult:
    words = tokens(text)
    seen: set[tuple[str, str]] = set()
    total = green = 0
    for previous, word in zip(words, words[1:]):
        pair = (previous, word)
        if ignore_repeats:
            if pair in seen:
                continue
            seen.add(pair)
        total += 1
        green += is_green(key, previous, word, gamma)
    if total == 0:
        return GreenResult(0, 0, gamma, 0.0, 1.0)
    z = (green - gamma * total) / math.sqrt(total * gamma * (1 - gamma))
    return GreenResult(total, green, gamma, z, 0.5 * math.erfc(z / math.sqrt(2)))


class SyntheticGenerator:
    """Bigram sampler over a corpus, with a keyed green-list bias (delta added to green logits)."""

    def __init__(self, corpus: str = BUILTIN_CORPUS) -> None:
        stream = re.findall(r"[A-Za-z']+|[.!?]", corpus)
        self.vocab = sorted({w.lower() for w in stream if w[0].isalpha()})
        self.followers: dict[str, dict[str, int]] = {}
        previous = "."
        for item in stream:
            item = item.lower()
            self.followers.setdefault(previous, {})
            self.followers[previous][item] = self.followers[previous].get(item, 0) + 1
            previous = item
        if len(self.vocab) < 20:
            raise ValueError("The corpus needs at least 20 distinct words.")

    def generate(self, words: int, key: str, gamma: float, delta: float, seed: int = 0) -> str:
        rng = random.Random(seed)
        out: list[str] = []
        state, previous_word, produced = ".", "", 0
        capitalise = True
        while produced < words:
            options = dict(self.followers.get(state, {}))
            for w in rng.sample(self.vocab, min(24, len(self.vocab))):
                options.setdefault(w, 0.35)
            names = list(options)
            weights = []
            for name in names:
                logit = math.log(options[name])
                if name not in ".!?" and previous_word and is_green(key, previous_word, name, gamma):
                    logit += delta
                weights.append(math.exp(logit))
            choice = rng.choices(names, weights)[0]
            if choice in ".!?":
                if out and out[-1][-1] not in ".!?":
                    out[-1] += choice
                    capitalise = True
                state = choice
                continue
            out.append(choice.capitalize() if capitalise else choice)
            capitalise = False
            state, previous_word = choice, choice
            produced += 1
        text = " ".join(out)
        return text if text.endswith((".", "!", "?")) else text + "."
