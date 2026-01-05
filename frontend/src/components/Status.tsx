import { useState, useEffect } from 'react';
import axios from 'axios';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, XCircle, AlertCircle, RefreshCw, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

interface ServiceStatus {
  healthy: boolean;
  status: string;
  latency_ms?: number;
}

interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  services: {
    mongodb?: ServiceStatus;
    openai?: ServiceStatus;
    deepgram?: ServiceStatus;
    daily?: ServiceStatus;
    pipecat?: ServiceStatus;
    cartesia?: ServiceStatus;
  };
}

const SERVICE_LABELS: Record<string, string> = {
  mongodb: 'Database',
  openai: 'AI Language Model',
  deepgram: 'Speech Recognition',
  daily: 'Telephony',
  pipecat: 'Voice Pipeline',
  cartesia: 'Text-to-Speech',
};

const StatusIcon = ({ healthy }: { healthy: boolean | undefined }) => {
  if (healthy === undefined) {
    return <AlertCircle className="h-5 w-5 text-muted-foreground" />;
  }
  return healthy ? (
    <CheckCircle className="h-5 w-5 text-green-500" />
  ) : (
    <XCircle className="h-5 w-5 text-red-500" />
  );
};

const OverallStatusBadge = ({ status }: { status: string }) => {
  const variants: Record<string, { variant: 'default' | 'destructive' | 'outline' | 'secondary'; label: string }> = {
    healthy: { variant: 'default', label: 'All Systems Operational' },
    degraded: { variant: 'secondary', label: 'Partial Outage' },
    unhealthy: { variant: 'destructive', label: 'Major Outage' },
    unknown: { variant: 'outline', label: 'Checking...' },
  };

  const config = variants[status] || variants.unknown;

  return (
    <Badge variant={config.variant} className="text-sm px-3 py-1">
      {config.label}
    </Badge>
  );
};

export function Status() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const fetchHealth = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await axios.get<HealthResponse>(`${API_BASE_URL}/health`);
      setHealth(response.data);
      setLastChecked(new Date());
    } catch (err) {
      setError('Unable to fetch system status');
      setHealth(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const formatTime = (date: Date | null) => {
    if (!date) return 'Never';
    return date.toLocaleTimeString();
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto px-4 py-8 max-w-4xl">
        {/* Header */}
        <div className="flex flex-col items-center gap-4 mb-8">
          <div className="flex items-center gap-3">
            <Activity className="h-8 w-8 text-primary" />
            <h1 className="text-3xl font-bold">System Status</h1>
          </div>
          <OverallStatusBadge status={health?.status || 'unknown'} />
          <p className="text-muted-foreground text-sm">
            Last updated: {formatTime(lastChecked)}
          </p>
        </div>

        {/* Error State */}
        {error && (
          <Card className="mb-6 border-destructive">
            <CardContent className="pt-6">
              <div className="flex items-center gap-3 text-destructive">
                <XCircle className="h-5 w-5" />
                <span>{error}</span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Service Cards */}
        <div className="grid gap-4 md:grid-cols-2">
          {health?.services && Object.entries(health.services).map(([service, status]) => (
            <Card key={service} className={!status?.healthy ? 'border-destructive/50' : ''}>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg">
                    {SERVICE_LABELS[service] || service}
                  </CardTitle>
                  <StatusIcon healthy={status?.healthy} />
                </div>
                <CardDescription className="text-sm capitalize">
                  {status?.status || 'Unknown'}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {status?.latency_ms !== undefined && (
                  <p className="text-xs text-muted-foreground">
                    Response time: {status.latency_ms}ms
                  </p>
                )}
              </CardContent>
            </Card>
          ))}

          {/* Loading skeleton */}
          {loading && !health && (
            <>
              {[1, 2, 3, 4].map((i) => (
                <Card key={i} className="animate-pulse">
                  <CardHeader className="pb-2">
                    <div className="h-5 bg-muted rounded w-32" />
                    <div className="h-4 bg-muted rounded w-20 mt-2" />
                  </CardHeader>
                  <CardContent>
                    <div className="h-3 bg-muted rounded w-24" />
                  </CardContent>
                </Card>
              ))}
            </>
          )}
        </div>

        {/* Refresh Button */}
        <div className="flex justify-center mt-8">
          <Button
            variant="outline"
            onClick={fetchHealth}
            disabled={loading}
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh Status
          </Button>
        </div>

        {/* Footer */}
        <div className="mt-12 text-center text-sm text-muted-foreground">
          <p>Status page auto-refreshes every 30 seconds</p>
          <p className="mt-1">
            For urgent issues, contact{' '}
            <a href="mailto:support@optimalbot.ai" className="text-primary hover:underline">
              support@optimalbot.ai
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
