import { ErrorPage } from "./ErrorPage"

function PageNotFoundIcon() {
  return (
    <svg
      className="text-accent mx-auto h-16 w-16"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      aria-hidden="true"
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
    <ErrorPage
      eyebrow={
        <>
          <p className="text-primary text-7xl font-bold sm:text-8xl">404</p>
          <div className="mt-4 motion-safe:animate-bounce">
            <PageNotFoundIcon />
          </div>
        </>
      }
      title="Page not found"
      description="Sorry, we couldn't find the page you're looking for."
    />
  )
}
