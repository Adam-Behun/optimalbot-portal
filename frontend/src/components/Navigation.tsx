import { Link } from 'react-router-dom';
import {
  Menubar,
  MenubarMenu,
  MenubarTrigger,
} from '@/components/ui/menubar';
import { Button } from '@/components/ui/button';
import { SettingsMenu } from './SettingsMenu';
import { ModeToggle } from './ModeToggle';
import { Home } from 'lucide-react';

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
