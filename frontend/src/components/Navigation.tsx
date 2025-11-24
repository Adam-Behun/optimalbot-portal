import { Link } from 'react-router-dom';
import {
  Menubar,
  MenubarMenu,
  MenubarTrigger,
} from '@/components/ui/menubar';
import { Button } from '@/components/ui/button';
import { SettingsMenu } from './settings-menu';
import { ModeToggle } from './mode-toggle';
import { getSelectedWorkflow } from '@/lib/auth';
import { Home } from 'lucide-react';

// Top navigation bar - always visible
export function Navigation() {
  return (
    <nav className="border-b bg-background sticky top-0 z-50">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-16">
          <Menubar className="border-none bg-transparent">
            <MenubarMenu>
              <Link to="/home">
                <MenubarTrigger className="cursor-pointer">
                  <Home className="h-4 w-4" />
                </MenubarTrigger>
              </Link>
            </MenubarMenu>
            <MenubarMenu>
              <Link to="/workflows">
                <MenubarTrigger className="cursor-pointer font-semibold">Workflows</MenubarTrigger>
              </Link>
            </MenubarMenu>
          </Menubar>

          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => {}}>
              Support
            </Button>
            <ModeToggle />
            <SettingsMenu />
          </div>
        </div>
      </div>
    </nav>
  );
}

// Secondary navigation bar - only visible when a workflow is selected
export function WorkflowNavigation() {
  const selectedWorkflow = getSelectedWorkflow();
  const workflowLabel = selectedWorkflow
    ? selectedWorkflow.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
    : '';

  if (!selectedWorkflow) {
    return null;
  }

  return (
    <nav className="border-b bg-muted/30">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-12">
          <Menubar className="border-none bg-transparent">
            <MenubarMenu>
              <Link to="/dashboard">
                <MenubarTrigger className="cursor-pointer">Dashboard</MenubarTrigger>
              </Link>
            </MenubarMenu>
            <MenubarMenu>
              <Link to="/patient-list">
                <MenubarTrigger className="cursor-pointer">Patient List</MenubarTrigger>
              </Link>
            </MenubarMenu>
            <MenubarMenu>
              <Link to="/add-patient">
                <MenubarTrigger className="cursor-pointer">Add Patient</MenubarTrigger>
              </Link>
            </MenubarMenu>
          </Menubar>

          <span className="text-xs text-muted-foreground">
            {workflowLabel}
          </span>
        </div>
      </div>
    </nav>
  );
}
