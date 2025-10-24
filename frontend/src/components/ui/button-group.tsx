import * as React from "react"
import { cn } from "@/lib/utils"

const ButtonGroup = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "inline-flex rounded-md shadow-sm [&>button]:rounded-none [&>button:first-child]:rounded-l-md [&>button:last-child]:rounded-r-md [&>button:not(:first-child)]:border-l-0",
      className
    )}
    {...props}
  />
))
ButtonGroup.displayName = "ButtonGroup"

export { ButtonGroup }
