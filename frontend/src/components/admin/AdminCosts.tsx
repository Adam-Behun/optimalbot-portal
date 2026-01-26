import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getAdminCosts, AdminCosts as AdminCostsType } from '@/api';
import { DollarSign, Calendar, CalendarDays, CalendarRange, RefreshCw } from 'lucide-react';

export function AdminCosts() {
  const [data, setData] = useState<AdminCostsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showOrgBreakdown, setShowOrgBreakdown] = useState(false);

  const fetchData = () => {
    setLoading(true);
    setError(null);
    getAdminCosts(showOrgBreakdown)
      .then(setData)
      .catch((err) => setError(err.message || 'Failed to load costs'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
  }, [showOrgBreakdown]);

  if (loading && !data) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Cost Report</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Cost Report</h1>
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={fetchData} variant="outline">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Cost Report</h1>
        <Button onClick={fetchData} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Cost Summary Cards */}
      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Today</CardTitle>
            <Calendar className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              ${data?.today?.cost_usd?.toFixed(4) ?? '0.0000'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.today?.call_count ?? 0} calls
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">This Week</CardTitle>
            <CalendarDays className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              ${data?.this_week?.cost_usd?.toFixed(4) ?? '0.0000'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.this_week?.call_count ?? 0} calls
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">This Month</CardTitle>
            <CalendarRange className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              ${data?.this_month?.cost_usd?.toFixed(4) ?? '0.0000'}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.this_month?.call_count ?? 0} calls
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Organization Breakdown Toggle */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <DollarSign className="h-5 w-5" />
              Costs by Organization (This Month)
            </CardTitle>
            <div className="flex items-center space-x-2">
              <Switch
                id="org-breakdown"
                checked={showOrgBreakdown}
                onCheckedChange={setShowOrgBreakdown}
              />
              <Label htmlFor="org-breakdown">Show breakdown</Label>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {showOrgBreakdown && data?.by_organization ? (
            data.by_organization.length > 0 ? (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Organization</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.by_organization.map((org) => (
                      <TableRow key={org.organization_id}>
                        <TableCell>{org.organization_name}</TableCell>
                        <TableCell className="text-right">{org.call_count}</TableCell>
                        <TableCell className="text-right font-mono">
                          ${org.cost_usd.toFixed(4)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <p className="text-muted-foreground text-center py-4">No organization data available</p>
            )
          ) : (
            <p className="text-muted-foreground text-center py-4">
              Enable the toggle above to see costs by organization
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
