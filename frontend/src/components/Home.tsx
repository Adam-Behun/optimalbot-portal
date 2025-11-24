import { Navigation } from './Navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { getOrganization } from '@/lib/auth';

export function Home() {
  const org = getOrganization();

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Welcome{org?.name ? ` to ${org.name}` : ''}</h1>
          <p className="text-muted-foreground">
            Select a workflow to get started with patient management and calls
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Getting Started</CardTitle>
            <CardDescription>
              Use the Workflows tab above to select and manage your active workflow
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li>1. Navigate to Workflows to select your desired workflow</li>
              <li>2. Once selected, go to Dashboard to view call statistics</li>
              <li>3. Add patients and initiate authorization calls</li>
            </ul>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
