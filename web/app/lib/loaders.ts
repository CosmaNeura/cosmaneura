import { mockData } from "@/data/mock"

export async function loadMockData() {
  // Simulate network delay
  await new Promise((resolve) => setTimeout(resolve, 1000))
  return mockData
}

export async function clientLoader() {
  const data = await loadMockData()
  return { data }
}
