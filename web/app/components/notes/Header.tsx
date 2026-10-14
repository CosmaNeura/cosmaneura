import { UploadRecordingButton } from "@/components/notes/UploadRecordingButton"
import { CreateTestVisitsButton } from "~/components/notes/CreateTestVisitsButton"
import { PageHeader } from "~/components/shared/PageHeader"
import { isStaging } from "~/utils/environment"

export function Header({ title }: { title?: string }) {
  return (
    <PageHeader
      title={title ?? "Notes"}
      isPageTitle={title === undefined}
      actions={
        <>
          {title === undefined && isStaging() && <CreateTestVisitsButton />}
          <UploadRecordingButton />
        </>
      }
    />
  )
}
