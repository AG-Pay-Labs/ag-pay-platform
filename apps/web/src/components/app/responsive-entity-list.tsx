import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

export type EntityColumn<T> = {
  id: string
  header: React.ReactNode
  cell: (item: T) => React.ReactNode
  mobileLabel?: React.ReactNode
  align?: "left" | "center" | "right"
  className?: string
  headerClassName?: string
  hideOnMobile?: boolean
}

type ResponsiveEntityListProps<T> = {
  items: readonly T[]
  columns: readonly EntityColumn<T>[]
  getKey: (item: T) => React.Key
  caption: string
  emptyState?: React.ReactNode
  renderMobile?: (item: T) => React.ReactNode
  className?: string
  tableClassName?: string
}

function alignmentClass(alignment: EntityColumn<unknown>["align"]) {
  if (alignment === "right") return "text-right"
  if (alignment === "center") return "text-center"
  return "text-left"
}

export function ResponsiveEntityList<T>({
  items,
  columns,
  getKey,
  caption,
  emptyState,
  renderMobile,
  className,
  tableClassName,
}: ResponsiveEntityListProps<T>) {
  if (items.length === 0) return <>{emptyState ?? null}</>

  return (
    <div className={cn("min-w-0", className)}>
      <div className="hidden overflow-hidden rounded-xl border border-border bg-card md:block">
        <Table className={tableClassName}>
          <caption className="sr-only">{caption}</caption>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              {columns.map((column) => (
                <TableHead
                  key={column.id}
                  className={cn(
                    "px-4 text-xs font-semibold tracking-wide text-muted-foreground uppercase",
                    alignmentClass(column.align as EntityColumn<unknown>["align"]),
                    column.headerClassName,
                  )}
                >
                  {column.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={getKey(item)}>
                {columns.map((column) => (
                  <TableCell
                    key={column.id}
                    className={cn(
                      "px-4 py-3",
                      alignmentClass(column.align as EntityColumn<unknown>["align"]),
                      column.className,
                    )}
                  >
                    {column.cell(item)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <ul className="grid gap-3 md:hidden" aria-label={caption}>
        {items.map((item) => (
          <li
            key={getKey(item)}
            className="overflow-hidden rounded-xl border border-border bg-card p-4 shadow-sm shadow-black/[0.02]"
          >
            {renderMobile ? (
              renderMobile(item)
            ) : (
              <dl className="space-y-3">
                {columns
                  .filter((column) => !column.hideOnMobile)
                  .map((column) => (
                    <div
                      key={column.id}
                      className="flex items-start justify-between gap-4"
                    >
                      <dt className="text-xs font-medium text-muted-foreground">
                        {column.mobileLabel ?? column.header}
                      </dt>
                      <dd className="min-w-0 text-right text-sm text-foreground">
                        {column.cell(item)}
                      </dd>
                    </div>
                  ))}
              </dl>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

