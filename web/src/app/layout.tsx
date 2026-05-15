import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "axiom-microsim — CO SNAP",
  description: "PE-free microsimulation over Enhanced CPS using axiom-rules-engine",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-stone-50 text-stone-900 antialiased">{children}</body>
    </html>
  );
}
