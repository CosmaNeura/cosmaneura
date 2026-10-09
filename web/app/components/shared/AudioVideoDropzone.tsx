import { Upload } from "lucide-react"

import {
  DropZoneArea,
  Dropzone,
  DropzoneMessage,
  DropzoneTrigger,
  useDropzone,
} from "@/components/ui/dropzone"
import { VISIT_UPLOAD_MAX_BYTES } from "~/lib/azure-upload"
import { formatFileSize } from "~/lib/file-size"

// Mirrors _CONTENT_TYPE_BY_EXTENSION["visit_audio"] in app/commands/uploads/sas.py;
// the server refuses any other extension.
const ACCEPTED_TYPES = {
  "audio/*": [".mp3", ".wav", ".m4a"],
  "video/*": [".mp4", ".mov", ".avi"],
}

export function AudioVideoDropzone({
  children,
  file,
  onDropFile,
}: {
  children?: React.ReactNode
  file: File | null
  onDropFile: (file: File) => void
}) {
  const dropzone = useDropzone({
    // this off-the-shelf component seems to want to upload files right away, which we do not do
    // this expectation mismatch could cause issues in the future. Additionally, react-dropzone
    // is not officially supported by react 19, which could further cause issues.

    onDropFile: async (file: File) => {
      // naming is confusing here! This is the argument passed to the image drop
      // designed to bet a hook to set the file reference
      onDropFile(file)

      return {
        status: "success",
        result: null,
      }
    },
    validation: {
      accept: ACCEPTED_TYPES,
      // Keep this in sync with the server-side visit-upload ceiling.
      maxSize: VISIT_UPLOAD_MAX_BYTES,
      maxFiles: 1,
    },
    shiftOnMaxFiles: true,
  })

  return (
    <Dropzone {...dropzone}>
      <div className="flex justify-between">
        <DropzoneMessage />
      </div>
      <DropZoneArea>
        <DropzoneTrigger className="my-6 flex gap-8 bg-transparent text-sm">
          {children}
          {!children && !file && (
            <div className="flex items-center gap-4 font-semibold">
              <div className="bg-muted rounded-full p-3">
                <Upload className="h-4 w-4" />
              </div>
              <div className="flex flex-col gap-1">
                <p>Upload Audio or Video</p>
                <p className="text-muted-foreground text-xs">
                  Click here or drag in an audio or video file smaller than{" "}
                  {formatFileSize(VISIT_UPLOAD_MAX_BYTES)}
                </p>
              </div>
            </div>
          )}
          {!children && file && (
            <div className="flex min-w-0 flex-col gap-1 text-left">
              <p
                className="truncate font-semibold"
                data-ph-no-capture=""
                data-sentry-block=""
              >
                {file.name}
              </p>
              <p className="text-muted-foreground text-xs">
                {formatFileSize(file.size)}
              </p>
              <p className="text-primary text-xs font-medium underline underline-offset-4">
                Choose a different file
              </p>
            </div>
          )}
        </DropzoneTrigger>
      </DropZoneArea>
    </Dropzone>
  )
}
