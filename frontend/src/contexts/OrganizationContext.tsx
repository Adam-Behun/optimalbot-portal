import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { Organization, WorkflowConfig } from '@/types';
import { getOrganization, setOrganization as storeOrganization } from '@/lib/auth';

interface OrganizationContextType {
  organization: Organization | null;
  loading: boolean;
  error: string | null;
  getWorkflowSchema: (workflowName: string) => WorkflowConfig | null;
  updateOrganization: (org: Organization) => void;
  clearOrganization: () => void;
}

const OrganizationContext = createContext<OrganizationContextType | undefined>(undefined);

export function OrganizationProvider({ children }: { children: React.ReactNode }) {
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Initialize from localStorage on mount
  // Organization is set during login (from AuthResponse) and stored in localStorage
  useEffect(() => {
    try {
      const storedOrg = getOrganization();
      if (storedOrg) {
        setOrganization(storedOrg);
      }
    } catch (err) {
      setError('Failed to load organization from storage');
      console.error('Error loading organization:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Update organization (called after login with AuthResponse.organization)
  const updateOrganization = useCallback((org: Organization) => {
    setOrganization(org);
    storeOrganization(org);
    setError(null);
  }, []);

  // Clear organization (called on logout)
  const clearOrganization = useCallback(() => {
    setOrganization(null);
  }, []);

  // Get workflow schema by name
  const getWorkflowSchema = useCallback((workflowName: string): WorkflowConfig | null => {
    return organization?.workflows?.[workflowName] || null;
  }, [organization]);

  return (
    <OrganizationContext.Provider value={{
      organization,
      loading,
      error,
      getWorkflowSchema,
      updateOrganization,
      clearOrganization
    }}>
      {children}
    </OrganizationContext.Provider>
  );
}

export function useOrganization() {
  const context = useContext(OrganizationContext);
  if (!context) {
    throw new Error('useOrganization must be used within OrganizationProvider');
  }
  return context;
}
