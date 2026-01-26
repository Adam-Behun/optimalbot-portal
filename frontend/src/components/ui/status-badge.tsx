import { cn } from '@/lib/utils';

function getStatusStyle(status: string): string {
  switch (status) {
    case 'completed':
      return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200';
    case 'running':
    case 'starting':
      return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200';
    case 'failed':
      return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200';
    case 'transferred':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200';
    default:
      return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200';
  }
}

interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'default';
  className?: string;
}

export function StatusBadge({ status, size = 'default', className }: StatusBadgeProps) {
  const sizeClasses = size === 'sm'
    ? 'px-2 py-1 text-xs'
    : 'px-3 py-1 text-sm';

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full font-medium capitalize',
        sizeClasses,
        getStatusStyle(status),
        className
      )}
    >
      {status}
    </span>
  );
}
