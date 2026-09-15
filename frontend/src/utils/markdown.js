function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function renderInline(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
}

export function renderBasicMarkdown(markdown) {
  const lines = String(markdown ?? '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
  const rendered = []
  let paragraphLines = []
  let listType = null
  let codeLines = null
  let codeLanguage = ''

  function flushParagraph() {
    if (!paragraphLines.length) {
      return
    }
    rendered.push(
      `<p>${paragraphLines.map(renderInline).join('<br>')}</p>`,
    )
    paragraphLines = []
  }

  function closeList() {
    if (!listType) {
      return
    }
    rendered.push(`</${listType}>`)
    listType = null
  }

  function flushCodeBlock() {
    const languageAttribute = codeLanguage
      ? ` class="language-${codeLanguage}"`
      : ''
    rendered.push(
      `<pre><code${languageAttribute}>${escapeHtml(codeLines.join('\n'))}</code></pre>`,
    )
    codeLines = null
    codeLanguage = ''
  }

  for (const line of lines) {
    if (codeLines) {
      if (/^```\s*$/.test(line)) {
        flushCodeBlock()
      } else {
        codeLines.push(line)
      }
      continue
    }

    const codeFence = line.match(/^```([A-Za-z0-9_+-]*)\s*$/)
    if (codeFence) {
      flushParagraph()
      closeList()
      codeLines = []
      codeLanguage = codeFence[1]
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      closeList()
      const level = heading[1].length
      rendered.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    const listItem = line.match(/^\s*([-*+]|\d+\.)\s+(.+)$/)
    if (listItem) {
      flushParagraph()
      const nextListType = /\d+\./.test(listItem[1]) ? 'ol' : 'ul'
      if (listType !== nextListType) {
        closeList()
        listType = nextListType
        rendered.push(`<${listType}>`)
      }
      rendered.push(`<li>${renderInline(listItem[2])}</li>`)
      continue
    }

    if (!line.trim()) {
      flushParagraph()
      closeList()
      continue
    }

    closeList()
    paragraphLines.push(line)
  }

  flushParagraph()
  closeList()
  if (codeLines) {
    flushCodeBlock()
  }
  return rendered.join('\n')
}
