"""Unit tests for ``tools/measure/measure.py::resolve_device``.

Regression: the original implementation only did substring matching on
device names, so passing ``--output-device 2`` matched
"BenQ BL2710" (because "2" is a substring of the name) and silently
sent audio to the wrong output.

The fixed version tries ``int()`` first, then falls back to substring.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip the whole module if sounddevice isn't installed (the import in
# measure.py would fail).  measure.py is in tools/measure and isn't a
# package, so we add it to sys.path on the fly.
pytest.importorskip("sounddevice")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "measure"))

import measure as _measure  # noqa: E402


# A representative sounddevice.query_devices() return — list of dicts
# with name + max_input_channels + max_output_channels.  Index "2"
# deliberately has a name with the digit "2" in it to lock in the fix.
FAKE_DEVICES = [
    {"name": "MacBook Pro Microphone",   "max_input_channels": 1, "max_output_channels": 0},
    {"name": "MacBook Pro Speakers",     "max_input_channels": 0, "max_output_channels": 2},
    {"name": "BenQ BL2710",              "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Scarlett 2i2 USB",         "max_input_channels": 2, "max_output_channels": 4},
    {"name": "Umik-1 Gain: 18dB",        "max_input_channels": 1, "max_output_channels": 0},
]


@pytest.fixture(autouse=True)
def _patch_query_devices():
    with patch.object(_measure.sd, "query_devices", return_value=FAKE_DEVICES):
        yield


# ── int-first resolution ────────────────────────────────────────────────
def test_int_index_resolves_to_that_index_for_output():
    # "2" must mean index 2 (BenQ BL2710), not "first device whose name
    # contains the digit 2".
    assert _measure.resolve_device("2", "output") == 2


def test_int_index_resolves_to_that_index_for_input():
    # "0" → MacBook Pro Microphone (input)
    assert _measure.resolve_device("0", "input") == 0


def test_int_index_works_for_scarlett():
    assert _measure.resolve_device("3", "output") == 3
    assert _measure.resolve_device("3", "input") == 3


def test_int_index_negative_or_huge_raises():
    with pytest.raises(RuntimeError, match="out of range"):
        _measure.resolve_device("99", "output")
    with pytest.raises(RuntimeError, match="out of range"):
        _measure.resolve_device("-1", "output")


def test_int_index_wrong_kind_raises_with_helpful_message():
    # MacBook Pro Microphone (index 0) has no output channels — asking
    # for it as 'output' must error rather than silently fall through
    # to substring matching some other "0"-containing device.
    with pytest.raises(RuntimeError, match="no output channels"):
        _measure.resolve_device("0", "output")
    # MacBook Pro Speakers (index 1) has no input channels
    with pytest.raises(RuntimeError, match="no input channels"):
        _measure.resolve_device("1", "input")


# ── substring fallback ────────────────────────────────────────────────
def test_substring_fallback_when_input_isnt_int():
    # "BenQ" → substring match → BenQ BL2710 (index 2)
    assert _measure.resolve_device("BenQ", "output") == 2


def test_substring_fallback_case_insensitive():
    assert _measure.resolve_device("scarlett", "input") == 3
    assert _measure.resolve_device("UMIK-1", "input") == 4


def test_substring_fallback_picks_correct_kind():
    # "MacBook" matches BOTH "MacBook Pro Microphone" (input) and
    # "MacBook Pro Speakers" (output).  Asking for output should give
    # the speakers, asking for input the microphone.
    assert _measure.resolve_device("MacBook", "output") == 1
    assert _measure.resolve_device("MacBook", "input") == 0


def test_no_match_raises():
    with pytest.raises(RuntimeError, match="No output device matching 'NoSuchThing'"):
        _measure.resolve_device("NoSuchThing", "output")


# ── the original bug it's worth pinning ───────────────────────────────
def test_bug_regression_int_2_does_not_substring_match_BenQ_BL2710():
    """The exact bug from the field report: '2' must not silently
    pick BenQ BL2710 even though '2' is in the name.

    BenQ IS index 2 in this fake device list, so the assertion looks
    like the same answer — but the WIN here is that ``"2"`` parses
    as int(2), bypassing substring match entirely.  The substring
    match would also have hit MacBook Pro Speakers via "Speakers" if
    it were re-ordered first; we just lock in that int-first wins.
    """
    # Re-order so MacBook Pro Speakers is at index 0 — then int(2)
    # still picks BenQ BL2710 deterministically (= index 2 in the
    # NEW order), proving int parse runs before substring scan.
    reordered = [
        FAKE_DEVICES[1],   # MacBook Pro Speakers (was 1) → now 0
        FAKE_DEVICES[0],   # MacBook Pro Microphone (was 0) → now 1
        FAKE_DEVICES[2],   # BenQ BL2710 (was 2) → still 2
        FAKE_DEVICES[3],
        FAKE_DEVICES[4],
    ]
    with patch.object(_measure.sd, "query_devices", return_value=reordered):
        assert _measure.resolve_device("2", "output") == 2
        # Sanity-check the reordering: "Speakers" via substring is now 0
        assert _measure.resolve_device("Speakers", "output") == 0
