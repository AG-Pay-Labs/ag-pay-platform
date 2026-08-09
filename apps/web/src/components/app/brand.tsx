import Image from "next/image";

import { cn } from "@/lib/utils";

type BrandMarkProps = {
  className?: string;
  priority?: boolean;
};

export function BrandMark({ className, priority = false }: BrandMarkProps) {
  return (
    <span
      className={cn(
        "relative block size-10 shrink-0 overflow-hidden rounded-[9px] bg-transparent shadow-sm shadow-violet-950/20 ring-1 ring-violet-950/10 dark:ring-white/10",
        className,
      )}
      aria-hidden="true"
    >
      <Image
        src="/brand/agpay-mark.png"
        alt=""
        fill
        priority={priority}
        sizes="48px"
        className="object-cover"
      />
    </span>
  );
}

type BrandLockupProps = {
  className?: string;
  markClassName?: string;
  inverse?: boolean;
  subtitle?: string;
  priority?: boolean;
};

export function BrandLockup({
  className,
  markClassName,
  inverse = false,
  subtitle = "Agent wallet",
  priority = false,
}: BrandLockupProps) {
  return (
    <span className={cn("inline-flex items-center gap-3", className)}>
      <BrandMark className={markClassName} priority={priority} />
      <span className="min-w-0">
        <span
          className={cn(
            "block text-sm font-semibold tracking-tight",
            inverse ? "text-white" : "text-foreground",
          )}
        >
          AG Pay
        </span>
        <span
          className={cn(
            "block text-xs",
            inverse ? "text-zinc-400" : "text-muted-foreground",
          )}
        >
          {subtitle}
        </span>
      </span>
    </span>
  );
}
