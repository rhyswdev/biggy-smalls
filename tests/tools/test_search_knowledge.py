"""`search_knowledge`, the source inventory and the retrieval check (build rows D2 and D8; section 6.4 of
docs/decisions/tools-agent-build.md): no query text can raise a syntax error, a close note is found by a
word that appears only in it, and the retrieval check runs."""
import json
import re
from collections import Counter

import pytest

import golden
import parity
import test_leak
from eval.retrieval import run as retrieval
from smalls.tools import inventory, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
NASTY = ['timeout-ms=8000', '"unbalanced quote', "AND OR NOT", "NEAR(a b, 3)", "*", "col:term", "a OR (b", "'; DROP TABLE chunk; --",
         "pool^2 -exhausted +timeout", "{curly} [square] <angle>", "ünïcödé naïve café 連接", "🙂 🔥", "!!! ??? ...", "a" * 300, "x AND", "\\", "\x00\x01"]


@pytest.mark.parametrize("query", NASTY, ids=[repr(q)[:24] for q in NASTY])
def test_no_query_text_can_raise_a_syntax_error(query):
    ctx = golden.ctx()
    env = golden.run("search_knowledge", ctx, query=query)
    assert env.status == "ok", env.text
    assert env.meta["empty"] or env.rows
    assert env.text.splitlines()[-1].startswith("[FTS5] ")


def chunks(ctx) -> list[tuple]:
    return ctx.store.conn.execute("SELECT v.handle, v.kind, vc.heading, vc.text FROM visible_chunk vc JOIN visible v ON v.rid = vc.rid").fetchall()


def only_in(ctx, text: str) -> str | None:
    """A word of `text` that the index finds in exactly one visible passage. The index stems its words, so
    counting spellings would not do: the index is asked."""
    for word in sorted(set(re.findall(r"[a-z]{7,}", text.lower()))):
        hits = ctx.store.conn.execute("SELECT count(*) FROM chunk_fts JOIN visible_chunk vc ON vc.chunk_id = chunk_fts.rowid WHERE chunk_fts MATCH ?", (f'"{word}"',))
        if hits.fetchone()[0] == 1:
            return word
    return None


def words(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_close_note_is_found_by_a_word_that_appears_only_in_it(checkpoint):
    ctx = golden.ctx(checkpoint)
    checked = 0
    for handle, kind, heading, text in chunks(ctx):
        word = only_in(ctx, text) if kind == "incident" else None
        if not word:
            continue
        env = golden.run("search_knowledge", ctx, query=word)
        assert env.status == "ok" and env.shown == (handle,) and env.meta["total"] == 1
        row = env.rows[0].text
        assert row.startswith(f"{handle}  [1] ") and "  close note  updated " in row
        body = row.split("\n", 1)[1].replace("\n    ", "\n").strip()
        assert body == text.strip() and "[…" not in row                           # the whole close note, never a snippet
        assert re.search(r"Close code: .+", text) and "Close code:" in row
        checked += 1
    assert checked >= 3


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_hits_are_ranked_by_bm25_over_the_quoted_words_joined_with_or(checkpoint):
    ctx = golden.ctx(checkpoint)
    query = "rotate the certificate before it expires"
    match = " OR ".join(f'"{w}"' for w in re.findall(r"\w+", query))
    want = ctx.store.conn.execute("SELECT v.handle FROM chunk_fts JOIN visible_chunk vc ON vc.chunk_id = chunk_fts.rowid JOIN visible v ON v.rid = vc.rid "
                                  "WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts), vc.chunk_id", (match,)).fetchall()
    env = golden.run("search_knowledge", ctx, query=query)
    assert golden.handles(env) == [h for (h,) in want[:5]] and env.meta["total"] == len(want) > 5
    assert env.native_query == match and env.text.splitlines()[-1] == f"[FTS5] {match}"
    assert [int(re.search(r"\[(\d)\]", row.text).group(1)) for row in env.rows] == [1, 2, 3, 4, 5]
    one = golden.run("search_knowledge", ctx, query=query, k=1)
    assert golden.handles(one) == golden.handles(env)[:1] and f"[showing 1 of {len(want)} passages: the lower-ranked {len(want) - 1} not shown." in one.text
    assert "read past the top hit" in env.text
    assert golden.run("search_knowledge", ctx, query=query, k=6).meta["error"] == "arguments"
    assert golden.run("search_knowledge", ctx, query="").meta["error"] == "arguments"


def test_two_hits_on_one_page_are_two_rows_with_one_handle_and_a_section_is_a_marked_snippet():
    ctx = golden.ctx()
    page, n = ctx.store.conn.execute("SELECT v.handle, count(*) FROM visible_chunk vc JOIN visible v ON v.rid = vc.rid WHERE v.kind = 'doc' GROUP BY 1 ORDER BY 2 DESC").fetchone()
    title = ctx.store.conn.execute("SELECT title FROM visible WHERE handle = ?", (page,)).fetchone()[0]
    env = golden.run("search_knowledge", ctx, query=title)
    mine = [row for row in env.rows if row.handle == page]
    assert n > 2 and len(mine) >= 2 and env.shown.count(page) == 1 and len(env.rows) == 5      # two rows, one citable handle
    assert all("  page section  updated " in row.text for row in mine) and env.referenced == ()
    # A long section is shown as a snippet around the matched word, marked, and the record tool has the rest.
    handle, section, word = next((h, t, only_in(ctx, t[650:])) for h, kind, _, t in sorted(chunks(ctx), key=lambda c: -len(c[3]))
                                 if kind == "doc" and len(t) > 700 and only_in(ctx, t[650:]))
    found = golden.run("search_knowledge", ctx, query=word)
    assert found.shown == (handle,) and "get_record <handle> returns the whole record" in found.text
    snippet = found.rows[0].text.split("\n", 1)[1]
    assert word in snippet.lower() and snippet.lstrip().startswith("[…] ") and len(words(snippet)) < len(words(section))
    whole = golden.run("get_record", ctx, handle=handle)
    assert "part 1 of" not in whole.text and words(section.split("\n")[0]) in words(whole.text)      # nothing is lost


def test_a_passage_is_searchable_exactly_when_its_record_is_visible():
    late = golden.ctx(golden.SECOND)
    rows = late.store.conn.execute("SELECT v.handle, v.t_known, vc.text FROM visible_chunk vc JOIN visible v ON v.rid = vc.rid WHERE v.kind = 'note' ORDER BY v.t_known DESC").fetchall()
    handle, known, text = rows[0]
    word = only_in(late, text)
    assert word and golden.run("search_knowledge", late, query=word).shown == (handle,)
    before = golden.ctx(windows.iso(known - 60_000))
    assert handle not in golden.run("search_knowledge", before, query=word).shown
    for checkpoint in CHECKPOINTS:
        ctx = golden.ctx(checkpoint)
        env = golden.run("search_knowledge", ctx, query="what changed and what failed")
        assert max(test_leak.times_in(env.text, test_leak.UTC), default=0) < ctx.store.now
        assert all(row.t_known is None or parity.ms(row.t_known) < ctx.store.now for row in env.rows)


# -- the retrieval check (row D8) --------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_retrieval_check_runs_and_reports_recall_and_mrr(checkpoint):
    queries = retrieval.load_queries()
    assert 10 <= len(queries) <= 14 and all(q["query"].strip() and q["expect"] for q in queries)
    ctx = golden.ctx(checkpoint)
    visible = {h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible")}
    assert all(set(q["expect"]) <= visible for q in queries)                      # every expected document exists and is visible
    result = retrieval.check(ctx, queries)
    assert result["queries"] == len(queries) and all(row["status"] == "ok" for row in result["rows"])
    assert 0.5 <= result["recall_at_1"] <= 1.0 and result["recall_at_1"] <= result["mrr"] <= 1.0      # a sanity check, not a benchmark
    assert result["mrr"] == round(sum(1 / r["rank"] for r in result["rows"] if r["rank"]) / len(queries), 3)
    # Joining the words with AND would have returned nothing for most natural-language questions.
    assert result["empty_if_every_word_had_to_match"] >= len(queries) * 3 // 4


def test_the_retrieval_check_has_a_command(capsys):
    assert retrieval.main(["--bundle", str(parity.BUNDLE), "--now", golden.CHECKPOINTS[golden.SECOND], "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {"queries", "recall_at_1", "mrr", "not_in_top_5", "rows"} <= set(result)
    assert retrieval.main(["--bundle", str(parity.BUNDLE), "--now", golden.CHECKPOINTS[golden.SECOND]]) == 0
    assert "recall@1" in capsys.readouterr().out


# -- the source inventory ----------------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_inventory_lists_what_exists_from_visible_rows_only(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    env = inventory.build(ctx)
    text = env.text
    docs = sorted(r["handle"] for r in golden.visible(now, "confluence/"))
    assert docs and all(any(line.strip().startswith(h + "  ") for line in text.splitlines()) for h in docs)
    assert f"Knowledge documents ({len(docs)}):" in text
    series = {h for (h,) in ctx.store.conn.execute("SELECT DISTINCT handle FROM visible_point")}
    assert series and all(h in text for h in series) and f"Metric series ({len(series)}):" in text
    for search, fired in ctx.store.conn.execute("SELECT s.handle, (SELECT count(DISTINCT a.stream_key) FROM visible a WHERE a.parent = s.handle) FROM visible s "
                                                "WHERE s.kind = 'alert_def' AND s.stream_key IS NULL"):
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(search + "  "))
        assert line.endswith(f"fired {fired} times")
    channels = [c for (c,) in ctx.store.conn.execute("SELECT DISTINCT dataset FROM visible WHERE kind = 'message' ORDER BY 1")]
    assert f"Chat channels: {', '.join(channels)}" in text
    for sourcetype, n in ctx.store.conn.execute("SELECT json_extract(attrs, '$.sourcetype'), count(*) FROM visible WHERE kind = 'log' GROUP BY 1"):
        assert f"  {sourcetype}: {n} records; hosts: " in text
    by_source: dict[str, list[int]] = {}
    for source, known in ctx.store.conn.execute("SELECT source, t_known FROM visible"):
        if known > 0:                                                             # a record with no known time has no place on a time line
            by_source.setdefault(source, []).append(known)
    coverage = text.split("Time coverage, first to last visible record")[1].split("\n\n")[0].splitlines()
    assert coverage[0] == f" ({len(by_source)}):" and len(coverage) == len(by_source) + 1
    for source, times in by_source.items():
        assert f"  {source}: {windows.iso(min(times))} to {windows.iso(max(times))} ({len(times)} records)" in coverage, source
    newest = {}
    for row in golden.visible(now):                                               # and against the manifest: no source's last time is later than its newest visible record
        source = next(s for s in sorted(by_source, key=len, reverse=True) if row["path"] == s or row["path"].startswith(s + "/") or row["path"].startswith(s + "."))
        newest[source] = max(newest.get(source, 0), parity.ms(row["t_known"]))
    assert all(max(times) <= newest[source] for source, times in by_source.items())
    assert set(env.referenced) == set(docs) | series | {h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE kind = 'alert_def' AND stream_key IS NULL")}
    assert env.rows == () and len(text) // 4 <= 2000                               # it names things; it shows no record's content
    assert max(test_leak.times_in(text, test_leak.UTC), default=0) < now


def test_a_metric_is_listed_only_when_it_has_a_visible_point():
    ctx = golden.ctx()
    first = ctx.store.conn.execute("SELECT min(t_known) FROM visible_point").fetchone()[0]
    early = golden.ctx(windows.iso(first - 60_000))
    assert "Metric series" not in inventory.build(early).text and not [h for h in inventory.build(early).referenced if h.startswith("metric:")]
    assert "Metric series (" in inventory.build(golden.ctx(windows.iso(first + 3_600_000))).text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_with_tags_on_the_inventory_counts_each_facet_with_the_untagged_beside_it(checkpoint):
    off, on = inventory.build(golden.ctx(checkpoint, tags=False)), inventory.build(golden.ctx(checkpoint, tags=True))
    assert "tag" not in off.text.lower() and "Tags, a search aid and never evidence" in on.text
    plain = re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", on.text.split("Tags, a search aid")[0]).rstrip("\n")
    assert plain == off.text and "[tag signal=" in on.text                         # the labels and the tag block are the only difference
    ctx = golden.ctx(checkpoint, tags=True)
    for facet, kinds in inventory.FACET_KINDS.items():
        marks = ", ".join("?" * len(kinds))
        rows = [json.loads(t).get(facet) for (t,) in ctx.store.conn.execute(
            f"SELECT tags FROM visible WHERE kind IN ({marks}) AND (tag_key IS NOT NULL OR tags != '{{}}')", kinds)]
        values = Counter(v for value in rows if value for v in (value if isinstance(value, list) else [value]))
        line = next(ln for ln in on.text.splitlines() if ln.strip().startswith(f"{facet} over {len(rows)} rows: "))
        assert line.endswith(f"; untagged {sum(1 for value in rows if not value)}")
        for value, n in values.items():
            assert re.search(rf"\b{value} {n}\b", line), (facet, value)
    assert "untagged" in on.text and len(on.text) // 4 <= 2000


def test_past_twenty_entries_of_a_kind_the_inventory_prints_the_count_and_the_first_twenty():
    names = [f"host-{n:02d}" for n in range(26)]
    assert inventory.listed(names[:20]) == ", ".join(names[:20])
    assert inventory.listed(names) == "26 in all; the first 20: " + ", ".join(names[:20])
    rows = inventory.block("Things", [f"thing {n}" for n in range(23)])
    assert rows[0] == "Things (23):" and len(rows) == 22 and rows[-1] == "  (3 more are not listed)" and inventory.block("Nothing", []) == []
