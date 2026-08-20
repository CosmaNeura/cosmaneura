import {
  ChannelAuthChrome,
  channelRedirect,
  channelSibling,
  useAuthChannel,
} from "~/components/brand/ChannelAuthChrome"

import { SignUp } from "@clerk/clerk-react"

export default function SignUpPage() {
  const channel = useAuthChannel()

  return (
    <ChannelAuthChrome channel={channel}>
      <SignUp
        path="/sign-up"
        signInUrl={channelSibling("sign-up", channel)}
        forceRedirectUrl={channelRedirect(channel)}
        fallbackRedirectUrl={channelRedirect(channel)}
      />
    </ChannelAuthChrome>
  )
}
