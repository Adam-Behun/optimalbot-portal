"use client"

import { ColumnDef } from "@tanstack/react-table"
import { ArrowUpDown } from "lucide-react"
import { Checkbox } from "@/components/ui/checkbox"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Patient, SchemaField } from "@/types"
import { PatientActionsCell } from "./patient-actions-cell"
import { getCurrentWorkflowSchema } from "@/lib/auth"

interface ColumnsConfig {
  onActionComplete?: () => void
}

export const createColumns = (config: ColumnsConfig): ColumnDef<Patient>[] => {
  const schema = getCurrentWorkflowSchema();
  const schemaFields = schema?.fields || [];

  // Get fields that should display in list, sorted by display_order
  const displayFields = schemaFields
    .filter((f: SchemaField) => f.display_in_list)
    .sort((a: SchemaField, b: SchemaField) => a.display_order - b.display_order);

  // Base columns that always appear
  const baseColumns: ColumnDef<Patient>[] = [
    {
      id: "select",
      header: ({ table }) => (
        <Checkbox
          checked={
            table.getIsAllPageRowsSelected() ||
            (table.getIsSomePageRowsSelected() && "indeterminate")
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="Select all"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label="Select row"
          onClick={(e) => e.stopPropagation()}
        />
      ),
      enableSorting: false,
      enableHiding: false,
    },
    {
      accessorKey: "patient_name",
      header: "Patient Name",
      cell: ({ row }) => (
        <div className="font-medium">{row.getValue("patient_name")}</div>
      ),
    },
  ];

  // Dynamic columns from schema - read flat fields directly
  const dynamicColumns: ColumnDef<Patient>[] = displayFields.map((field: SchemaField) => ({
    id: field.key,
    header: field.label,
    accessorFn: (row: Patient) => row[field.key] || '',
    cell: ({ row }) => {
      const value = row.original[field.key];
      return <div>{value || '-'}</div>;
    },
  }));

  // Call status and actions columns
  const endColumns: ColumnDef<Patient>[] = [
    {
      accessorKey: "call_status",
      header: ({ column }) => {
        return (
          <Button
            variant="ghost"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Call Status
            <ArrowUpDown className="ml-2 h-4 w-4" />
          </Button>
        )
      },
      cell: ({ row }) => {
        const status = row.getValue("call_status") as Patient["call_status"]

        const statusConfig = {
          "Not Started": { variant: "secondary", label: "Not Started" },
          "In Progress": { variant: "default", label: "In Progress" },
          "Completed": { variant: "outline", label: "Completed" },
          "Completed - Left VM": { variant: "outline", label: "Completed - Left VM" },
          "Call Transferred": { variant: "outline", label: "Call Transferred" },
        } as const

        const statusCfg = statusConfig[status] || {
          variant: "secondary" as const,
          label: status || "Unknown"
        }

        return (
          <Badge variant={statusCfg.variant as "default" | "secondary" | "outline"}>
            {statusCfg.label}
          </Badge>
        )
      },
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <PatientActionsCell
          patient={row.original}
          onActionComplete={config.onActionComplete}
        />
      ),
      enableSorting: false,
      enableHiding: false,
    },
  ];

  return [...baseColumns, ...dynamicColumns, ...endColumns];
}