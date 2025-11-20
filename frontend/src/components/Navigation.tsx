import { Link } from 'react-router-dom';
import {
  Menubar,
  MenubarMenu,
  MenubarTrigger,
} from '@/components/ui/menubar';
import { SettingsMenu } from './settings-menu';
import { ModeToggle } from './mode-toggle';

export function Navigation() {
  return (
    <nav className="border-b bg-background sticky top-0 z-50">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-16">
          <Menubar className="border-none bg-transparent">
            <MenubarMenu>
              <Link to="/dashboard">
                <MenubarTrigger className="cursor-pointer">Dashboard</MenubarTrigger>
              </Link>
            </MenubarMenu>
            <MenubarMenu>
              <Link to="/workflows">
                <MenubarTrigger className="cursor-pointer">Workflows</MenubarTrigger>
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

          <div className="flex items-center gap-2">
            <ModeToggle />
            <SettingsMenu />
          </div>
        </div>
      </div>
    </nav>
  );
}
