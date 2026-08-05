export function formatMoney(amount: string | number, currency: string) {
  const value = typeof amount === "number" ? amount : Number.parseFloat(amount);

  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return "Not available";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "Not scheduled";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(value),
  );
}

export function relativeTime(value: string | null | undefined) {
  if (!value) return "Never";

  const deltaSeconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];

  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      return formatter.format(Math.round(deltaSeconds / seconds), unit);
    }
  }

  return formatter.format(deltaSeconds, "second");
}

export function hostname(value: string) {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return value;
  }
}

export function titleCase(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

