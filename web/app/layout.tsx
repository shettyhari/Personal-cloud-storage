import type { Metadata, Viewport } from "next";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "Personal Cloud OS",
  description: "Your home PC as a private cloud, remote server and AI workspace",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

// Applies the saved theme before first paint to avoid a flash.
const themeScript = `try{var t=localStorage.getItem('pcloud-theme');if(t==='dark'||(!t&&false))document.documentElement.classList.add('dark')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
