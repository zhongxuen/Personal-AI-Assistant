import { Children, isValidElement, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, OptionHTMLAttributes, ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from './utils'

interface SelectChangeEvent {
  target: { value: string }
}

interface SelectProps {
  value: string
  onChange: (event: SelectChangeEvent) => void
  children: ReactNode
  className?: string
  disabled?: boolean
  id?: string
  name?: string
  'aria-label'?: string
}

interface ParsedOption {
  value: string
  label: ReactNode
  disabled: boolean
}

/** Extracts `{ value, label, disabled }` from `<option>` children so call sites keep
 * authoring plain `<option>` elements (Tasks.tsx, Routines.tsx) even though this no
 * longer renders a native `<select>` underneath -- see the component doc below for why.
 */
function parseOptions(children: ReactNode): ParsedOption[] {
  const options: ParsedOption[] = []
  Children.forEach(children, (child) => {
    if (!isValidElement<OptionHTMLAttributes<HTMLOptionElement>>(child)) return
    const { value, children: label, disabled } = child.props
    if (value === undefined) return
    options.push({ value: String(value), label, disabled: Boolean(disabled) })
  })
  return options
}

/** Themed dropdown matching Input/Button's styling (md-files/ui-development.md §4).
 * Renders a custom listbox instead of a native `<select>` -- a native select's closed
 * field can be restyled, but its open option list is OS chrome that ignores almost
 * all CSS, so it rendered as a plain light popup that clashed hard with the rest of
 * the Cyberpunk HUD theme. This reimplements the open state too: dark surface, border,
 * and glow tokens, plus a fade/scale entrance matching Modal.tsx's motion treatment.
 *
 * Keeps the native-select call-site shape (`<option>` children, `onChange` reading
 * `e.target.value`) so Tasks.tsx/Routines.tsx didn't need to change.
 *
 * Usage:
 *   <Select value={status} onChange={(e) => setStatus(e.target.value)}>
 *     <option value="open">Open</option>
 *   </Select>
 */
export function Select({ value, onChange, children, className, disabled, id, name, ...rest }: SelectProps) {
  const options = useMemo(() => parseOptions(children), [children])
  const [open, setOpen] = useState(false)
  const [highlighted, setHighlighted] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)

  const selectedIndex = options.findIndex((o) => o.value === value)
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined

  // Re-seed the highlighted row from the current value every time the panel opens,
  // rather than tracking it continuously, so arrow-key nav always starts from
  // "where we are" instead of wherever the mouse last hovered.
  useEffect(() => {
    if (!open) return
    setHighlighted(selectedIndex >= 0 ? selectedIndex : 0)
  }, [open, selectedIndex])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onPointerDown)
    return () => window.removeEventListener('mousedown', onPointerDown)
  }, [open])

  function commit(index: number) {
    const option = options[index]
    if (!option || option.disabled) return
    onChange({ target: { value: option.value } })
    setOpen(false)
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        if (!open) setOpen(true)
        else setHighlighted((i) => Math.min(options.length - 1, i + 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        if (!open) setOpen(true)
        else setHighlighted((i) => Math.max(0, i - 1))
        break
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (open) commit(highlighted)
        else setOpen(true)
        break
      case 'Escape':
        setOpen(false)
        break
      default:
        break
    }
  }

  const listboxId = id ? `${id}-listbox` : undefined

  return (
    <div ref={rootRef} className="relative w-full">
      <button
        type="button"
        id={id}
        name={name}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        {...rest}
        className={cn(
          'flex w-full items-center justify-between gap-2 rounded-md border border-border bg-surface-2 px-3 py-1.5 text-left text-sm text-text transition-all duration-200 focus:border-primary focus:shadow-glow-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-50',
          open && 'border-primary shadow-glow-primary',
          className,
        )}
      >
        <span className={cn('truncate', !selected && 'text-text-muted')}>
          {selected ? selected.label : value || 'Select…'}
        </span>
        <ChevronDown
          className={cn(
            'h-4 w-4 shrink-0 text-text-muted transition-transform duration-200',
            open && 'rotate-180 text-primary',
          )}
        />
      </button>
      <AnimatePresence>
        {open && (
          <motion.ul
            id={listboxId}
            role="listbox"
            tabIndex={-1}
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-surface-2/95 p-1 shadow-2xl backdrop-blur-md"
          >
            {options.map((option, index) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled}
                onClick={() => commit(index)}
                onMouseEnter={() => setHighlighted(index)}
                className={cn(
                  'flex cursor-pointer items-center justify-between gap-2 rounded px-2.5 py-1.5 text-sm transition-colors duration-100',
                  option.disabled && 'cursor-not-allowed opacity-40',
                  !option.disabled && index === highlighted && 'bg-primary/15 text-primary',
                  !option.disabled && index !== highlighted && 'text-text hover:bg-surface',
                )}
              >
                <span className="truncate">{option.label}</span>
                {option.value === value && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  )
}
