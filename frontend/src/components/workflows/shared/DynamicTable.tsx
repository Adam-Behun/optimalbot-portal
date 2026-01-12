import { useState, useMemo, useEffect } from 'react';
import { WorkflowConfig, Patient, SchemaField } from '@/types';
import { useBreakpoint } from '@/hooks/use-mobile';
import { formatDate, formatDatetime, formatTime } from '@/lib/utils';
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
  DropdownMenuCheckboxItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { ArrowUpDown, Phone, Trash2, MoreHorizontal, Pencil, Eye, Columns3, Download, RotateCcw } from 'lucide-react';
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

interface DynamicTableProps {
  schema: WorkflowConfig;
  patients: Patient[];
  workflowName: string;
  onRowClick?: (patient: Patient) => void;
  loading?: boolean;
  onStartCalls?: (patients: Patient[]) => void | Promise<void>;
  onDeletePatients?: (patients: Patient[]) => void | Promise<void>;
  onExportPatients?: (patients: Patient[]) => void;
  onViewPatient?: (patient: Patient) => void;
  onEditPatient?: (patient: Patient) => void;
  onStartCall?: (patient: Patient) => void;
  onDeletePatient?: (patient: Patient) => void;
}

// Format value based on field type
function formatValue(value: unknown, field: SchemaField): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const strValue = String(value);

  switch (field.type) {
    case 'date':
      return formatDate(strValue);
    case 'datetime':
      return formatDatetime(strValue);
    case 'time':
      return formatTime(strValue);
    case 'phone':
      return formatPhone(strValue);
    default:
      return strValue;
  }
}

// Format phone number for display
function formatPhone(phone: string): string {
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.length === 11 && cleaned.startsWith('1')) {
    return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
  }
  if (cleaned.length === 10) {
    return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
  }
  return phone;
}

// Get badge variant based on call status
function getStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'Completed':
    case 'Supervisor Dialed':
      return 'default';
    case 'In Progress':
    case 'Dialing':
      return 'secondary';
    case 'Failed':
      return 'destructive';
    default:
      return 'outline';
  }
}

type SortDirection = 'asc' | 'desc' | null;

export function DynamicTable({
  schema,
  patients,
  workflowName,
  onRowClick,
  loading,
  onStartCalls,
  onDeletePatients,
  onExportPatients,
  onViewPatient,
  onEditPatient,
  onStartCall,
  onDeletePatient,
}: DynamicTableProps) {
  const hasRowActions = onViewPatient || onEditPatient || onStartCall || onDeletePatient;
  const breakpoint = useBreakpoint();
  const storageKey = `optimalbot_columns_${workflowName}`;

  // All fields sorted by display_order (for column selector)
  const allFields = useMemo(() => {
    return [...schema.patient_schema.fields]
      .filter(f => !f.computed) // Exclude computed fields from selector
      .sort((a, b) => a.display_order - b.display_order);
  }, [schema.patient_schema.fields]);

  // Default columns (fields with display_in_list: true)
  const defaultColumns = useMemo(() => {
    return allFields.filter(f => f.display_in_list).map(f => f.key);
  }, [allFields]);

  // Visible columns state - initialize from localStorage or defaults
  const [visibleColumns, setVisibleColumns] = useState<string[]>(() => {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch {
        return defaultColumns;
      }
    }
    return defaultColumns;
  });

  // Persist to localStorage when visibleColumns changes
  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(visibleColumns));
  }, [visibleColumns, storageKey]);

  // Toggle column visibility
  const toggleColumn = (key: string, checked: boolean) => {
    if (checked) {
      setVisibleColumns(prev => [...prev, key]);
    } else {
      setVisibleColumns(prev => prev.filter(k => k !== key));
    }
  };

  // Reset to defaults
  const resetColumns = () => {
    setVisibleColumns(defaultColumns);
  };

  // Get columns filtered by visibility and breakpoint
  const columns = useMemo(() => {
    return allFields
      .filter((f) => {
        if (!visibleColumns.includes(f.key)) return false;

        // Filter by display_priority based on current breakpoint
        const priority = f.display_priority || 'desktop';

        if (breakpoint === 'mobile') {
          return priority === 'mobile';
        } else if (breakpoint === 'tablet') {
          return priority === 'mobile' || priority === 'tablet';
        }
        // Desktop shows all columns
        return true;
      });
  }, [allFields, visibleColumns, breakpoint]);

  // Find the patient_name field for filtering (search all fields, not just visible ones)
  const patientNameField = schema.patient_schema.fields.find(f => f.key === 'patient_name' || f.key.includes('name'));

  // State
  const [nameFilter, setNameFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sortField, setSortField] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [pageSize, setPageSize] = useState(10);
  const [pageIndex, setPageIndex] = useState(0);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [bulkActionLoading, setBulkActionLoading] = useState(false);

  // Filter and sort patients
  const filteredPatients = useMemo(() => {
    let result = [...patients];

    // Filter by name
    if (nameFilter && patientNameField) {
      const lower = nameFilter.toLowerCase();
      result = result.filter(p => {
        const value = p[patientNameField.key];
        return value && String(value).toLowerCase().includes(lower);
      });
    }

    // Filter by status
    if (statusFilter !== 'all') {
      result = result.filter(p => p.call_status === statusFilter);
    }

    // Sort
    if (sortField && sortDirection) {
      result.sort((a, b) => {
        const aVal = a[sortField] ?? '';
        const bVal = b[sortField] ?? '';
        const comparison = String(aVal).localeCompare(String(bVal));
        return sortDirection === 'asc' ? comparison : -comparison;
      });
    }

    return result;
  }, [patients, nameFilter, statusFilter, sortField, sortDirection, patientNameField]);

  // Pagination
  const pageCount = Math.ceil(filteredPatients.length / pageSize);
  const paginatedPatients = filteredPatients.slice(
    pageIndex * pageSize,
    (pageIndex + 1) * pageSize
  );

  // Selection
  const selectedPatients = patients.filter(p => selectedIds.has(p.patient_id));
  const allPageSelected = paginatedPatients.length > 0 &&
    paginatedPatients.every(p => selectedIds.has(p.patient_id));
  const somePageSelected = paginatedPatients.some(p => selectedIds.has(p.patient_id));

  const toggleSelectAll = () => {
    if (allPageSelected) {
      const newSelected = new Set(selectedIds);
      paginatedPatients.forEach(p => newSelected.delete(p.patient_id));
      setSelectedIds(newSelected);
    } else {
      const newSelected = new Set(selectedIds);
      paginatedPatients.forEach(p => newSelected.add(p.patient_id));
      setSelectedIds(newSelected);
    }
  };

  const toggleSelect = (patientId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(patientId)) {
      newSelected.delete(patientId);
    } else {
      newSelected.add(patientId);
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

  const handleBulkStartCalls = async () => {
    if (!onStartCalls || selectedPatients.length === 0) return;
    setBulkActionLoading(true);
    try {
      await onStartCalls(selectedPatients);
      setSelectedIds(new Set());
    } finally {
      setBulkActionLoading(false);
    }
  };

  const handleBulkDelete = async () => {
    if (!onDeletePatients || selectedPatients.length === 0) return;
    setBulkActionLoading(true);
    setShowDeleteDialog(false);
    try {
      await onDeletePatients(selectedPatients);
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

  if (patients.length === 0) {
    return (
      <div className="text-center py-12 border rounded-lg bg-card">
        <p className="text-muted-foreground">No patients found</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
          <Input
            placeholder="Filter by name..."
            value={nameFilter}
            onChange={(e) => {
              setNameFilter(e.target.value);
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
              <SelectValue placeholder="Call Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Statuses</SelectItem>
              <SelectItem value="Not Started">Not Started</SelectItem>
              <SelectItem value="In Progress">In Progress</SelectItem>
              <SelectItem value="Completed">Completed</SelectItem>
              <SelectItem value="Failed">Failed</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Column Selector */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline">
              <Columns3 className="mr-2 h-4 w-4" />
              Columns
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            {allFields.map((field) => (
              <DropdownMenuCheckboxItem
                key={field.key}
                checked={visibleColumns.includes(field.key)}
                onCheckedChange={(checked) => toggleColumn(field.key, !!checked)}
              >
                {field.label}
              </DropdownMenuCheckboxItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={resetColumns}>
              <RotateCcw className="mr-2 h-4 w-4" />
              Reset to defaults
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Bulk Actions Bar */}
      {selectedPatients.length > 0 && (onStartCalls || onDeletePatients || onExportPatients) && (
        <div className="flex flex-col gap-3 p-4 bg-muted rounded-lg sm:flex-row sm:items-center sm:gap-2">
          <span className="text-sm font-medium">
            {selectedPatients.length} selected
          </span>
          <div className="flex gap-2 sm:ml-auto">
            {onStartCalls && (
              <Button
                onClick={handleBulkStartCalls}
                disabled={bulkActionLoading}
                variant="default"
                size="default"
                className="flex-1 sm:flex-none sm:size-auto"
              >
                <Phone className="mr-2 h-4 w-4" />
                Start Calls
              </Button>
            )}
            {onExportPatients && (
              <Button
                onClick={() => onExportPatients(selectedPatients)}
                variant="outline"
                size="default"
                className="flex-1 sm:flex-none sm:size-auto"
              >
                <Download className="mr-2 h-4 w-4" />
                Export ({selectedPatients.length})
              </Button>
            )}
            {onDeletePatients && (
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
            )}
          </div>
        </div>
      )}

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {(onStartCalls || onDeletePatients) && (
                <TableHead className="w-[50px]">
                  <Checkbox
                    checked={allPageSelected ? true : somePageSelected ? 'indeterminate' : false}
                    onCheckedChange={toggleSelectAll}
                    aria-label="Select all"
                  />
                </TableHead>
              )}
              {columns.map((field) => (
                <TableHead key={field.key}>
                  <Button
                    variant="ghost"
                    onClick={() => handleSort(field.key)}
                    className="h-auto p-0 font-medium hover:bg-transparent"
                  >
                    {field.label}
                    <ArrowUpDown className="ml-2 h-4 w-4" />
                  </Button>
                </TableHead>
              ))}
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('call_status')}
                  className="h-auto p-0 font-medium hover:bg-transparent"
                >
                  Call Status
                  <ArrowUpDown className="ml-2 h-4 w-4" />
                </Button>
              </TableHead>
              {hasRowActions && (
                <TableHead className="w-[70px]">Actions</TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedPatients.map((patient) => (
              <TableRow
                key={patient.patient_id}
                data-state={selectedIds.has(patient.patient_id) && 'selected'}
                onClick={() => onRowClick?.(patient)}
                className={`${onRowClick ? 'cursor-pointer' : ''} [&>td]:py-3 sm:[&>td]:py-2`}
              >
                {(onStartCalls || onDeletePatients) && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={selectedIds.has(patient.patient_id)}
                      onCheckedChange={() => toggleSelect(patient.patient_id)}
                      aria-label="Select row"
                    />
                  </TableCell>
                )}
                {columns.map((field) => (
                  <TableCell key={field.key}>
                    {field.key === 'patient_name' ? (
                      <div className="font-medium">{formatValue(patient[field.key], field)}</div>
                    ) : (
                      formatValue(patient[field.key], field)
                    )}
                  </TableCell>
                ))}
                <TableCell>
                  <Badge variant={getStatusVariant(patient.call_status)}>
                    {patient.call_status}
                  </Badge>
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
                        {onViewPatient && (
                          <DropdownMenuItem onClick={() => onViewPatient(patient)}>
                            <Eye className="mr-2 h-4 w-4" />
                            View Details
                          </DropdownMenuItem>
                        )}
                        {onEditPatient && (
                          <DropdownMenuItem onClick={() => onEditPatient(patient)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            Edit Patient
                          </DropdownMenuItem>
                        )}
                        {onStartCall && patient.call_status === 'Not Started' && (
                          <DropdownMenuItem onClick={() => onStartCall(patient)}>
                            <Phone className="mr-2 h-4 w-4" />
                            Start Call
                          </DropdownMenuItem>
                        )}
                        {onDeletePatient && (
                          <DropdownMenuItem
                            onClick={() => onDeletePatient(patient)}
                            className="text-destructive focus:text-destructive"
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete Patient
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
          {selectedPatients.length} of {filteredPatients.length} row(s) selected.
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
              Delete {selectedPatients.length} patient(s)?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the selected patient records.
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
