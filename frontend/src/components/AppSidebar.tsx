import { useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Home,
  ChevronRight,
  FileCheck,
  MessageSquare,
  BarChart3,
  LogOut,
  Settings,
  Moon,
  Sun,
  ClipboardCheck,
  Calendar,
  HelpCircle,
  Phone,
  LucideIcon,
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTheme } from "@/components/ThemeProvider";
import { logout } from "../api";
import { removeAuthToken, getAuthUser } from "../lib/auth";
import { toast } from "sonner";
import { useOrganization } from "../contexts/OrganizationContext";

// Icons for different workflow types
const workflowIcons: Record<string, LucideIcon> = {
  eligibility_verification: FileCheck,
  patient_questions: MessageSquare,
  patient_scheduling: Calendar,
  mainline: Phone,
  eligibility: ClipboardCheck,
  general_questions: HelpCircle,
};

// Workflow-specific sub-items configuration
const workflowSubItems: Record<string, Array<{ title: string; urlSuffix: string }>> = {
  eligibility_verification: [
    { title: "Dashboard", urlSuffix: "dashboard" },
    { title: "Patients", urlSuffix: "patients" },
  ],
  patient_questions: [
    { title: "Dashboard", urlSuffix: "dashboard" },
    { title: "Calls", urlSuffix: "calls" },
  ],
  patient_scheduling: [
    { title: "Dashboard", urlSuffix: "dashboard" },
    { title: "Calls", urlSuffix: "calls" },
  ],
  mainline: [
    { title: "Dashboard", urlSuffix: "dashboard" },
    { title: "Calls", urlSuffix: "calls" },
  ],
  // Default sub-items for other workflows
  default: [
    { title: "Dashboard", urlSuffix: "dashboard" },
  ],
};

const mainNavItems = [
  {
    title: "Home",
    url: "/home",
    icon: Home,
  },
  {
    title: "Reports",
    url: "/custom-reports",
    icon: BarChart3,
  },
];

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const location = useLocation();
  const { theme, setTheme } = useTheme();
  const user = getAuthUser();
  const { organization } = useOrganization();

  // Build dynamic workflow navigation from organization data
  const workflowNavItems = useMemo(() => {
    if (!organization?.workflows) return [];

    return Object.entries(organization.workflows)
      .filter(([, config]) => config.enabled)
      .map(([workflowId, config]) => {
        const Icon = workflowIcons[workflowId] || HelpCircle;
        const subItems = workflowSubItems[workflowId] || workflowSubItems.default;

        return {
          id: workflowId,
          title: config.display_name || workflowId.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
          icon: Icon,
          items: subItems.map(item => ({
            title: item.title,
            url: `/workflows/${workflowId}/${item.urlSuffix}`,
          })),
        };
      });
  }, [organization]);

  const handleSignOut = async () => {
    try {
      await logout();
      removeAuthToken();
      toast.success("Signed out successfully");
      window.location.href = "https://optimalbot.ai";
    } catch (error) {
      console.error("Error during logout:", error);
      removeAuthToken();
      window.location.href = "https://optimalbot.ai";
    }
  };

  // Check if a URL is active - exact match for main nav, startsWith for sub-items
  const isActive = (url: string) => {
    // Exact match for top-level items
    if (location.pathname === url) return true;
    // For workflow sub-items, match if we're on a child route (e.g., /patients/123)
    if (url.includes("/workflows/")) {
      return location.pathname.startsWith(url);
    }
    return false;
  };

  // Check if any item in a workflow section is active
  const isWorkflowActive = (items: { url: string }[]) =>
    items.some((item) => location.pathname.startsWith(item.url));

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader className="p-4">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" className="cursor-default hover:bg-transparent">
              <span className="font-semibold">{organization?.name || "MyRobot"}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent className="px-2">
        {/* Main Navigation */}
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {mainNavItems.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)} tooltip={item.title}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Dynamic Workflows */}
        {workflowNavItems.length > 0 && (
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {workflowNavItems.map((workflow) => (
                  <Collapsible
                    key={workflow.id}
                    asChild
                    defaultOpen={isWorkflowActive(workflow.items)}
                    className="group/collapsible"
                  >
                    <SidebarMenuItem>
                      <CollapsibleTrigger asChild>
                        <SidebarMenuButton tooltip={workflow.title}>
                          <workflow.icon />
                          <span>{workflow.title}</span>
                          <ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
                        </SidebarMenuButton>
                      </CollapsibleTrigger>
                      <CollapsibleContent>
                        <SidebarMenuSub>
                          {workflow.items.map((item) => (
                            <SidebarMenuSubItem key={item.title}>
                              <SidebarMenuSubButton asChild isActive={isActive(item.url)}>
                                <Link to={item.url}>
                                  <span>{item.title}</span>
                                </Link>
                              </SidebarMenuSubButton>
                            </SidebarMenuSubItem>
                          ))}
                        </SidebarMenuSub>
                      </CollapsibleContent>
                    </SidebarMenuItem>
                  </Collapsible>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton tooltip="Settings">
                  <Settings />
                  <span>{user?.email || "Settings"}</span>
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                className="w-[--radix-dropdown-menu-trigger-width] min-w-56 rounded-lg"
                side="top"
                align="start"
                sideOffset={4}
              >
                <DropdownMenuItem onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                  {theme === "dark" ? <Sun className="mr-2 h-4 w-4" /> : <Moon className="mr-2 h-4 w-4" />}
                  <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut}>
                  <LogOut className="mr-2 h-4 w-4" />
                  <span>Sign out</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
