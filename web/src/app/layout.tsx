import type { Metadata } from "next";
import { JetBrains_Mono, Newsreader, Geist } from "next/font/google";
import { Nav } from "@/components/Nav";
import "./globals.css";

const sans = Geist({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const serif = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  variable: "--font-serif",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "axiom-microsim — CO SNAP",
  description: "PE-free microsimulation over Enhanced CPS using axiom-rules-engine",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/axiom-icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/axiom-icon-512.png",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${serif.variable} ${mono.variable}`}
    >
      <body className="bg-paper text-ink antialiased">
        <Nav />
        {children}
      </body>
    </html>
  );
}
