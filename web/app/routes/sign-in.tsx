import { SignIn } from "@clerk/clerk-react"

import {
  ChannelAuthChrome,
  channelRedirect,
  channelSibling,
  useAuthChannel,
} from "~/components/brand/ChannelAuthChrome"

export default function SignInPage() {
  const channel = useAuthChannel()

  return (
    <ChannelAuthChrome channel={channel}>
      <SignIn
        path="/sign-in"
        signUpUrl={channelSibling("sign-in", channel)}
        forceRedirectUrl={channelRedirect(channel)}
        fallbackRedirectUrl={channelRedirect(channel)}
      />
    </ChannelAuthChrome>
  )
}
