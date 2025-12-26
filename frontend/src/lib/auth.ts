// Authentication utilities

import { Organization } from '../types';

const TOKEN_KEY = 'auth_token';
const USER_KEY = 'auth_user';
const ORGANIZATION_KEY = 'organization_context';
const WORKFLOW_KEY = 'selected_workflow';

export interface AuthUser {
  user_id: string;
  email: string;
}

export const setAuthToken = (token: string): void => {
  sessionStorage.setItem(TOKEN_KEY, token);
};

export const getAuthToken = (): string | null => {
  return sessionStorage.getItem(TOKEN_KEY);
};

export const removeAuthToken = (): void => {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
  sessionStorage.removeItem(ORGANIZATION_KEY);
  sessionStorage.removeItem(WORKFLOW_KEY);
};

export const setAuthUser = (user: AuthUser): void => {
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
};

export const getAuthUser = (): AuthUser | null => {
  const userStr = sessionStorage.getItem(USER_KEY);
  if (!userStr) return null;
  try {
    return JSON.parse(userStr);
  } catch {
    return null;
  }
};

export const isAuthenticated = (): boolean => {
  return !!getAuthToken();
};

export const setOrganization = (org: Organization): void => {
  sessionStorage.setItem(ORGANIZATION_KEY, JSON.stringify(org));
};

export const getOrganization = (): Organization | null => {
  const orgStr = sessionStorage.getItem(ORGANIZATION_KEY);
  if (!orgStr) return null;
  try {
    return JSON.parse(orgStr);
  } catch {
    return null;
  }
};

export const removeOrganization = (): void => {
  sessionStorage.removeItem(ORGANIZATION_KEY);
};

export const setSelectedWorkflow = (workflow: string): void => {
  sessionStorage.setItem(WORKFLOW_KEY, workflow);
};

export const getSelectedWorkflow = (): string | null => {
  return sessionStorage.getItem(WORKFLOW_KEY);
};

export const removeSelectedWorkflow = (): void => {
  sessionStorage.removeItem(WORKFLOW_KEY);
};

// Helper to get the current workflow's schema
export const getCurrentWorkflowSchema = () => {
  const org = getOrganization();
  const workflow = getSelectedWorkflow();
  if (!org || !workflow) return null;
  return org.workflows?.[workflow]?.patient_schema || null;
};

// --- Auth Event System ---
// Used when logout needs to be triggered from non-React code (like API interceptors)

type LogoutCallback = () => void;
const logoutListeners: Set<LogoutCallback> = new Set();

// Emit logout event - triggers all registered callbacks
export const emitLogoutEvent = (): void => {
  logoutListeners.forEach((callback) => callback());
};

// Subscribe to logout events - returns unsubscribe function
export const onLogoutEvent = (callback: LogoutCallback): (() => void) => {
  logoutListeners.add(callback);
  return () => {
    logoutListeners.delete(callback);
  };
};
