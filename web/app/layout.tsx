import "./globals.css";
import type { ReactNode } from "react";
import { Archivo, Fraunces, JetBrains_Mono } from "next/font/google";
import { Providers } from "./providers";

// Self-hosted via next/font so the fonts always render identically to the design
// (no Google-CDN/CSP/timing dependency, no fallback-metric "everything looks bigger").
const archivo = Archivo({ subsets: ["latin"], variable: "--font-archivo", display: "swap" });
const fraunces = Fraunces({ subsets: ["latin"], variable: "--font-fraunces", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata = { title: "SubStrateOS — Ask" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${fraunces.variable} ${mono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
