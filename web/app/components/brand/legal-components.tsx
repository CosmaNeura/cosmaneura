import type { ReactNode } from "react"

interface LegalCalloutProps {
  children: ReactNode
}

export function LegalCallout({ children }: LegalCalloutProps) {
  return (
    <div
      className="my-5 rounded-sm px-[22px] py-[18px] text-[15px]"
      style={{
        background: "#FFFDF6",
        border: "1px solid var(--brand-rule)",
        borderLeft: "4px solid var(--brand-gold)",
      }}
    >
      {children}
    </div>
  )
}

interface LegalSectionProps {
  id: string
  num: string
  title: string
  children: ReactNode
}

export function LegalSection({ id, num, title, children }: LegalSectionProps) {
  return (
    <section>
      <h2
        id={id}
        className="mt-12 mb-1 text-[30px] leading-snug font-semibold"
        style={{
          fontFamily: "'Cormorant Garamond', serif",
          color: "var(--brand-navy-deep)",
        }}
      >
        <span
          className="mb-2 block font-sans text-[11px] font-semibold tracking-[3px] uppercase"
          style={{
            color: "var(--brand-gold)",
            fontFamily: "Inter, sans-serif",
          }}
        >
          {num}
        </span>
        {title}
      </h2>
      {children}
    </section>
  )
}

interface LegalSubheadingProps {
  children: ReactNode
}

export function LegalSubheading({ children }: LegalSubheadingProps) {
  return (
    <h3
      className="mt-7 mb-2 text-[17px] font-semibold"
      style={{
        color: "var(--brand-navy-deep)",
        fontFamily: "Inter, sans-serif",
      }}
    >
      {children}
    </h3>
  )
}

interface LegalTableProps {
  headers: string[]
  rows: string[][]
}

export function LegalTable({ headers, rows }: LegalTableProps) {
  return (
    <div className="my-[18px] overflow-x-auto">
      <table className="w-full border-collapse text-[14.5px]">
        <thead>
          <tr>
            {headers.map((h) => (
              <th
                key={h}
                className="border-b border-[var(--brand-rule)] px-[14px] py-3 text-left text-[13px] font-semibold tracking-[1px] uppercase"
                style={{
                  background: "#FFFDF6",
                  color: "var(--brand-navy-deep)",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td
                  key={j}
                  className="border-b border-[var(--brand-rule)] px-[14px] py-3 text-left align-top"
                  dangerouslySetInnerHTML={{ __html: cell }}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface TocProps {
  items: { label: string; href: string }[]
}

export function LegalToc({ items }: TocProps) {
  return (
    <div
      className="mb-10 rounded-sm px-7 py-6"
      style={{ background: "#FFFDF6", border: "1px solid var(--brand-rule)" }}
    >
      <h4
        className="mb-3 text-[11px] font-semibold tracking-[2.5px] uppercase"
        style={{ color: "var(--brand-gold)" }}
      >
        Contents
      </h4>
      <ol className="m-0 list-decimal columns-2 gap-8 pl-5 text-[14px] sm:columns-1">
        {items.map((item) => (
          <li key={item.href} className="my-1 break-inside-avoid">
            <a
              href={item.href}
              className="underline"
              style={{
                color: "var(--brand-navy-mid)",
                textDecorationColor: "var(--brand-gold)",
              }}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ol>
    </div>
  )
}
