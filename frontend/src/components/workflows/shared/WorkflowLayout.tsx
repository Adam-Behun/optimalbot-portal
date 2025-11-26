import { ReactNode } from 'react';

interface WorkflowLayoutProps {
  workflowName: string;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}

export function WorkflowLayout({
  children,
  actions,
}: WorkflowLayoutProps) {
  return (
    <div className="w-full space-y-6">
      {/* Actions bar (if any) */}
      {actions && (
        <div className="flex items-center justify-end">
          <div className="flex items-center gap-2">{actions}</div>
        </div>
      )}

      {/* Content */}
      <div>{children}</div>
    </div>
  );
}
