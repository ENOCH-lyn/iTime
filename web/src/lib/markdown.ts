import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  html: false,
  breaks: false,
  linkify: true,
  typographer: false,
})

const defaultFence = markdown.renderer.rules.fence
if (!defaultFence) {
  throw new Error('markdown-it fence renderer is unavailable')
}
markdown.renderer.rules.fence = (
  tokens,
  index,
  options,
  environment,
  renderer,
) => (
  '<div class="markdown-code-block">'
  + '<button type="button" class="markdown-copy" data-copy-code '
  + 'aria-label="复制代码" aria-live="polite">复制</button>'
  + defaultFence(tokens, index, options, environment, renderer)
  + '</div>'
)

export function renderMarkdown(source: string) {
  const tokens = markdown.parse(source, {})
  for (const token of tokens) {
    if (!['heading_open', 'heading_close'].includes(token.type)) continue
    if (token.tag === 'h1') token.tag = 'h2'
    if (token.tag === 'h5' || token.tag === 'h6') token.tag = 'h4'
  }
  const sanitized = DOMPurify.sanitize(
    markdown.renderer.render(tokens, markdown.options, {}),
    { USE_PROFILES: { html: true } },
  )
  const template = document.createElement('template')
  template.innerHTML = sanitized
  for (const link of template.content.querySelectorAll<HTMLAnchorElement>('a')) {
    try {
      const target = new URL(link.href, window.location.href)
      if (!['http:', 'https:'].includes(target.protocol)) continue
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
    } catch {
      link.removeAttribute('href')
    }
  }
  return template.innerHTML
}
