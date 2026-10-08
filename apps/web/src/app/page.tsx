export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-16">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">ClaimPilot</h1>
        <p className="text-zinc-600 dark:text-zinc-400">
          Drop a pile of receipts. Get a claim that&apos;s ready to submit.
        </p>
      </header>

      {/* M3: replace with the real drop zone + camera capture + streaming claim cards */}
      <section
        aria-label="Upload receipts"
        className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-zinc-300 p-8 text-center dark:border-zinc-700"
      >
        <p className="font-medium">Receipts, PDFs and UPI screenshots</p>
        <p className="text-sm text-zinc-500">Upload arrives in milestone M3</p>
      </section>
    </main>
  );
}
