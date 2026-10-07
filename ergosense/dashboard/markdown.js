// Minimal Markdown -> HTML renderer for the safety report (no external libraries, works offline).
// Supports headings, paragraphs, bullet/numbered lists, tables, blockquotes, bold, italics,
// inline code and highlights citation tags like [rotation_guidance#2].
(function () {
  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inline(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([a-z0-9_]+#\d+)\]/g, '<span class="cite">$1</span>')
      .replace(/\[citation removed: ([^\]]+)\]/g, '<span class="cite bad" title="not in retrieved context">$1</span>');
  }

  function render(md) {
    const lines = md.replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }

      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length, 3);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i++; continue;
      }
      if (/^---+\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

      if (line.trim().startsWith("|")) {                       // table
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(lines[i].trim()); i++; }
        const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r) => !/^\|[\s:|-]+\|$/.test(r));
        let html = "<table>";
        body.forEach((row, n) => {
          const tag = n === 0 ? "th" : "td";
          html += "<tr>" + cells(row).map((c) => `<${tag}>${inline(c)}</${tag}>`).join("") + "</tr>";
        });
        out.push(html + "</table>");
        continue;
      }

      if (/^\s*([-*]|\d+\.)\s+/.test(line)) {                  // list
        const ordered = /^\s*\d+\./.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          let item = lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "");
          i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
            item += " " + lines[i].trim(); i++;                  // wrapped continuation line
          }
          items.push(`<li>${inline(item)}</li>`);
        }
        out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
        continue;
      }

      if (line.startsWith(">")) {
        const quote = [];
        while (i < lines.length && lines[i].startsWith(">")) { quote.push(lines[i].replace(/^>\s?/, "")); i++; }
        out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
        continue;
      }

      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\||>|\s*([-*]|\d+\.)\s)/.test(lines[i])) {
        para.push(lines[i].trim()); i++;
      }
      out.push(`<p>${inline(para.join(" "))}</p>`);
    }
    return out.join("\n");
  }

  window.renderMarkdown = render;
})();
