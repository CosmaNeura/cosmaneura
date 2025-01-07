import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Mail } from '@/data/mock';
import { useState } from 'react';
import Markdown from 'react-markdown'

interface MessageTemplatesProps {
  note: {}
}

export default function AIVisitNote({
  note,
}: MessageTemplatesProps) {
  const [isOpen, setOpen] = useState(false)
  
  return (
      <Collapsible open={isOpen} onOpenChange={setOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0">
              <ChevronDown
                className={`h-4 w-4 transition-transform ${
                  isOpen ? 'transform rotate-180' : ''
                }`}
              />
              <span className="ml-2 text-xl font-semibold">{note.note_name}</span>
            </Button>
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent className="mt-3">
          <div className="text-left text-sm">
            {Object.keys(note.content).map((fieldName) => {
                const noteContent = note.content[fieldName]
                return <>
                <h2>{fieldName}</h2>
                <Markdown>{noteContent}</Markdown>
                </>
            })}
          </div>
        </CollapsibleContent>
      </Collapsible>
  );
}