import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Citation } from '../types'
import { useDocPreview } from './DocumentPreview'

// One citation: "doc:12", "doc:12 p3", "doc:12 p.3", "doc:12, p3", "doc:12 p3-4" (a range cites its first page).
const ONE_SRC = String.raw`doc:(\d+)(?:\s*,?\s*p\.?\s*(\d+)(?:\s*[-–]\s*\d+)?)?`
const ONE_NC = String.raw`doc:\d+(?:\s*,?\s*p\.?\s*\d+(?:\s*[-–]\s*\d+)?)?`
// A bracketed group of one or more citations separated by "," or ";": "[doc:12]", "[doc:12 p3; doc:14]".
const GROUP = new RegExp(String.raw`\[\s*(${ONE_NC}(?:\s*[,;]\s*${ONE_NC})*)\s*\]`, 'g')
const ONE = new RegExp(ONE_SRC, 'g')
// Fenced code blocks (``` or ~~~, possibly unterminated) and inline code spans. Markers inside them are left alone.
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g

function shortName(name: string, max = 26) {
  return name.length > max ? name.slice(0, max - 1) + '…' : name
}

function linkifySegment(md: string, byId: Map<number, Citation>): string {
  return md.replace(GROUP, (_m, inner: string) => {
    const parts: string[] = []
    for (const m of inner.matchAll(ONE)) {
      const id = Number(m[1])
      const page = m[2] ? Number(m[2]) : null
      const c = byId.get(id)
      const label = `${c?.filename ? shortName(c.filename) : `doc ${id}`}${page ? ` · p${page}` : ''}`
      const safe = label.replace(/[[\]]/g, '').replace(/([\\`*_~<>])/g, '\\$1')
      parts.push(`[${safe}](cite:${id}:${page ?? ''})`)
    }
    return ' ' + parts.join(' ')
  })
}

/** Convert citation markers into markdown links with a private `cite:` scheme rendered as chips. */
export function linkifyCitations(md: string, citations: Citation[]): string {
  const byId = new Map<number, Citation>()
  citations.forEach((c) => c && typeof c.document_id === 'number' && !byId.has(c.document_id) && byId.set(c.document_id, c))
  // split() with a capturing group alternates [text, code, text, code, ...]; only touch text segments.
  return md
    .split(CODE)
    .map((seg, i) => (i % 2 === 1 ? seg : linkifySegment(seg, byId)))
    .join('')
}

export default function CitedMarkdown({ markdown, citations = [] }: { markdown: string; citations?: Citation[] }) {
  const open = useDocPreview()
  const text = linkifyCitations(markdown || '', citations)
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Model output may echo text from prompt-injected documents: never render images
        // (an <img> URL fetches automatically and could exfiltrate data). Raw HTML is not
        // rendered either (react-markdown's default, no rehype-raw).
        disallowedElements={['img']}
        urlTransform={(url) => (url.startsWith('cite:') ? url : defaultUrlTransform(url))}
        components={{
          img: () => null,
          a: ({ href, children }) => {
            if (href && href.startsWith('cite:')) {
              const [, idStr, pageStr] = href.split(':')
              const id = Number(idStr)
              const page = pageStr ? Number(pageStr) : null
              const c = citations.find((x) => x.document_id === id && (page == null || x.page == null || x.page === page)) ||
                citations.find((x) => x.document_id === id)
              return (
                <button
                  type="button"
                  onClick={() => open({ id, page, filename: c?.filename, snippet: c?.snippet })}
                  className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-teal-200 bg-teal-50 px-1.5 py-0 align-baseline text-[11px] font-medium text-teal-800 no-underline hover:bg-teal-100"
                  title={c?.snippet || 'Open source document'}
                >
                  📎 {children}
                </button>
              )
            }
            // Only open a link when the user clicks it, in a new tab with no opener or referrer.
            return (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" referrerPolicy="no-referrer" className="text-teal-700 underline" title={href}>
                {children}
              </a>
            )
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
