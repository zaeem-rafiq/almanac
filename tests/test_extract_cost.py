"""Cost controls on extraction: prompt caching, low effort, and the opt-in batch path.

**What these tests can and cannot prove.** The credit balance is exhausted, so nothing here has
been measured against the live API — `count_tokens` is refused too. What is asserted is the
request SHAPE: that the cache breakpoint sits where it saves money rather than where it burns it,
that effort is set, that the batch path sends byte-identical requests to the synchronous one, and
that no removed sampling parameter has crept back. The savings are arithmetic on top of that
shape, not observation. `usage.cache_read_input_tokens` on the first paid run is the real proof.

Nothing here touches what the model is ASKED — the prompt, the tool schema, the forced tool
choice and the parsing are unchanged. These tests pin that too.
"""

from __future__ import annotations

import threading
import time

import pytest

from almanac import extract


def params(text: str = "chunk text") -> dict:
    return extract._request_params(text, "SYSTEM PROMPT", "claude-opus-5")


# ------------------------------------------------------------------ 1. prompt caching placement


def test_cache_breakpoint_sits_at_the_end_of_system_not_on_the_chunk() -> None:
    """The cached prefix must be tools + system — the part that repeats.

    Render order is tools -> system -> messages. A breakpoint on the LAST cacheable block (what
    top-level auto-caching would do) would land on the per-chunk user text: every call would
    write a fresh entry and none would ever read one, paying the 1.25x write premium for nothing.
    """
    p = params()
    assert isinstance(p["system"], list), "cache_control is a block field; system must be blocks"
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"}

    # The volatile part carries no breakpoint of its own.
    assert "cache_control" not in p
    for message in p["messages"]:
        assert "cache_control" not in message


def test_the_cached_prefix_is_identical_across_chunks() -> None:
    """A prefix that varies per call is a prefix that never gets read."""
    a, b = params("first video"), params("second video")
    assert a["system"] == b["system"]
    assert a["tools"] == b["tools"]
    assert a["messages"] != b["messages"], "the chunk text is what should differ"


def test_the_cached_prefix_clears_the_models_minimum() -> None:
    """Opus 5 caches from 512 tokens up; below that it silently does not cache at all."""
    import json

    p = params()
    prefix_chars = len(p["system"][0]["text"]) + len(json.dumps(p["tools"]))
    assert prefix_chars / 4 > 512, f"prefix is only ~{prefix_chars // 4} tokens; too short to cache"


# ------------------------------------------------------------------------------- 2. low effort


def test_effort_is_low_and_thinking_is_left_alone() -> None:
    """Extraction is labelling, not reasoning — but thinking must NOT be disabled.

    On Opus 5, `thinking: {type: "disabled"}` can make the model write a tool call into visible
    TEXT instead of a tool_use block: the turn succeeds, the call never runs, nothing raises. For
    a forced-tool-use extractor that failure is silent and total, so the lever is low effort.
    """
    p = params()
    assert p["output_config"] == {"effort": extract.EXTRACT_EFFORT}
    assert extract.EXTRACT_EFFORT == "low"
    assert "thinking" not in p, "thinking must stay at the adaptive default, not be disabled"


@pytest.mark.parametrize("removed", ["temperature", "top_p", "top_k", "seed", "random_seed"])
def test_no_removed_sampling_parameter_has_crept_back(removed: str) -> None:
    """ADR-000 §10: none of these exist on this model. Passing one is a 400 or a TypeError."""
    assert removed not in params()


def test_what_the_model_is_asked_has_not_changed() -> None:
    """The cost work must not become a prompt change by accident."""
    p = params()
    assert p["tool_choice"] == {"type": "tool", "name": extract.TOOL_NAME}
    assert p["tools"][0]["name"] == extract.TOOL_NAME
    assert p["tools"][0]["input_schema"] == extract.Extraction.model_json_schema()
    assert p["system"][0]["text"] == "SYSTEM PROMPT"
    assert p["messages"] == [{"role": "user", "content": "chunk text"}]
    assert p["max_tokens"] == extract.MAX_TOKENS


# --------------------------------------------------------------- 3. warm-then-fan cache warming


class RecordingClient:
    """Records when each call starts and ends, so serialisation is observable."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.lock = threading.Lock()
        self.messages = self

    def create(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        with self.lock:
            self.events.append(f"start:{content}")
        time.sleep(0.05)
        with self.lock:
            self.events.append(f"end:{content}")
        return type("M", (), {"content": []})()


def test_the_first_call_runs_alone_to_warm_the_cache() -> None:
    """Fanning out cold would have every in-flight call MISS and pay the write premium.

    N concurrent misses cost more than no caching at all. One serialized call turns N writes
    into 1 write + (N-1) reads.
    """
    client = RecordingClient()
    jobs = [(f"j{i}", f"chunk{i}") for i in range(4)]
    extract._run_jobs(jobs, "SYS", client, "claude-opus-5", max_workers=4, batch=False)

    first_end = client.events.index("end:chunk0")
    later_starts = [
        i for i, e in enumerate(client.events) if e.startswith("start:") and e != "start:chunk0"
    ]
    assert all(i > first_end for i in later_starts), (
        f"a later call started before the warming call finished: {client.events}"
    )


def test_every_job_still_gets_a_result_key() -> None:
    client = RecordingClient()
    jobs = [(f"j{i}", f"chunk{i}") for i in range(4)]
    out = extract._run_jobs(jobs, "SYS", client, "claude-opus-5", max_workers=4, batch=False)
    assert sorted(out) == ["j0", "j1", "j2", "j3"]


# ------------------------------------------------------------------------------ 4. batch path


class FakeBatches:
    """A Message Batches stand-in. Returns results OUT OF ORDER on purpose."""

    def __init__(self, statuses=("in_progress", "ended"), fail: set[str] | None = None) -> None:
        self.sent: list = []
        self.statuses = list(statuses)
        self.fail = fail or set()

    def create(self, requests):
        self.sent = requests
        return type("B", (), {"id": "batch_test", "processing_status": "in_progress"})()

    def retrieve(self, _id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return type("B", (), {"id": _id, "processing_status": status})()

    def results(self, _id):
        rows = []
        for request in self.sent:
            cid = request["custom_id"]
            text = request["params"]["messages"][0]["content"]
            if cid in self.fail:
                result = type("R", (), {"type": "errored", "message": None})()
            else:
                block = type("Blk", (), {
                    "type": "tool_use",
                    "input": {"claims": [{
                        "quote": text, "claim_type": "illustrative", "confidence": 0.9,
                    }]},
                })()
                message = type("M", (), {"content": [block]})()
                result = type("R", (), {"type": "succeeded", "message": message})()
            rows.append(type("Row", (), {"custom_id": cid, "result": result})())
        return list(reversed(rows))  # results arrive in ANY order


class FakeBatchClient:
    def __init__(self, batches: FakeBatches) -> None:
        self.messages = type("M", (), {"batches": batches})()


def test_batch_maps_results_by_custom_id_not_by_position() -> None:
    """Results come back in any order. Matching by position silently scrambles the corpus."""
    batches = FakeBatches()
    client = FakeBatchClient(batches)
    chunks = {"alpha": "text-A", "beta": "text-B", "gamma": "text-C"}

    out = extract._call_model_batch(chunks, "SYS", client, "claude-opus-5", poll_seconds=0)

    assert sorted(out) == ["alpha", "beta", "gamma"]
    for key, text in chunks.items():
        assert out[key][0].quote == text, f"{key} received another request's result"


def test_batch_sends_the_same_request_body_as_the_synchronous_path() -> None:
    """The two paths differ in transport and price — never in what the model is asked."""
    batches = FakeBatches()
    extract._call_model_batch({"k": "chunk text"}, "SYSTEM PROMPT", FakeBatchClient(batches),
                              "claude-opus-5", poll_seconds=0)
    sent = dict(batches.sent[0]["params"])
    assert sent == params("chunk text")


def test_batch_survives_one_errored_request() -> None:
    """A failed request yields no claims for its key; the coverage sweep is what notices."""
    batches = FakeBatches(fail={"job1"})
    out = extract._call_model_batch({"a": "text-A", "b": "text-B"}, "SYS",
                                    FakeBatchClient(batches), "claude-opus-5", poll_seconds=0)
    assert out["a"] and out["b"] == []


def test_batch_gives_up_rather_than_polling_forever() -> None:
    batches = FakeBatches(statuses=("in_progress",))
    with pytest.raises(TimeoutError, match="batch_test"):
        extract._call_model_batch({"a": "text"}, "SYS", FakeBatchClient(batches),
                                  "claude-opus-5", poll_seconds=0, timeout_seconds=0.05)


def test_batch_is_opt_in_and_lint_never_uses_it() -> None:
    """`lint` and the web endpoint answer while a human waits; batch takes minutes."""
    import inspect

    assert "batch: bool = False" in inspect.signature(extract.extract_sources).__str__().replace(
        "'", ""
    ) or extract.extract_sources.__defaults__[-1] is False

    from almanac import cli

    assert "batch=batch" in inspect.getsource(cli._judged)
    assert "batch" not in inspect.getsource(cli._cmd_lint)
