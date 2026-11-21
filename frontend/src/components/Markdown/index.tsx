import { useEffect, useMemo, useRef, useState } from 'react'
import { message as antdMessage } from 'antd'

import './index.css'

/**
 * Markdown 渲染组件（零依赖）。
 *
 * 为什么自己写而不装 react-markdown：
 *   1. 我们只需要一个很小的子集（标题、列表、粗斜体、行内代码、代码块、链接、表格、引用）
 *   2. 依赖越少，答辩时越能说清"这行代码是干什么的"
 *   3. **渲染成 React 元素而不是 innerHTML** —— 天然没有 XSS 面，
 *      这点比自己写解析器更重要：模型输出是不可信内容
 *
 * 流式友好：解析器对"还没写完"的文本是容错的。
 * 比如只有一个 ``` 开头没有结尾，也会当成代码块渲染，而不是显示成乱码。
 */

// ---------------------------------------------------------------------------
// 行内解析
// ---------------------------------------------------------------------------
type InlineToken =
  | { type: 'text'; value: string }
  | { type: 'code'; value: string }
  | { type: 'bold'; value: string }
  | { type: 'italic'; value: string }
  | { type: 'link'; value: string; href: string }

/** 行内语法：`code` > **bold** > *italic* > [text](url) */
function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let buffer = ''
  let index = 0

  const pushText = () => {
    if (buffer) {
      tokens.push({ type: 'text', value: buffer })
      buffer = ''
    }
  }

  while (index < text.length) {
    const rest = text.slice(index)

    // 行内代码
    const codeMatch = /^`([^`]+)`/.exec(rest)
    if (codeMatch) {
      pushText()
      tokens.push({ type: 'code', value: codeMatch[1] })
      index += codeMatch[0].length
      continue
    }

    // 链接
    const linkMatch = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest)
    if (linkMatch) {
      pushText()
      tokens.push({ type: 'link', value: linkMatch[1], href: linkMatch[2] })
      index += linkMatch[0].length
      continue
    }

    // 粗体
    const boldMatch = /^\*\*([^*]+)\*\*/.exec(rest)
    if (boldMatch) {
      pushText()
      tokens.push({ type: 'bold', value: boldMatch[1] })
      index += boldMatch[0].length
      continue
    }

    // 斜体
    const italicMatch = /^\*([^*]+)\*/.exec(rest)
    if (italicMatch) {
      pushText()
      tokens.push({ type: 'italic', value: italicMatch[1] })
      index += italicMatch[0].length
      continue
    }

    buffer += text[index]
    index += 1
  }

  pushText()
  return tokens
}

function renderInline(text: string): React.ReactNode[] {
  return parseInline(text).map((token, index) => {
    switch (token.type) {
      case 'code':
        return (
          <code key={index} className="md-code">
            {token.value}
          </code>
        )
      case 'bold':
        return <strong key={index}>{token.value}</strong>
      case 'italic':
        return <em key={index}>{token.value}</em>
      case 'link':
        return (
          <a key={index} href={token.href} target="_blank" rel="noreferrer">
            {token.value}
          </a>
        )
      default:
        return <span key={index}>{token.value}</span>
    }
  })
}

// ---------------------------------------------------------------------------
// 块级解析
// ---------------------------------------------------------------------------
type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'code'; lang: string; code: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'quote'; text: string }
  | { type: 'table'; header: string[]; rows: string[][] }
  | { type: 'hr' }

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim())
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    // 空行
    if (!line.trim()) {
      index += 1
      continue
    }

    // 代码块（含未闭合的情况 —— 流式输出时很常见）
    if (line.trimStart().startsWith('```')) {
      const lang = line.trim().slice(3).trim()
      const body: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trimStart().startsWith('```')) {
        body.push(lines[index])
        index += 1
      }
      index += 1 // 跳过结束的 ```
      blocks.push({ type: 'code', lang, code: body.join('\n') })
      continue
    }

    // 分割线
    if (/^(\s*)(---+|\*\*\*+|___+)\s*$/.test(line)) {
      blocks.push({ type: 'hr' })
      index += 1
      continue
    }

    // 标题
    const headingMatch = /^(#{1,6})\s+(.*)$/.exec(line)
    if (headingMatch) {
      blocks.push({ type: 'heading', level: headingMatch[1].length, text: headingMatch[2] })
      index += 1
      continue
    }

    // 引用
    if (line.trimStart().startsWith('>')) {
      const body: string[] = []
      while (index < lines.length && lines[index].trimStart().startsWith('>')) {
        body.push(lines[index].trimStart().replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push({ type: 'quote', text: body.join('\n') })
      continue
    }

    // 表格：表头 + 分隔行
    if (
      line.includes('|') &&
      index + 1 < lines.length &&
      /^[\s|:-]+$/.test(lines[index + 1]) &&
      lines[index + 1].includes('-')
    ) {
      const header = splitRow(line)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].includes('|')) {
        rows.push(splitRow(lines[index]))
        index += 1
      }
      blocks.push({ type: 'table', header, rows })
      continue
    }

    // 有序/无序列表
    const listMatch = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(line)
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[2])
      const items: string[] = []
      while (index < lines.length) {
        const itemMatch = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(lines[index])
        if (!itemMatch) {
          break
        }
        items.push(itemMatch[3])
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }

    // 普通段落：连续非空行合并
    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].trimStart().startsWith('```') &&
      !/^(#{1,6})\s+/.test(lines[index]) &&
      !/^(\s*)([-*+]|\d+\.)\s+/.test(lines[index]) &&
      !lines[index].trimStart().startsWith('>')
    ) {
      paragraph.push(lines[index])
      index += 1
    }
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') })
  }

  return blocks
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
interface MarkdownProps {
  content: string
  /** 是否还有内容在流式输出（决定是否显示光标） */
  streaming?: boolean
}

export function Markdown({ content, streaming = false }: MarkdownProps) {
  const blocks = useMemo(() => parseBlocks(content), [content])

  return (
    <div className="md">
      {blocks.map((block, index) => {
        switch (block.type) {
          case 'heading': {
            const Tag = `h${Math.min(block.level + 2, 6)}` as 'h3' | 'h4' | 'h5' | 'h6'
            return (
              <Tag key={index} className="md-h">
                {renderInline(block.text)}
              </Tag>
            )
          }
          case 'paragraph':
            return (
              <p key={index} className="md-p">
                {renderInline(block.text)}
              </p>
            )
          case 'code':
            return <CodeBlock key={index} lang={block.lang} code={block.code} />
          case 'list': {
            const ListTag = block.ordered ? 'ol' : 'ul'
            return (
              <ListTag key={index} className="md-list">
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{renderInline(item)}</li>
                ))}
              </ListTag>
            )
          }
          case 'quote':
            return (
              <blockquote key={index} className="md-quote">
                {renderInline(block.text)}
              </blockquote>
            )
          case 'table':
            return (
              <div key={index} className="md-table-wrap">
                <table className="md-table">
                  <thead>
                    <tr>
                      {block.header.map((cell, cellIndex) => (
                        <th key={cellIndex}>{renderInline(cell)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex}>{renderInline(cell)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          default:
            return <hr key={index} className="md-hr" />
        }
      })}

      {streaming && <span className="md-caret" />}
    </div>
  )
}

// ---------------------------------------------------------------------------
function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (timer.current) {
        window.clearTimeout(timer.current)
      }
    }
  }, [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      timer.current = window.setTimeout(() => setCopied(false), 1500)
    } catch {
      antdMessage.warning('复制失败，请手动选择')
    }
  }

  return (
    <div className="md-codeblock">
      <div className="md-codeblock__head">
        <span className="md-codeblock__lang">{lang || 'text'}</span>
        <button type="button" className="md-codeblock__copy" onClick={copy}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="md-codeblock__body">
        <code>{code}</code>
      </pre>
    </div>
  )
}
