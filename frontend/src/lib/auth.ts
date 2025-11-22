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
  localStorage.setItem(TOKEN_KEY, token);
};

export const getAuthToken = (): string | null => {
  return localStorage.getItem(TOKEN_KEY);
};

export const removeAuthToken = (): void => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ORGANIZATION_KEY);
  localStorage.removeItem(WORKFLOW_KEY);
};

export const setAuthUser = (user: AuthUser): void => {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};

export const getAuthUser = (): AuthUser | null => {
  const userStr = localStorage.getItem(USER_KEY);
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
  localStorage.setItem(ORGANIZATION_KEY, JSON.stringify(org));
};

export const getOrganization = (): Organization | null => {
  const orgStr = localStorage.getItem(ORGANIZATION_KEY);
  if (!orgStr) return null;
  try {
    return JSON.parse(orgStr);
  } catch {
    return null;
  }
};

export const removeOrganization = (): void => {
  localStorage.removeItem(ORGANIZATION_KEY);
};

export const setSelectedWorkflow = (workflow: string): void => {
  localStorage.setItem(WORKFLOW_KEY, workflow);
};

export const getSelectedWorkflow = (): string | null => {
  return localStorage.getItem(WORKFLOW_KEY);
};

export const removeSelectedWorkflow = (): void => {
  localStorage.removeItem(WORKFLOW_KEY);
};

// Helper to get the current workflow's schema
export const getCurrentWorkflowSchema = () => {
  const org = getOrganization();
  const workflow = getSelectedWorkflow();
  if (!org || !workflow) return null;
  return org.workflows?.[workflow]?.patient_schema || null;
};
