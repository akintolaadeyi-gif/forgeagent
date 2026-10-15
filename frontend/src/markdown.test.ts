import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./components/Markdown";

describe("renderMarkdown", () => {
  it("escapes HTML so model/tool output can never inject markup", () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)"> <script>alert(2)</script>');
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;script&gt;");
  });
  it("renders code fences verbatim (escaped) and inline formatting", () => {
    const html = renderMarkdown("Use `x < y` and **bold**\n```python\nprint('<b>')\n```");
    expect(html).toContain("<code>x &lt; y</code>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<pre><code>print('&lt;b&gt;')\n</code></pre>");
  });
  it("renders lists", () => {
    expect(renderMarkdown("- a\n- b")).toBe("<ul><li>a</li><li>b</li></ul>");
    expect(renderMarkdown("1. a\n2. b")).toBe("<ol><li>a</li><li>b</li></ol>");
  });
});
