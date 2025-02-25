import {
  DropZoneArea,
  Dropzone,
  DropzoneMessage,
  DropzoneTrigger,
  useDropzone,
} from "@/components/ui/dropzone"

export function ImageDropzone({
  children,
  onDropFile,
}: {
  children: React.ReactNode
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
      accept: {
        "image/*": [".png", ".jpg", ".jpeg", ".pdf"],
      },
      // 10mb
      maxSize: 10 * 1024 * 1024,
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
        </DropzoneTrigger>
      </DropZoneArea>
    </Dropzone>
  )
}
