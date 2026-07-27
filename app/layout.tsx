import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentarium · AI Company OS",
  description:
    "Plataforma local-first para convertir objetivos en trabajo de agentes verificable.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
