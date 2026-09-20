export const metadata = { title: "Business Platform", description: "Reusable enterprise business platform" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
