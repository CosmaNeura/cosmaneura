import { motion, useReducedMotion } from "motion/react"

// MotionConfig's reducedMotion only drops transform animations; the infinite
// opacity pulse would keep running, so it is omitted outright.
export function AnimatedCheckmark() {
  const reduceMotion = useReducedMotion()
  return (
    <motion.div
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 200, damping: 15, delay: 0.1 }}
      className="relative flex size-24 items-center justify-center rounded-full"
      style={{ background: "var(--brand-navy-deep)" }}
      aria-hidden="true"
    >
      {!reduceMotion && (
        <motion.span
          initial={{ scale: 0.8, opacity: 0.6 }}
          animate={{ scale: 1.6, opacity: 0 }}
          transition={{ duration: 1.2, repeat: Infinity, ease: "easeOut" }}
          className="absolute inset-0 rounded-full"
          style={{ background: "var(--brand-navy-deep)" }}
        />
      )}
      <svg
        viewBox="0 0 52 52"
        className="size-12"
        fill="none"
        stroke="var(--brand-gold)"
        strokeWidth="4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <motion.path
          d="M14 27 L23 36 L38 18"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.5, delay: 0.4, ease: "easeOut" }}
        />
      </svg>
    </motion.div>
  )
}
