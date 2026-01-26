import { cn } from '@/lib/utils';

interface DetailRowProps {
  label: string;
  value: React.ReactNode;
  labelWidth?: string;
  className?: string;
}

export function DetailRow({ label, value, labelWidth = 'w-48', className }: DetailRowProps) {
  return (
    <div className={cn('flex py-1.5 border-b last:border-b-0', className)}>
      <div className={cn('font-semibold text-muted-foreground shrink-0', labelWidth)}>
        {label}:
      </div>
      <div className="flex-1 text-foreground">{value || '-'}</div>
    </div>
  );
}
