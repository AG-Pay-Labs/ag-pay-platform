import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppProviders } from "@/providers/app-providers";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "AG Pay",
    template: "%s · AG Pay",
  },
  description: "The human control plane for purchases proposed by AI agents.",
  icons: {
    icon: "/brand/agpay-mark.png",
    apple: "/brand/agpay-mark.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>
        <AppProviders>
          <TooltipProvider>
            {children}
            <Toaster richColors position="top-right" />
          </TooltipProvider>
        </AppProviders>
      </body>
    </html>
  );
}
