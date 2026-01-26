import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { getAdminCalls, AdminCall, AdminCallsResponse } from '@/api';
import { RefreshCw, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { formatDatetime } from '@/lib/utils';
import { StatusBadge } from '@/components/ui/status-badge';

export function AdminCalls() {
  const navigate = useNavigate();
  const [data, setData] = useState<AdminCallsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    getAdminCalls({
      page,
      page_size: 50,
      status: statusFilter || undefined,
      search: searchQuery || undefined,
    })
      .then(setData)
      .catch((err) => setError(err.message || 'Failed to load calls'))
      .finally(() => setLoading(false));
  }, [page, statusFilter, searchQuery]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSearch = () => {
    setPage(1);
    setSearchQuery(searchInput);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">All Calls</h1>
        <Button onClick={fetchData} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-4">
        <div className="flex gap-2 flex-1">
          <Input
            placeholder="Search by session ID or phone..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyPress={handleKeyPress}
            className="max-w-sm"
          />
          <Button onClick={handleSearch} variant="outline" size="icon">
            <Search className="h-4 w-4" />
          </Button>
        </div>
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); }}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All statuses</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="running">Running</SelectItem>
            <SelectItem value="transferred">Transferred</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={fetchData} variant="outline">
            Retry
          </Button>
        </div>
      ) : (
        <>
          <div className="text-sm text-muted-foreground mb-2">
            {data?.total_count ?? 0} total calls
          </div>

          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Session ID</TableHead>
                  <TableHead>Organization</TableHead>
                  <TableHead>Workflow</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Cost</TableHead>
                  <TableHead>Date</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center py-8 text-muted-foreground">
                      Loading...
                    </TableCell>
                  </TableRow>
                ) : !data?.calls?.length ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center py-8 text-muted-foreground">
                      No calls found
                    </TableCell>
                  </TableRow>
                ) : (
                  data.calls.map((call: AdminCall) => (
                    <TableRow
                      key={call.session_id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => navigate(`/admin/calls/${call.session_id}`)}
                    >
                      <TableCell className="font-mono text-sm">
                        {call.session_id?.substring(0, 12)}...
                      </TableCell>
                      <TableCell>{call.organization_name}</TableCell>
                      <TableCell>{call.workflow}</TableCell>
                      <TableCell><StatusBadge status={call.status} size="sm" /></TableCell>
                      <TableCell>
                        {call.total_cost_usd != null ? `$${call.total_cost_usd.toFixed(4)}` : '-'}
                      </TableCell>
                      <TableCell>{formatDatetime(call.created_at)}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <div className="text-sm text-muted-foreground">
                Page {data.page} of {data.total_pages}
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage(p => Math.min(data.total_pages, p + 1))}
                  disabled={page === data.total_pages}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
