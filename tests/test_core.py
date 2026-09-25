"""Unit tests of BiblioMancer (no model, no PDFs needed).

Run from the repository root:  python -m pytest tests -q
"""

from __future__ import annotations

import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bibliomancer import cli, config, core as kb  # noqa: E402
from bibliomancer.gui import KBProgress  # noqa: E402


def test_authors_capped_and_normalised():
    n = kb.normalise_authors
    assert n("Rakhmanov M, Sic H, Kienzler A-K, Fischer B, Rizzi M, Seidl M, Melkaoui K") == \
        "Rakhmanov M, Sic H, Kienzler A-K, Fischer B, Rizzi M, Seidl M et al."
    assert n("Ochocka, N., Kaminska, B.") == "Ochocka N., Kaminska B."
    assert n("Stuart, Tim; Satija, Rahul") == "Stuart T, Satija R"
    assert n("Jiang, Lehnert, You and Snell") == "Jiang, Lehnert, You, Snell"
    assert n("Gemini Team, Google") == "Gemini Team, Google"
    assert n("") == ""


def test_clean_for_model_drops_plot_glyphs():
    text = "--- Page 1 ---\nTitle of the paper\n●\n●●\n● ● ●\n\n\n\n0.5\nResults: 12% better\n·\n"
    out = kb.clean_for_model(text)
    assert "●" not in out and "·" not in out
    assert "--- Page 1 ---" in out and "Title of the paper" in out and "0.5" in out
    assert "\n\n\n" not in out


def test_context_overflow_parsed():
    class R:
        text = ('{"error":"... request (65357 tokens) exceeds the available context size '
                '(49152 tokens), try increasing it"}')
    assert kb._context_overflow(R()) == (65357, 49152)

    class Other:
        text = '{"error":"something else"}'
    assert kb._context_overflow(Other()) is None


def test_record_normalised():
    rec = kb.normalise_record({"title": " T ", "year": "Published 2024-05", "authors": "A, B",
                               "keywords": "x; y", "research_fields": ["F"]})
    assert rec["title"] == "T" and rec["year"] == "2024" and rec["keywords"] == ["x", "y"]


def test_derived_limits_grow_with_tokens():
    chars, ctx = kb.derived_limits(kb.DEFAULT_DOC_TOKENS)
    assert ctx == kb.MIN_CONTEXT_LENGTH and chars == int(kb.DEFAULT_DOC_TOKENS * kb.CHARS_PER_TOKEN)
    chars2, ctx2 = kb.derived_limits(30000)
    assert ctx2 > ctx and ctx2 % 1024 == 0 and chars2 > chars


def test_reasoning_off_by_default():
    assert config.DEFAULTS["reasoning_effort"] == "none"
    assert "reasoning_effort" in kb.reasoning_args() or kb.REASONING_EFFORT == ""


def test_kb_config_json_migrates(tmp_path, monkeypatch):
    pdfs = str(tmp_path / "pdfs")                                 # absolute on every OS
    (tmp_path / "kb_config.json").write_text(json.dumps(
        {"papers_dir": pdfs, "max_pages": 40, "ignored": 1}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(config, "PORTABLE", True)
    cfg = config.load()
    assert cfg["papers_dir"] == pdfs and cfg["max_pages"] == 40
    assert cfg["data_dir"] == config.APP_HOME                 # portable: data next to the code
    assert (tmp_path / "kb_config.json.migrated").exists()
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "# Your PDF library" in text and "ignored" not in text


def test_template_matches_defaults():
    import yaml
    tpl = yaml.safe_load(config.TEMPLATE)
    assert set(tpl) == set(config.DEFAULTS)


def test_progress_reads_pipeline_lines():
    p = KBProgress("update", hint=3)
    for ln in ["=== start LM Studio server ===", "=== model already loaded: x ===",
               "[2026-09-25 01:15:50] Scan complete in 1.2s - 2 new paper(s) pending.",
               "=== ingest new papers ===", "[1/2] Folder\\a.pdf", "  OK  14.2s  Title A",
               "[2/2] b.pdf", "      page 1: 300 chars via vision"]:
        p.feed(ln)
    assert p.step_label() == "Reading new papers"
    frac, cap = p.step_fraction()
    assert 0.5 <= frac < 1 and cap.startswith("Paper 2 of 2")
    assert "vision" in p.item_text()
    p.feed("OK 10.0s Title B")
    p.feed("=== update semantic index ===")
    p.feed("  embedded 32/64")
    assert p.step_fraction() == (0.5, "Embedded 32 of 64")
    p.feed("=== pipeline finished in 2.0 min ===")
    assert p.overall() == 1.0 and p.summary() == "  ·  2 paper(s) read"


def test_every_cli_command_has_a_module_with_main():
    for cmd, (mod, _prefix) in cli.MODULES.items():
        m = importlib.import_module(f"bibliomancer.{mod}")
        assert callable(getattr(m, "main", None)), cmd
