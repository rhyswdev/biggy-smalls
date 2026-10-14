// Q7: the markdown the model writes in its prose is shown as formatting, from a small subset, and never as HTML.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Inline, Prose } from "@/components/chat/Prose";
import { blocks, leadSentence, runs } from "@/lib/chat/prose";

const html = (text: string, onOpen?: (h: string) => void) => renderToStaticMarkup(<Prose text={text} onOpen={onOpen} />);

describe("the prose subset", () => {
  it("reads paragraphs at blank lines and keeps a single line break inside one", () => {
    expect(blocks("First line\nsecond line\n\nNext paragraph").map((b) => b.kind)).toEqual(["p", "p"]);
    expect(html("First line\nsecond line")).toBe('<p class="narr">First line<br/>second line</p>');
  });

  it("reads bullet and numbered lists, which may follow a paragraph line directly, and keeps the first number", () => {
    const b = blocks("Three things:\n- one\n* two\n+ three\n\n4. four\n5) five");
    expect(b.map((x) => x.kind)).toEqual(["p", "ul", "ol"]);
    expect(html("- one\n- two")).toBe('<ul class="narr"><li>one</li><li>two</li></ul>');
    expect(html("4. four\n5. five")).toBe('<ol class="narr" start="4"><li>four</li><li>five</li></ol>');
  });

  it("joins a plain line after a list item to that item, and drops a rule", () => {
    expect(html("- one\n  and more\n\n---\n\nAfter")).toBe('<ul class="narr"><li>one<br/>and more</li></ul><p class="narr">After</p>');
  });

  it("shows a heading as a bold line, not a heading in the page's outline", () => {
    expect(html("## What changed")).toBe('<p class="narr"><strong>What changed</strong></p>');
  });

  it("reads bold and inline code inside a line, and code inside bold", () => {
    expect(runs("a **b** c `d`")).toEqual([{ text: "a " }, { text: "b", bold: true }, { text: " c " }, { text: "d", code: true }]);
    expect(runs("**use `x` now**")).toEqual([{ text: "use ", bold: true }, { text: "x", code: true, bold: true }, { text: " now", bold: true }]);
  });

  it("leaves what it does not read as the characters written", () => {
    for (const t of ["an *aside* here", "a ** b ** c", "unclosed **bold", "a | table | row", "snake_case_name"]) {
      expect(html(t)).toBe(`<p class="narr">${t}</p>`);
    }
  });

  it("never renders HTML from the text", () => {
    const out = html('<img src=x onerror="alert(1)"> and **<b>bold</b>**');
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>");
    expect(out).toContain("&lt;img");
    expect(out).toContain("<strong>&lt;b&gt;bold&lt;/b&gt;</strong>");
  });

  it("makes handles buttons when there is a drawer, inside bold and code too, and plain text when there is none", () => {
    const text = "Errors rose **after log:web-01/0a1b2c3d** and `conf:KB0000001` says why.";
    const linked = html(text, () => {});
    expect(linked.match(/<button/g)).toHaveLength(2);
    expect(linked).toContain("<strong>after <button");
    expect(linked).toContain("<code><button");
    expect(html(text)).not.toContain("<button");
  });
});

describe("the lead sentence of a summary", () => {
  it("ends at the first sentence outside bold and drops bold around the whole of it", () => {
    expect(leadSentence("**The queue backed up. Workers stalled.** Then jobs failed.\n\n- one")).toEqual(["The queue backed up. Workers stalled.", "Then jobs failed.\n\n- one"]);
    expect(leadSentence("The cause is **a full disk.** Then jobs failed.")).toEqual(["The cause is **a full disk.**", "Then jobs failed."]);
  });

  it("stays inside the first paragraph, which a list ends", () => {
    expect(leadSentence("No sentence end here\n- one\n- two")).toEqual(["No sentence end here", "- one\n- two"]);
    expect(leadSentence("Plain text with no stop")).toEqual(["Plain text with no stop", ""]);
  });

  it("gives no lead when the summary opens with a list or a heading", () => {
    expect(leadSentence("- one\n- two")).toEqual(["", "- one\n- two"]);
    expect(leadSentence("## Summary\nText.")).toEqual(["", "## Summary\nText."]);
  });

  it("is shown inline, with its bold and its handles", () => {
    expect(renderToStaticMarkup(<Inline text="See **log:web-01/0a1b2c3d**." onOpen={() => {}} />)).toMatch(/^See <strong><button[^>]*>.*<\/button><\/strong>\.$/);
  });
});
