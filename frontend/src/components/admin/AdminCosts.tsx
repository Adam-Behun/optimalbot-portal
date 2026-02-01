import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getAdminCosts, exportCosts, AdminCosts as AdminCostsType } from '@/api';
import { downloadBlob } from '@/lib/download';
import { DollarSign, Calendar, CalendarDays, CalendarRange, RefreshCw, Download } from 'lucide-react';
import { toast } from 'sonner';

function formatCurrency(value: number, decimals = 4): string {
  return `$${value.toFixed(decimals)}`;
}

function formatRate(cost: number, count: number, decimals = 4): string {
  if (count === 0) return '-';
  return formatCurrency(cost / count, decimals);
}

export function AdminCosts() {
  const [data, setData] = useState<AdminCostsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const fetchData = () => {
    setLoading(true);
    setError(null);
    getAdminCosts()
      .then(setData)
      .catch((err) => setError(err.message || 'Failed to load costs'))
      .finally(() => setLoading(false));
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const { blob, filename } = await exportCosts();
      downloadBlob(blob, filename);
      toast.success('Export downloaded');
    } catch {
      toast.error('Failed to export costs');
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

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

  // Calculate component percentages
  const totalComponentCost = data?.by_component?.reduce((sum, c) => sum + c.cost_usd, 0) || 0;
  const componentPercentages = data?.by_component?.map((c) => ({
    ...c,
    percentage: totalComponentCost > 0 ? (c.cost_usd / totalComponentCost) * 100 : 0,
  })) || [];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Cost Report</h1>
        <div className="flex gap-2">
          <Button onClick={handleExport} variant="outline" size="sm" disabled={exporting}>
            <Download className="mr-2 h-4 w-4" />
            {exporting ? 'Exporting...' : 'Export Financials'}
          </Button>
          <Button onClick={fetchData} variant="outline" size="sm">
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Today</CardTitle>
            <Calendar className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              {formatCurrency(data?.today?.cost_usd ?? 0)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.today?.call_count ?? 0} calls
            </p>
            <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
              <span>{formatRate(data?.today?.cost_usd ?? 0, data?.today?.call_count ?? 0)}/call</span>
              <span>{formatRate(data?.today?.cost_usd ?? 0, data?.today?.total_minutes ?? 0)}/min</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">WTD</CardTitle>
            <CalendarDays className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              {formatCurrency(data?.wtd?.cost_usd ?? 0)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.wtd?.call_count ?? 0} calls
            </p>
            <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
              <span>{formatRate(data?.wtd?.cost_usd ?? 0, data?.wtd?.call_count ?? 0)}/call</span>
              <span>{formatRate(data?.wtd?.cost_usd ?? 0, data?.wtd?.total_minutes ?? 0)}/min</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">MTD</CardTitle>
            <CalendarRange className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">
              {formatCurrency(data?.mtd?.cost_usd ?? 0)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data?.mtd?.call_count ?? 0} calls
            </p>
            <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
              <span>{formatRate(data?.mtd?.cost_usd ?? 0, data?.mtd?.call_count ?? 0)}/call</span>
              <span>{formatRate(data?.mtd?.cost_usd ?? 0, data?.mtd?.total_minutes ?? 0)}/min</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Cost by Component (MTD) */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <DollarSign className="h-5 w-5" />
            Cost by Component (MTD)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {componentPercentages.map((comp) => (
            <div key={comp.component} className="space-y-1">
              <div className="flex justify-between text-sm">
                <span>{comp.component}</span>
                <span className="text-muted-foreground">
                  {formatCurrency(comp.cost_usd)} ({comp.percentage.toFixed(0)}%)
                </span>
              </div>
              <Progress value={comp.percentage} className="h-2" />
            </div>
          ))}
          {componentPercentages.length === 0 && (
            <p className="text-muted-foreground text-center py-4">No data available</p>
          )}
        </CardContent>
      </Card>

      {/* Cost by Workflow (MTD) */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <DollarSign className="h-5 w-5" />
            Cost by Workflow (MTD)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data?.by_workflow && data.by_workflow.length > 0 ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Workflow</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead className="text-right">$/call</TableHead>
                    <TableHead className="text-right">$/min</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.by_workflow.map((wf) => (
                    <TableRow key={wf.workflow}>
                      <TableCell className="font-medium">{wf.workflow}</TableCell>
                      <TableCell className="text-right">{wf.call_count}</TableCell>
                      <TableCell className="text-right font-mono">
                        {formatCurrency(wf.cost_usd)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatRate(wf.cost_usd, wf.call_count)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatRate(wf.cost_usd, wf.total_minutes)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-muted-foreground text-center py-4">No data available</p>
          )}
        </CardContent>
      </Card>

      {/* Cost by Organization (MTD) */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <DollarSign className="h-5 w-5" />
            Cost by Organization (MTD)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data?.by_organization && data.by_organization.length > 0 ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Organization</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead className="text-right">$/call</TableHead>
                    <TableHead className="text-right">$/min</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.by_organization.map((org) => (
                    <TableRow key={org.organization_id}>
                      <TableCell className="font-medium">{org.organization_name}</TableCell>
                      <TableCell className="text-right">{org.call_count}</TableCell>
                      <TableCell className="text-right font-mono">
                        {formatCurrency(org.cost_usd)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatRate(org.cost_usd, org.call_count)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatRate(org.cost_usd, org.total_minutes)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-muted-foreground text-center py-4">No data available</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
