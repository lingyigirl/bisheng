import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cname } from "../utils"

const badgeVariants = cva(
    "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
    {
        variants: {
            variant: {
                default:
                    "border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80",
                secondary:
                    "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
                destructive:
                    "border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80",
                outline: "text-foreground",
                gray: "bg-[#E8EBF2] text-[#61646D] px-1 py-[1px]",
                // Status colours, from the semantic tokens rather than hex (root
                // AGENTS.md: semantic colours are not themed). No `hover:` -- a
                // status badge is not interactive, and the siblings' hover would
                // otherwise tint it back toward the brand colour on mouse-over.
                success: "border-transparent bg-success-background text-success-foreground",
                error: "border-transparent bg-error-background text-error-foreground"
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
)

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

function Badge({ className, variant, ...props }: BadgeProps) {
    return (
        <div className={cname(badgeVariants({ variant }), className)} {...props} />
    )
}

export { Badge, badgeVariants }
