import { expect, it } from "vitest"

it("preserves the finished rejection when a DOM animation is cancelled", async () => {
  const element = document.createElement("div")
  document.body.append(element)
  const animation = element.animate([{ opacity: 0 }, { opacity: 1 }], 1000)
  const finished = expect(animation.finished).rejects.toMatchObject({
    name: "AbortError",
  })
  animation.cancel()
  await finished
  expect(animation.playState).toBe("idle")
  element.remove()
})
