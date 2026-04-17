import type { ReactNode } from "react"

interface LegalCalloutProps {
  children: ReactNode
}

export function LegalCallout({ children }: LegalCalloutProps) {
  return (
    <div
      className="rounded-sm text-[15px] my-5 px-[22px] py-[18px]"
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
        className="font-semibold text-[30px] leading-snug mt-12 mb-1"
        style={{ fontFamily: "'Cormorant Garamond', serif", color: "var(--brand-navy-deep)" }}
      >
        <span className="block text-[11px] font-semibold tracking-[3px] uppercase mb-2 font-sans" style={{ color: "var(--brand-gold)", fontFamily: "Inter, sans-serif" }}>
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
      className="font-semibold text-[17px] mt-7 mb-2"
      style={{ color: "var(--brand-navy-deep)", fontFamily: "Inter, sans-serif" }}
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
    <div className="overflow-x-auto my-[18px]">
      <table className="w-full border-collapse text-[14.5px]">
        <thead>
          <tr>
            {headers.map((h) => (
              <th
                key={h}
                className="text-left px-[14px] py-3 text-[13px] uppercase tracking-[1px] font-semibold border-b border-[var(--brand-rule)]"
                style={{ background: "#FFFDF6", color: "var(--brand-navy-deep)" }}
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
                  className="text-left px-[14px] py-3 border-b border-[var(--brand-rule)] align-top"
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
      className="rounded-sm px-7 py-6 mb-10"
      style={{ background: "#FFFDF6", border: "1px solid var(--brand-rule)" }}
    >
      <h4
        className="text-[11px] uppercase tracking-[2.5px] font-semibold mb-3"
        style={{ color: "var(--brand-gold)" }}
      >
        Contents
      </h4>
      <ol className="columns-2 gap-8 text-[14px] list-decimal pl-5 m-0 sm:columns-1">
        {items.map((item) => (
          <li key={item.href} className="my-1 break-inside-avoid">
            <a
              href={item.href}
              className="underline"
              style={{ color: "var(--brand-navy-mid)", textDecorationColor: "var(--brand-gold)" }}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ol>
    </div>
  )
}
