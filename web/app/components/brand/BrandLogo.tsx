interface BrandLogoProps {
  variant?: "light" | "dark"
  size?: "sm" | "md" | "lg"
}

const sizes = {
  sm: { img: "w-8 h-8", name: "text-xl", co: "text-[10px]" },
  md: { img: "w-14 h-14", name: "text-2xl", co: "text-[11px]" },
  lg: { img: "w-16 h-16", name: "text-3xl", co: "text-xs" },
}

export function BrandLogo({ variant = "light", size = "md" }: BrandLogoProps) {
  const s = sizes[size]
  const nameColor =
    variant === "light"
      ? "text-[var(--brand-cream)]"
      : "text-[var(--brand-navy-deep)]"
  const coColor = "text-[var(--brand-gold)]"

  return (
    <div className="flex items-center gap-4">
      <img
        src="/cosmas-icon-128.png"
        alt="Cosmas logo"
        className={`${s.img} rounded-xl`}
      />
      <div className="flex flex-col leading-none">
        <span
          className={`${s.name} ${nameColor} font-semibold tracking-[0.5px]`}
          style={{ fontFamily: "'Cormorant Garamond', serif" }}
        >
          Cosmas
        </span>
        <span
          className={`${s.co} ${coColor} mt-1 font-semibold tracking-[2.5px] uppercase`}
        >
          by CosmaNeura, Inc.
        </span>
      </div>
    </div>
  )
}
