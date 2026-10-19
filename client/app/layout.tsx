import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import {
  ClerkProvider,
  SignedOut,
  SignedIn,
  SignInButton,
  SignUpButton,
} from '@clerk/nextjs';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'DocQuery',
  description: 'Ask questions about PDFs and notes with answers linked to source passages.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body
          className={`${geistSans.variable} ${geistMono.variable} antialiased`}
        >
          <SignedOut>
            <main className="flex min-h-dvh items-center justify-center bg-muted/30 px-5 py-12">
              <section aria-labelledby="welcome-heading" className="w-full max-w-md rounded-2xl border bg-background p-8 text-center shadow-sm sm:p-10">
                <div aria-hidden="true" className="mx-auto mb-6 flex size-12 items-center justify-center rounded-xl bg-indigo-600 font-semibold text-white">DQ</div>
                <p className="mb-3 text-sm font-medium text-indigo-600 dark:text-indigo-300">DocQuery</p>
                <h1 id="welcome-heading" className="text-2xl font-semibold tracking-tight">Your documents. Clearer answers.</h1>
                <p className="mt-4 text-sm leading-6 text-muted-foreground">Ask questions about PDFs and notes, explore the details, and follow citations back to the source.</p>
                <div className="mt-8 flex flex-col gap-3">
                  <SignInButton mode="modal">
                    <button type="button" className="rounded-lg bg-indigo-600 px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500">Sign in</button>
                  </SignInButton>
                  <SignUpButton mode="modal">
                    <button type="button" className="rounded-lg border bg-background px-4 py-3 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500">Create account</button>
                  </SignUpButton>
                </div>
                <p className="mt-5 text-xs text-muted-foreground">Sign in to open your document workspace.</p>
              </section>
            </main>
          </SignedOut>
          <SignedIn>{children}</SignedIn>
        </body>
      </html>
    </ClerkProvider>
  );
}
