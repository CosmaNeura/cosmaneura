import {
  ChannelAuthChrome,
  channelRedirect,
  channelSibling,
  useAuthChannel,
} from "~/components/brand/ChannelAuthChrome"

import { SignIn } from "@clerk/clerk-react"

export default function SignInPage() {
  const channel = useAuthChannel()

  return (
    <ChannelAuthChrome channel={channel}>
      {/* fallback only: forceRedirectUrl would override Clerk's redirect_url
          and send deep-linked users to /onboarding instead of their target */}
      <SignIn
        path="/sign-in"
        signUpUrl={channelSibling("sign-in", channel)}
        fallbackRedirectUrl={channelRedirect(channel)}
      />
    </ChannelAuthChrome>
  )
}
