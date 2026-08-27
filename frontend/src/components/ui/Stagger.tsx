import type { ReactNode } from 'react'
import type { Variants } from 'framer-motion'
import { motion } from 'framer-motion'

const CONTAINER_VARIANTS: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
}

const ITEM_VARIANTS: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.25, ease: 'easeOut' } },
}

interface StaggerProps {
  children?: ReactNode
  className?: string
}

/** Staggered fade/slide-in for list items on load (md-files/ui-development.md §2/§5:
 * tasks, routines, provider cards). Wrap the list container in `StaggerList` and each
 * row/card in `StaggerItem` -- framer-motion propagates the "hidden" -> "visible"
 * variant transition down to children and staggers each child's start time. An item
 * that mounts later (e.g. freshly created) still animates in on its own; it just isn't
 * staggered against siblings that already finished.
 *
 * Usage:
 *   <StaggerList className="space-y-2">
 *     {items.map((item) => <StaggerItem key={item.id}><Panel>...</Panel></StaggerItem>)}
 *   </StaggerList>
 */
export function StaggerList({ children, className }: StaggerProps) {
  return (
    <motion.div className={className} variants={CONTAINER_VARIANTS} initial="hidden" animate="visible">
      {children}
    </motion.div>
  )
}

export function StaggerItem({ children, className }: StaggerProps) {
  return (
    <motion.div className={className} variants={ITEM_VARIANTS}>
      {children}
    </motion.div>
  )
}
