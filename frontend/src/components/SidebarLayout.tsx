import { useLocation } from "react-router-dom";
import { AppSidebar } from "./AppSidebar";
import { Separator } from "@/components/ui/separator";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { ChevronRight } from "lucide-react";

interface SidebarLayoutProps {
  children: React.ReactNode;
}

// Map paths to readable page titles
const pathNames: Record<string, string> = {
  home: "Home",
  workflows: "Workflows",
  prior_auth: "Prior Authorization",
  patient_questions: "Patient Questions",
  dashboard: "Dashboard",
  patients: "Patients",
  calls: "Calls",
  add: "Add Patient",
  edit: "Edit Patient",
  "custom-reports": "Reports",
};

function getBreadcrumbs(pathname: string): string[] {
  const segments = pathname.split("/").filter(Boolean);
  const breadcrumbs: string[] = [];

  for (const segment of segments) {
    // Check if it's an ID segment (MongoDB ObjectId)
    const isIdSegment = /^[a-f0-9]{24}$/i.test(segment);

    if (isIdSegment) {
      breadcrumbs.push("Details");
    } else {
      const name = pathNames[segment] || segment.charAt(0).toUpperCase() + segment.slice(1);
      breadcrumbs.push(name);
    }
  }

  return breadcrumbs;
}

export function SidebarLayout({ children }: SidebarLayoutProps) {
  const location = useLocation();
  const breadcrumbs = getBreadcrumbs(location.pathname);

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 data-[orientation=vertical]:h-4" />
          <nav className="flex items-center gap-1 text-sm">
            {breadcrumbs.map((crumb, index) => (
              <span key={index} className="flex items-center gap-1">
                {index > 0 && (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <span className={index === breadcrumbs.length - 1 ? "font-medium" : "text-muted-foreground"}>
                  {crumb}
                </span>
              </span>
            ))}
          </nav>
        </header>
        <div className="flex flex-1 flex-col p-6">
          {children}
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
