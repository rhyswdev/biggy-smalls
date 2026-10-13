"""The result envelope and the handle classes (docs/decisions/tools-agent-build.md section 5; build row A1)."""
import dataclasses

import pytest

from smalls.contracts.envelope import META_DEFAULTS, Envelope, Row
from smalls.contracts.handles import handle_class, ic_note_handle, metric_handle, query_handle


def test_shown_is_derived_from_rows_so_only_shown_content_is_citable():
    env = Envelope("text", rows=[Row("conf:A", "first passage", "2026-01-01T00:00:00.000Z"), Row("conf:B", "b"), Row("conf:A", "second passage")],
                   referenced=["jk:job#1/abcdef01"])
    assert env.shown == ("conf:A", "conf:B")                # two rows of one page are one citable handle
    assert env.referenced == ("jk:job#1/abcdef01",) and "jk:job#1/abcdef01" not in env.shown
    assert not hasattr(Envelope, "handles")
    assert "shown" not in {f.name for f in dataclasses.fields(Envelope)}


def test_meta_always_carries_the_seven_keys():
    assert set(Envelope("x").meta) == set(META_DEFAULTS) == {"total", "returned", "truncated", "clamped", "empty", "sampled", "tag"}
    env = Envelope("x", meta={"total": 12, "truncated": True, "tag": "signal=errors"})
    assert env.meta["total"] == 12 and env.meta["truncated"] is True and env.meta["returned"] == 0 and env.meta["tag"] == "signal=errors"
    with pytest.raises(TypeError):
        env.meta["total"] = 0


def test_an_envelope_is_frozen_and_serialises():
    env = Envelope("x", rows=[Row("q:0a1b2c3d", "table")], window=["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"], status="error")
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.text = "y"
    d = env.as_dict()
    assert d["shown"] == ["q:0a1b2c3d"] and d["window"] == ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"] and d["status"] == "error"
    assert d["rows"] == [{"handle": "q:0a1b2c3d", "text": "table", "t_known": None}]
    assert isinstance(env.window, tuple) and isinstance(env.rows, tuple)


def test_handle_class_is_the_one_definition_of_what_counts_as_chat():
    assert handle_class("slack:ops/1a2b3c4d") == "chat"
    assert handle_class(ic_note_handle("run", "2026-01-01T00:00:00Z", "try the other pool")) == "chat"
    assert handle_class(query_handle("search_logs|{}", "2026-01-01T00:00:00Z")) == "aggregate"
    assert handle_class(metric_handle("avg:system.load.1{*}")) == handle_class(metric_handle("avg:system.load.1{*}", "1400", "1452")) == "metric"
    # A journal entry and a paging note are records, not chat (open point P4).
    for handle in ("snow:jn/0a1b2c3d", "pd:log/Q1ABC", "log:worker-01/0a1b2c3d", "jk:job#1", "snow:INC0000001", "dd:ev1", "k8s:0a1b2c3d", "conf:X"):
        assert handle_class(handle) == "record", handle
