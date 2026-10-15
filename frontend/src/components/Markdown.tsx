// Tiny, dependency-free markdown renderer. Input is HTML-escaped first, so output is safe to inject.
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}

export function renderMarkdown(src: string): string {
  const blocks = esc(src).split(/```/);
  return blocks.map((chunk, i) => {
    if (i % 2 === 1) return `<pre><code>${chunk.replace(/^[\w-]*\n/, "")}</code></pre>`;
    const out: string[] = [];
    let list: "ul" | "ol" | null = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const line of chunk.split("\n")) {
      const ul = /^\s*[-*]\s+(.*)/.exec(line), ol = /^\s*\d+[.)]\s+(.*)/.exec(line);
      if (ul) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(ul[1])}</li>`); }
      else if (ol) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(ol[1])}</li>`); }
      else { close(); if (line.trim() === "---") out.push("<hr/>"); else if (line.trim()) out.push(`<p>${inline(line)}</p>`); }
    }
    close();
    return out.join("");
  }).join("");
}

export default function Markdown({ text }: { text: string }) {
  return <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
}
