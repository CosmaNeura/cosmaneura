import { cn } from "@/lib/utils";
import { Mail } from "@/data/mock";

interface PatientDataSectionsProps {
  className?: string;
  mail: Mail;
}

export function PatientDataSections({ className, mail }: PatientDataSectionsProps) {
  return (
    <div className={cn("grid gap-6 lg:grid-cols-2", className)}>
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Summary</h2>
        <div className="text-sm">
          {mail.teaser}
        </div>
      </div>
      
      <div className="space-y-6">
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">Complaints</h2>
          <ul className="list-disc pl-4 text-sm space-y-2">
            {mail.complaints?.list.map((complaint, index) => (
              <li key={index}>{complaint}</li>
            ))}
          </ul>
        </div>

        {mail.patientData && (
          <div className="space-y-4">
            <h2 className="text-xl font-semibold">Patient Data</h2>
            <div className="grid gap-4 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Height:</span>
                <span>{mail.patientData.height} ({mail.patientData.heightMetric})</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Weight:</span>
                <span>{mail.patientData.weight} ({mail.patientData.weightMetric})</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Blood Pressure:</span>
                <span>{mail.patientData.bloodPressure}</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Heart Rate:</span>
                <span>{mail.patientData.heartRate}</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <span className="text-muted-foreground">Temperature:</span>
                <span>{mail.patientData.temperature} ({mail.patientData.temperatureMetric})</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}