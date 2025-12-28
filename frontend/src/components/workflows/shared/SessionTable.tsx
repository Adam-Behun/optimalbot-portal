import { useState, useMemo } from 'react';
import { Session } from '@/types';
import { useBreakpoint } from '@/hooks/use-mobile';
import { formatDatetime } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ArrowUpDown, Trash2, MoreHorizontal, Eye, CheckCircle, Minus } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface SessionTableProps {
  sessions: Session[];
  loading?: boolean;
  onRowClick?: (session: Session) => void;
  onViewSession?: (session: Session) => void;
  onDeleteSession?: (session: Session) => void;
  onDeleteSessions?: (sessions: Session[]) => void;
}

// Format phone number for display
function formatPhone(phone?: string): string {
  if (!phone) return '-';
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.length === 11 && cleaned.startsWith('1')) {
    return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
  }
  if (cleaned.length === 10) {
    return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
  }
  return phone;
}

// Format duration between two timestamps
function formatDuration(startedAt: string, completedAt?: string): string {
  if (!completedAt) return '-';
  const start = new Date(startedAt).getTime();
  const end = new Date(completedAt).getTime();
  const seconds = Math.floor((end - start) / 1000);
  if (seconds < 0) return '-';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainingSeconds}s` : `${remainingSeconds}s`;
}

// Get badge variant based on session status
function getStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'completed':
      return 'default';
    case 'running':
    case 'starting':
      return 'secondary';
    case 'failed':
      return 'destructive';
    case 'transferred':
      return 'outline';
    default:
      return 'outline';
  }
}

// Get display text for session status
function getStatusDisplay(status: string): string {
  switch (status) {
    case 'completed':
      return 'Completed';
    case 'running':
      return 'In Progress';
    case 'starting':
      return 'Starting';
    case 'failed':
      return 'Failed';
    case 'transferred':
      return 'Transferred';
    default:
      return status;
  }
}

type SortDirection = 'asc' | 'desc' | null;

export function SessionTable({
  sessions,
  loading,
  onRowClick,
  onViewSession,
  onDeleteSession,
  onDeleteSessions,
}: SessionTableProps) {
  const hasRowActions = onViewSession || onDeleteSession;
  const breakpoint = useBreakpoint();

  // State
  const [callerFilter, setCallerFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sortField, setSortField] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [pageSize, setPageSize] = useState(10);
  const [pageIndex, setPageIndex] = useState(0);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [bulkActionLoading, setBulkActionLoading] = useState(false);

  // Filter and sort sessions
  const filteredSessions = useMemo(() => {
    let result = [...sessions];

    // Filter by caller (name or phone)
    if (callerFilter) {
      const lower = callerFilter.toLowerCase();
      result = result.filter(s => {
        const name = s.caller_name?.toLowerCase() || '';
        const phone = s.caller_phone || '';
        return name.includes(lower) || phone.includes(callerFilter);
      });
    }

    // Filter by status
    if (statusFilter !== 'all') {
      result = result.filter(s => s.status === statusFilter);
    }

    // Sort
    if (sortField && sortDirection) {
      result.sort((a, b) => {
        let aVal: string | boolean | undefined;
        let bVal: string | boolean | undefined;

        if (sortField === 'caller') {
          aVal = a.caller_name || a.caller_phone || '';
          bVal = b.caller_name || b.caller_phone || '';
        } else if (sortField === 'duration') {
          // Sort by actual duration in seconds
          const aDur = a.completed_at ? new Date(a.completed_at).getTime() - new Date(a.created_at).getTime() : 0;
          const bDur = b.completed_at ? new Date(b.completed_at).getTime() - new Date(b.created_at).getTime() : 0;
          return sortDirection === 'asc' ? aDur - bDur : bDur - aDur;
        } else {
          aVal = (a as Record<string, unknown>)[sortField] as string | undefined;
          bVal = (b as Record<string, unknown>)[sortField] as string | undefined;
        }

        const comparison = String(aVal ?? '').localeCompare(String(bVal ?? ''));
        return sortDirection === 'asc' ? comparison : -comparison;
      });
    }

    return result;
  }, [sessions, callerFilter, statusFilter, sortField, sortDirection]);

  // Pagination
  const pageCount = Math.ceil(filteredSessions.length / pageSize);
  const paginatedSessions = filteredSessions.slice(
    pageIndex * pageSize,
    (pageIndex + 1) * pageSize
  );

  // Selection
  const selectedSessions = sessions.filter(s => selectedIds.has(s.session_id));
  const allPageSelected = paginatedSessions.length > 0 &&
    paginatedSessions.every(s => selectedIds.has(s.session_id));
  const somePageSelected = paginatedSessions.some(s => selectedIds.has(s.session_id));

  const toggleSelectAll = () => {
    if (allPageSelected) {
      const newSelected = new Set(selectedIds);
      paginatedSessions.forEach(s => newSelected.delete(s.session_id));
      setSelectedIds(newSelected);
    } else {
      const newSelected = new Set(selectedIds);
      paginatedSessions.forEach(s => newSelected.add(s.session_id));
      setSelectedIds(newSelected);
    }
  };

  const toggleSelect = (sessionId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(sessionId)) {
      newSelected.delete(sessionId);
    } else {
      newSelected.add(sessionId);
    }
    setSelectedIds(newSelected);
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      if (sortDirection === 'asc') {
        setSortDirection('desc');
      } else if (sortDirection === 'desc') {
        setSortField(null);
        setSortDirection(null);
      }
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const handleBulkDelete = async () => {
    if (!onDeleteSessions || selectedSessions.length === 0) return;
    setBulkActionLoading(true);
    setShowDeleteDialog(false);
    try {
      await onDeleteSessions(selectedSessions);
      setSelectedIds(new Set());
    } finally {
      setBulkActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="text-center py-12 border rounded-lg bg-card">
        <p className="text-muted-foreground">No calls found</p>
      </div>
    );
  }

  // Determine which columns to show based on breakpoint
  const showOutcome = breakpoint === 'desktop' || breakpoint === 'tablet';
  const showDuration = breakpoint === 'desktop';
  const showVerified = breakpoint === 'desktop' || breakpoint === 'tablet';

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
        <Input
          placeholder="Search by caller..."
          value={callerFilter}
          onChange={(e) => {
            setCallerFilter(e.target.value);
            setPageIndex(0);
          }}
          className="w-full sm:max-w-sm"
        />
        <Select
          value={statusFilter}
          onValueChange={(value) => {
            setStatusFilter(value);
            setPageIndex(0);
          }}
        >
          <SelectTrigger className="w-full sm:w-[180px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Statuses</SelectItem>
            <SelectItem value="running">In Progress</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="transferred">Transferred</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Bulk Actions Bar */}
      {selectedSessions.length > 0 && onDeleteSessions && (
        <div className="flex flex-col gap-3 p-4 bg-muted rounded-lg sm:flex-row sm:items-center sm:gap-2">
          <span className="text-sm font-medium">
            {selectedSessions.length} selected
          </span>
          <div className="flex gap-2 sm:ml-auto">
            <Button
              onClick={() => setShowDeleteDialog(true)}
              disabled={bulkActionLoading}
              variant="destructive"
              size="default"
              className="flex-1 sm:flex-none sm:size-auto"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </Button>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {onDeleteSessions && (
                <TableHead className="w-[50px]">
                  <Checkbox
                    checked={allPageSelected ? true : somePageSelected ? 'indeterminate' : false}
                    onCheckedChange={toggleSelectAll}
                    aria-label="Select all"
                  />
                </TableHead>
              )}
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('status')}
                  className="h-auto p-0 font-medium hover:bg-transparent"
                >
                  Status
                  <ArrowUpDown className="ml-2 h-4 w-4" />
                </Button>
              </TableHead>
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('caller')}
                  className="h-auto p-0 font-medium hover:bg-transparent"
                >
                  Caller
                  <ArrowUpDown className="ml-2 h-4 w-4" />
                </Button>
              </TableHead>
              {showVerified && (
                <TableHead className="w-[80px]">Verified</TableHead>
              )}
              {showOutcome && (
                <TableHead>
                  <Button
                    variant="ghost"
                    onClick={() => handleSort('routed_to')}
                    className="h-auto p-0 font-medium hover:bg-transparent"
                  >
                    Outcome
                    <ArrowUpDown className="ml-2 h-4 w-4" />
                  </Button>
                </TableHead>
              )}
              {showDuration && (
                <TableHead>
                  <Button
                    variant="ghost"
                    onClick={() => handleSort('duration')}
                    className="h-auto p-0 font-medium hover:bg-transparent"
                  >
                    Duration
                    <ArrowUpDown className="ml-2 h-4 w-4" />
                  </Button>
                </TableHead>
              )}
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('created_at')}
                  className="h-auto p-0 font-medium hover:bg-transparent"
                >
                  Date
                  <ArrowUpDown className="ml-2 h-4 w-4" />
                </Button>
              </TableHead>
              {hasRowActions && (
                <TableHead className="w-[70px]">Actions</TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedSessions.map((session) => (
              <TableRow
                key={session.session_id}
                data-state={selectedIds.has(session.session_id) && 'selected'}
                onClick={() => onRowClick?.(session)}
                className={`${onRowClick ? 'cursor-pointer' : ''} [&>td]:py-3 sm:[&>td]:py-2`}
              >
                {onDeleteSessions && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selectedIds.has(session.session_id)}
                      onCheckedChange={() => toggleSelect(session.session_id)}
                      aria-label="Select row"
                    />
                  </TableCell>
                )}
                <TableCell>
                  <Badge variant={getStatusVariant(session.status)}>
                    {getStatusDisplay(session.status)}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="font-medium">
                    {session.caller_name || formatPhone(session.caller_phone)}
                  </div>
                  {session.caller_name && session.caller_phone && (
                    <div className="text-sm text-muted-foreground">
                      {formatPhone(session.caller_phone)}
                    </div>
                  )}
                </TableCell>
                {showVerified && (
                  <TableCell>
                    {session.identity_verified ? (
                      <CheckCircle className="h-4 w-4 text-green-600" />
                    ) : (
                      <Minus className="h-4 w-4 text-muted-foreground" />
                    )}
                  </TableCell>
                )}
                {showOutcome && (
                  <TableCell className="text-muted-foreground">
                    {session.routed_to || '-'}
                  </TableCell>
                )}
                {showDuration && (
                  <TableCell className="text-muted-foreground">
                    {formatDuration(session.created_at, session.completed_at)}
                  </TableCell>
                )}
                <TableCell className="text-muted-foreground">
                  {formatDatetime(session.created_at)}
                </TableCell>
                {hasRowActions && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="h-9 w-9 p-0 sm:h-8 sm:w-8">
                          <span className="sr-only">Open menu</span>
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {onViewSession && (
                          <DropdownMenuItem onClick={() => onViewSession(session)}>
                            <Eye className="mr-2 h-4 w-4" />
                            View Details
                          </DropdownMenuItem>
                        )}
                        {onDeleteSession && (
                          <DropdownMenuItem
                            onClick={() => onDeleteSession(session)}
                            className="text-destructive focus:text-destructive"
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm text-muted-foreground text-center sm:text-left">
          {selectedSessions.length} of {filteredSessions.length} row(s) selected.
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6 lg:gap-8">
          <div className="flex items-center justify-center gap-2 sm:justify-start">
            <p className="text-sm font-medium">Rows per page</p>
            <Select
              value={`${pageSize}`}
              onValueChange={(value) => {
                setPageSize(Number(value));
                setPageIndex(0);
              }}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue placeholder={pageSize} />
              </SelectTrigger>
              <SelectContent side="top">
                {[10, 20, 30, 40, 50].map((size) => (
                  <SelectItem key={size} value={`${size}`}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-center gap-4">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPageIndex(p => p - 1)}
              disabled={pageIndex === 0}
              className="min-h-9"
            >
              Previous
            </Button>
            <span className="text-sm font-medium whitespace-nowrap">
              {pageIndex + 1} / {pageCount || 1}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPageIndex(p => p + 1)}
              disabled={pageIndex >= pageCount - 1}
              className="min-h-9"
            >
              Next
            </Button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {selectedSessions.length} call record(s)?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the selected call records.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
