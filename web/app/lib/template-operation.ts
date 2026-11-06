import {
  type BrowserPrincipal,
  getBrowserPrincipalToken,
} from "~/configuration/clerk"
import { internalApiFetchWithDispatchGuard } from "~/lib/billing-recovery"

/** Per-request overrides only; the shared generated client's auth stays unchanged. */
export async function templateOperation<T>(
  principal: BrowserPrincipal,
  isCurrent: () => boolean,
  request: (options: {
    auth: string
    fetch: typeof globalThis.fetch
  }) => Promise<T>,
): Promise<T> {
  const auth = await getBrowserPrincipalToken(principal, isCurrent)
  try {
    const result = await request({
      auth: auth.token,
      fetch: (input, init) =>
        internalApiFetchWithDispatchGuard(input, init, auth.assertCurrent),
    })
    auth.assertCurrent()
    return result
  } catch (failure) {
    auth.assertCurrent()
    throw failure
  }
}
