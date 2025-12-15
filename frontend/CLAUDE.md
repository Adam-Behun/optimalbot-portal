# Frontend CLAUDE.md

React SPA for healthcare voice AI portal. Displays workflow dashboards, patient lists, and call management.

## Tech Stack

- **Framework:** React 18 + TypeScript + Vite
- **Styling:** TailwindCSS + Shadcn/Radix UI
- **Routing:** React Router v6
- **HTTP:** Axios with JWT interceptors
- **State:** React Context (OrganizationContext) + localStorage

## Directory Structure

```
src/
├── api.ts              # Axios instance, all API calls
├── types.ts            # TypeScript interfaces (Patient, SchemaField, WorkflowConfig)
├── App.tsx             # Routes, providers (Theme, Organization, Router)
├── components/
│   ├── ui/             # Shadcn primitives (Button, Input, Select, Table, etc.)
│   ├── workflows/
│   │   ├── shared/     # DynamicForm, DynamicTable, TranscriptViewer, WorkflowLayout
│   │   ├── eligibility_verification/, patient_scheduling/, mainline/
│   └── LoginForm, ProtectedRoute, SidebarLayout, AppSidebar, etc.
├── contexts/           # OrganizationContext (org + workflow schemas)
├── lib/                # auth.ts (localStorage), utils.ts (date formatting)
└── hooks/
```

## Key Patterns

### Schema-Driven UI

Forms and tables are generated from `WorkflowConfig.patient_schema.fields`:

```typescript
interface SchemaField {
  key: string;           // Field name in patient document
  label: string;         // Display label
  type: 'string' | 'date' | 'datetime' | 'time' | 'phone' | 'select' | 'text';
  required: boolean;
  display_in_list: boolean;
  display_order: number;
  options?: string[];    // For select fields
  computed?: boolean;    // Bot-updated, not user-editable
}
```

### DynamicForm

- Renders form fields based on schema
- Filters out `computed` fields (bot-only)
- Supports CSV upload via `showCsvUpload` + `onBulkSubmit`
- Usage: `<DynamicForm schema={schema} onSubmit={handleSubmit} />`

### DynamicTable

- Renders patient list from `display_in_list: true` fields
- Built-in filtering, sorting, pagination, row selection
- Props: `onStartCalls`, `onDeletePatients`, `onViewPatient`, `onEditPatient`, `onStartCall`

### Workflow Component Structure

Each workflow folder (`eligibility_verification/`, `patient_scheduling/`, `mainline/`) contains:
- `index.ts` - Barrel exports
- `*Dashboard.tsx` - Stats/overview page
- `*PatientList.tsx` or `*CallList.tsx` - List with DynamicTable
- `*AddPatient.tsx` - Form page with DynamicForm (if applicable)

## State Management

### OrganizationContext

- Provides `organization` object with branding and workflow configs
- `getWorkflowSchema(workflowName)` returns `WorkflowConfig` for a workflow
- Set during login from `AuthResponse.organization`

### Auth Flow

1. Login → receives JWT + organization → stored in localStorage
2. `api.ts` interceptor adds `Authorization: Bearer <token>` to all requests
3. 401 response → auto-logout and redirect to `/`

## Adding a New Workflow

1. Create folder: `src/components/workflows/<workflow_name>/`
2. Add components:
   - `<WorkflowName>Dashboard.tsx` - Use stats cards pattern from existing dashboards
   - `<WorkflowName>CallList.tsx` - Use `DynamicTable` with schema from context
   - `<WorkflowName>AddPatient.tsx` (optional) - Use `DynamicForm`
3. Create `index.ts` barrel export
4. Add routes in `App.tsx`:
   ```tsx
   <Route path="/workflows/<workflow_name>/dashboard" element={...} />
   <Route path="/workflows/<workflow_name>/calls" element={...} />
   ```
5. Add sidebar entry in `AppSidebar.tsx` (if workflow enabled in org config)

## Adding a New UI Component

1. Run `npx shadcn@latest add <component>` → installs to `src/components/ui/`
2. Import with `@/components/ui/<component>`

## API Calls

All in `api.ts`. Key functions:
- `getPatients(workflow?)` - Fetch patients, optionally filtered
- `addPatient(data)` / `addPatientsBulk(patients[])`
- `startCall(patientId, phoneNumber, clientName)`
- `login(email, password, organizationSlug?)`

## Conventions

- `@/` path alias for `src/` imports
- Toast notifications via `sonner` (`toast.success()`, `toast.error()`)
- Icons from `lucide-react`
- Form validation inline (no form library)
- Date formatting via `lib/utils.ts`
