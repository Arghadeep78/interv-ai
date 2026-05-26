import React from 'react';

export function UploadCard({
  icon: Icon, iconColor, label, accept, file, onChange,
}: {
  icon: React.ElementType; iconColor: string; label: string;
  accept: string; file: File | null;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label className="glass upload-zone rounded-2xl p-6 flex flex-col items-center gap-3 cursor-pointer border border-subtle hover:border-accent-alt">
      <div className={`w-14 h-14 rounded-2xl flex items-center justify-center ${iconColor}`}>
        <Icon className="w-7 h-7" />
      </div>
      <span className="font-semibold text-primary text-sm">{label}</span>
      {file
        ? <span className="text-accent-alt text-xs font-medium truncate max-w-full">{file.name}</span>
        : <span className="text-subtle text-xs">PDF or TXT</span>}
      <input type="file" accept={accept} className="sr-only" onChange={onChange} />
    </label>
  );
}
