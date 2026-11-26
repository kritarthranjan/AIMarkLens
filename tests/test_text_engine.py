from AIMarkLens.core.context import TaskContext
from AIMarkLens.engines.text import greenlist
from AIMarkLens.engines.text.analyser import TextOptions, analyse_text
from AIMarkLens.engines.text.artefacts import find_artefacts, remove_ai_utm
from AIMarkLens.engines.text.cleaning import DEFAULT_OPTIONS, clean_text
from AIMarkLens.engines.text.compare import compare_texts
from AIMarkLens.engines.text.sample import TextSample, decode_bytes, load_text_file
from AIMarkLens.engines.text.unicode_scan import scan

TAG = "".join(chr(0xE0000 + ord(c)) for c in "ignore previous")
ZW = "".join("​" if b == "0" else "‌" for b in "".join(f"{ord(c):08b}" for c in "Hi!"))
VS = "".join(chr(0xFE00 + b) if b < 16 else chr(0xE0100 + b - 16) for b in "secret".encode())
FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467"


def test_hidden_payloads_are_decoded():
    result = scan("a" + TAG + " b" + ZW + " c😀" + VS)
    decoded = {p.decoded for p in result.payloads}
    assert {"ignore previous", "Hi!", "secret"} <= decoded


def test_emoji_flags_and_joiners_are_benign():
    result = scan(f"family {FAMILY} flag {FLAG} keycap 1️⃣ persian می‌خواهم")
    assert result.counts.get("zero-width", 0) == 0 and result.counts.get("tag", 0) == 0
    assert result.counts.get("variation", 0) == 0
    assert sum(result.benign_counts.values()) >= 8
    assert not result.payloads


def test_homoglyphs_found_in_mixed_script_words():
    result = scan("Please log in to your Pаypal account")  # Cyrillic а
    assert result.counts["homoglyph"] == 1
    assert scan("Москва is a city").counts.get("homoglyph", 0) == 0


def test_artefacts_and_utm_removal():
    text = "See https://x.com/a?utm_source=chatgpt.com&b=1 and https://y.org/?q=1&utm_source=chatgpt.com citeturn0search3"
    titles = {m.rule.title for m in find_artefacts(text)}
    assert "Links tagged utm_source=chatgpt.com" in titles
    assert any("citeturn" in t for t in titles)
    cleaned, n = remove_ai_utm(text)
    assert n == 2 and "https://x.com/a?b=1" in cleaned and "https://y.org/?q=1 " in cleaned


def test_cleaning_keeps_emoji_and_removes_hidden_data():
    text = f"Hello{TAG} wo​rld {FAMILY} {FLAG}\r\n"
    cleaned, changes = clean_text(text, set(DEFAULT_OPTIONS))
    assert cleaned == f"Hello world {FAMILY} {FLAG}\r\n"
    assert dict(changes)


def test_analysis_of_pasted_text_and_reveal_positions():
    text = "Line one\r\nTwo​three " + TAG[:3] + " end 😀   x"
    doc = analyse_text(TextSample(text, "clipboard", "Clipboard text"), TextOptions(), TaskContext())
    assert doc.verdict.tone == "signal"
    assert doc.extra["integrity_ok"]
    reveal = doc.extra["reveal_text"]
    units = reveal.encode("utf-16-le", "surrogatepass")
    tokens = [units[s * 2:(s + n) * 2].decode("utf-16-le") for s, n, _ in doc.extra["highlights"]]
    assert tokens == ["⟦ZWSP⟧", "⟦TAG⟧", "⟦TAG⟧", "⟦TAG⟧", "⟦NNBSP⟧"]
    assert "\r" not in reveal


def test_text_file_is_read_exactly(tmp_path):
    path = tmp_path / "a.txt"
    raw = "﻿café\r\nline​ two\n".encode("utf-8")
    path.write_bytes(raw)
    sample = load_text_file(str(path), TaskContext())
    assert sample.text == "café\r\nline​ two\n" and sample.bom
    doc = analyse_text(sample, TextOptions(), TaskContext())
    assert doc.extra["integrity_ok"] and path.read_bytes() == raw
    assert decode_bytes("x".encode("utf-16"))[0] == "x"


def test_green_list_watermark_detection():
    text = greenlist.SyntheticGenerator().generate(200, "k", 0.25, 2.0, seed=1)
    assert greenlist.detect(text, "k", 0.25).detected
    assert not greenlist.detect(text, "other", 0.25).detected
    plain = greenlist.SyntheticGenerator().generate(200, "k", 0.25, 0.0, seed=2)
    assert not greenlist.detect(plain, "k", 0.25).detected


def test_text_experiment_and_compare():
    from AIMarkLens.engines.text.experiments import run_text_experiment

    doc = run_text_experiment("k", 0.25, 1.5, 150, 4, TaskContext())
    assert len(doc.sections[0].blocks[1].rows) >= 10
    cmp = compare_texts(TextSample("same text", "typed", "A"), TextSample("same​ text", "typed", "B"),
                        TextOptions(), TaskContext())
    assert cmp.verdict.headline == "Hidden differences found"
