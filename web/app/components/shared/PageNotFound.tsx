function PageNotFoundIcon() {
  return (
    <svg
      className="text-accent mx-auto h-16 w-16"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
      />
    </svg>
  )
}

export default function PageNotFound() {
  return (
    <div className="bg-background flex min-h-screen items-center justify-center px-4">
      <div className="text-center">
        <h1 className="text-primary text-9xl font-bold">404</h1>
        <div className="mt-4 motion-safe:animate-bounce">
          <PageNotFoundIcon />
        </div>
        <h2 className="text-foreground mt-6 text-4xl font-bold tracking-tight sm:text-5xl">
          Page not found
        </h2>
        <p className="text-muted-foreground mt-4 text-lg">
          Sorry, we couldn't find the page you're looking for.
        </p>
      </div>
    </div>
  )
}
