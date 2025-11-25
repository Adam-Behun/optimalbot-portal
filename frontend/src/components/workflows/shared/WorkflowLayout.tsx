import { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useOrganization } from '@/contexts/OrganizationContext';
import { Navigation } from '@/components/Navigation';

interface WorkflowLayoutProps {
  workflowName: string;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}

export function WorkflowLayout({
  workflowName,
  title,
  children,
  actions,
}: WorkflowLayoutProps) {
  const { getWorkflowSchema } = useOrganization();
  const workflow = getWorkflowSchema(workflowName);
  const displayName = workflow?.display_name || workflowName;

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        {/* Breadcrumb */}
        <nav className="flex items-center text-sm text-muted-foreground">
          <Link to="/workflows" className="hover:text-foreground">
            Workflows
          </Link>
          <ChevronRight className="h-4 w-4 mx-1" />
          <Link
            to={`/workflows/${workflowName}/dashboard`}
            className="hover:text-foreground"
          >
            {displayName}
          </Link>
          <ChevronRight className="h-4 w-4 mx-1" />
          <span className="text-foreground">{title}</span>
        </nav>

        {/* Title and actions */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link to={`/workflows/${workflowName}/dashboard`}>
              <Button variant="ghost" size="icon">
                <ArrowLeft className="h-4 w-4" />
              </Button>
            </Link>
            <h1 className="text-2xl font-semibold">{title}</h1>
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>

        {/* Content */}
        <div>{children}</div>
      </div>
    </>
  );
}
