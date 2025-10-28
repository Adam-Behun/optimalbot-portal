import { Settings, LogOut } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/mode-toggle';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { logout } from '../api';
import { removeAuthToken, getAuthUser } from '../lib/auth';
import { toast } from 'sonner';

export function SettingsMenu() {
  const navigate = useNavigate();
  const user = getAuthUser();

  const handleSignOut = async () => {
    try {
      await logout();
      removeAuthToken();
      toast.success('Signed out successfully');
      navigate('/login');
    } catch (error) {
      console.error('Error during logout:', error);
      removeAuthToken();
      navigate('/login');
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon">
          <Settings className="h-[1.2rem] w-[1.2rem]" />
          <span className="sr-only">Settings</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>
          {user?.email || 'Settings'}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        <div className="flex items-center justify-between px-2 py-2">
          <span className="text-sm">Theme</span>
          <ModeToggle />
        </div>

        <DropdownMenuSeparator />

        <DropdownMenuItem onClick={handleSignOut}>
          <LogOut className="mr-2 h-4 w-4" />
          <span>Sign out</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
