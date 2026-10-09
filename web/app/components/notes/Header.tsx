import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { PageHeader } from "~/components/shared/PageHeader"

export function Header({ title }: { title?: string }) {
  return (
    <PageHeader
      title={title ?? "Notes"}
      isPageTitle={!title}
      actions={<UploadRecordingButton />}
    />
  )
}
