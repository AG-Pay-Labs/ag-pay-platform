"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import {
  BotIcon,
  CreditCardIcon,
  LayoutDashboardIcon,
  LogOutIcon,
  MenuIcon,
  ReceiptTextIcon,
  RefreshCwIcon,
  ShoppingBasketIcon,
  SlidersHorizontalIcon,
} from "lucide-react"

import { BrandLockup } from "@/components/app/brand"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { useAuth } from "@/providers/auth-provider"

export const APP_NAVIGATION = [
  { href: "/overview", label: "Overview", icon: LayoutDashboardIcon },
  { href: "/approvals", label: "Approvals", icon: ShoppingBasketIcon },
  { href: "/agents", label: "Agents", icon: BotIcon },
  { href: "/rules", label: "Rules", icon: SlidersHorizontalIcon },
  { href: "/cards", label: "Cards", icon: CreditCardIcon },
  { href: "/purchases", label: "Purchases", icon: ReceiptTextIcon },
  { href: "/subscriptions", label: "Subscriptions", icon: RefreshCwIcon },
] as const

type AppShellProps = {
  children: React.ReactNode
  pendingApprovals?: number
}

function isCurrentRoute(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`)
}

function initials(username: string) {
  const parts = username.trim().split(/[\s._-]+/).filter(Boolean)

  if (parts.length === 0) return "AG"

  return parts
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase()
}

function Brand() {
  return (
    <Link
      href="/overview"
      aria-label="AG Pay overview"
      className="inline-flex items-center gap-3 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <BrandLockup markClassName="size-9 rounded-xl" priority />
    </Link>
  )
}

type NavigationProps = {
  pathname: string
  pendingApprovals: number
  onNavigate?: () => void
}

function Navigation({
  pathname,
  pendingApprovals,
  onNavigate,
}: NavigationProps) {
  return (
    <nav aria-label="Main navigation" className="space-y-1">
      {APP_NAVIGATION.map((item) => {
        const active = isCurrentRoute(pathname, item.href)
        const showCount = item.href === "/approvals" && pendingApprovals > 0

        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
            className={cn(
              "group flex min-h-10 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <item.icon
              className={cn(
                "size-[18px] shrink-0",
                active
                  ? "text-indigo-600 dark:text-indigo-400"
                  : "text-muted-foreground group-hover:text-foreground",
              )}
              aria-hidden="true"
            />
            <span>{item.label}</span>
            {showCount ? (
              <span
                className="ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-amber-100 px-1.5 py-0.5 text-[11px] leading-none font-semibold tabular-nums text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                aria-label={`${pendingApprovals} pending approval${pendingApprovals === 1 ? "" : "s"}`}
              >
                {pendingApprovals > 99 ? "99+" : pendingApprovals}
              </span>
            ) : null}
          </Link>
        )
      })}
    </nav>
  )
}

function UserMenu() {
  const router = useRouter()
  const { user, status, logout } = useAuth()
  const [isSigningOut, setIsSigningOut] = React.useState(false)

  if (status === "loading") {
    return (
      <div className="flex items-center gap-3 px-2 py-2" aria-label="Loading account">
        <Skeleton className="size-9 shrink-0 rounded-full" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <Skeleton className="h-3.5 w-24" />
          <Skeleton className="h-3 w-16" />
        </div>
      </div>
    )
  }

  if (!user) return null

  async function handleSignOut() {
    if (isSigningOut) return

    setIsSigningOut(true)
    try {
      await logout()
    } catch {
      // The auth provider clears local session state even if the network request fails.
    } finally {
      router.replace("/login")
      router.refresh()
      setIsSigningOut(false)
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-3 px-2 py-2 text-left"
          aria-label={`Open account menu for ${user.username}`}
        >
          <Avatar className="size-9 bg-indigo-50 dark:bg-indigo-950">
            <AvatarFallback className="bg-indigo-50 text-xs font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              {initials(user.username)}
            </AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-foreground">
              {user.username}
            </span>
            <span className="block truncate text-xs font-normal text-muted-foreground">
              Platform owner
            </span>
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="top" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <span className="block text-xs text-muted-foreground">Signed in as</span>
          <span className="block truncate text-sm font-medium text-foreground">
            {user.username}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          disabled={isSigningOut}
          onSelect={() => void handleSignOut()}
        >
          <LogOutIcon aria-hidden="true" />
          {isSigningOut ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function Sidebar({ pathname, pendingApprovals }: NavigationProps) {
  return (
    <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-border bg-card lg:flex">
      <div className="flex h-[72px] items-center px-5">
        <Brand />
      </div>
      <div className="flex-1 px-3 py-3">
        <Navigation pathname={pathname} pendingApprovals={pendingApprovals} />
      </div>
      <div className="border-t border-border p-3">
        <UserMenu />
      </div>
    </aside>
  )
}

function MobileHeader({ pathname, pendingApprovals }: NavigationProps) {
  const [open, setOpen] = React.useState(false)

  return (
    <header className="sticky top-0 z-40 flex h-16 items-center justify-between border-b border-border bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="outline" size="icon-lg" aria-label="Open navigation">
            <MenuIcon aria-hidden="true" />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="w-[18rem] max-w-[86vw] gap-0 p-0">
          <SheetHeader className="border-b border-border px-5 py-4 text-left">
            <Brand />
            <SheetTitle className="sr-only">Main navigation</SheetTitle>
            <SheetDescription className="sr-only">
              Navigate the AG Pay management application.
            </SheetDescription>
          </SheetHeader>
          <div className="flex-1 px-3 py-4">
            <Navigation
              pathname={pathname}
              pendingApprovals={pendingApprovals}
              onNavigate={() => setOpen(false)}
            />
          </div>
          <div className="border-t border-border p-3">
            <UserMenu />
          </div>
        </SheetContent>
      </Sheet>
      <Brand />
      <div className="w-9" aria-hidden="true" />
    </header>
  )
}

export function AppShell({ children, pendingApprovals = 0 }: AppShellProps) {
  const pathname = usePathname()
  const safePendingCount = Math.max(0, Math.floor(pendingApprovals))

  return (
    <div className="min-h-dvh bg-muted/30 text-foreground">
      <a
        href="#main-content"
        className="fixed top-3 left-3 z-[100] -translate-y-20 rounded-lg bg-background px-3 py-2 text-sm font-medium shadow-lg ring-2 ring-ring transition-transform focus:translate-y-0"
      >
        Skip to content
      </a>
      <div className="flex min-h-dvh">
        <Sidebar pathname={pathname} pendingApprovals={safePendingCount} />
        <div className="min-w-0 flex-1">
          <MobileHeader pathname={pathname} pendingApprovals={safePendingCount} />
          <main
            id="main-content"
            tabIndex={-1}
            className="px-4 py-6 outline-none sm:px-6 lg:px-8 lg:py-8"
          >
            <div className="mx-auto w-full max-w-[1440px]">{children}</div>
          </main>
        </div>
      </div>
    </div>
  )
}
