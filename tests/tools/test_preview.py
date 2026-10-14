"""Seam S7 (docs/decisions/ui-build.md section 8): a read card shows the native query and the resolved window
before the read runs. For every call of the leak test's call set, what a tool says it would echo is what it
echoes when it runs; a tool with no preview gives the window alone, and only when it has one."""

from __future__ import annotations

import re

import pytest

import golden
import test_leak_tools
from smalls.tools import registry, windows


@pytest.mark.parametrize("tags", [False, True])
def test_a_preview_is_what_the_run_echoes(tags):
    ctx = golden.ctx(golden.SECOND, tags)
    tools = registry.surface(tags=tags)
    compared = 0
    for name, calls in test_leak_tools.list_calls(ctx).items():
        for args in calls:
            seen = registry.preview(tools[name], args, ctx)
            env = registry.call(tools[name], args, ctx)
            if env.status != "ok":
                continue
            if tools[name].preview is None:
                assert seen is None or seen["native_query"] is None, (name, args)
                continue
            echoed = env.native_query
            if name == "query_metrics":         # the rollup a run chose depends on what the window held
                echoed = re.sub(r"\.rollup\([^)]*\)", "", echoed)
            empty_tag_count = env.meta.get("empty") and (args.get("group_by") == "signal" or args.get("signal"))
            assert seen["native_query"] == echoed or (empty_tag_count and seen["native_query"].startswith(echoed)), (name, args)
            assert seen["window"] == (list(env.window) if env.window else None), (name, args)
            compared += 1
    assert compared > 50


def test_a_preview_never_raises_and_says_nothing_it_cannot():
    ctx = golden.ctx()
    tools = registry.surface(tags=False)
    assert registry.preview(tools["search_logs"], {"since": "yesterday"}, ctx) is None          # a window that cannot be made
    assert registry.preview(tools["search_logs"], {"limit": -1}, ctx) is None                    # arguments that do not validate
    assert registry.preview(tools["get_record"], {"handle": "snow:INC0000001"}, ctx) is None     # nothing to show before it runs
    seen = registry.preview(tools["list_changes"], {"since": "-2h"}, ctx)
    assert seen["native_query"].startswith("(no single native query") and seen["window"][1] == windows.iso(ctx.store.now)
