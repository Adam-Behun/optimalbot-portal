"use client"

import { ColumnDef } from "@tanstack/react-table"
import { ArrowUpDown } from "lucide-react"
import { Checkbox } from "@/components/ui/checkbox"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Patient } from "@/types"
import { PatientActionsCell } from "./patient-actions-cell"

interface ColumnsConfig {
  onActionComplete?: () => void
}

export const createColumns = (config: ColumnsConfig): ColumnDef<Patient>[] => [
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
  {
    accessorKey: "facility_name",
    header: "Facility",
  },
  {
    accessorKey: "insurance_company_name",
    header: ({ column }) => {
      return (
        <Button
          variant="ghost"
          onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
        >
          Insurance
          <ArrowUpDown className="ml-2 h-4 w-4" />
        </Button>
      )
    },
  },
  {
    accessorKey: "prior_auth_status",
    header: "Auth Status",
  },
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
      } as const

      const config = statusConfig[status]

      return (
        <Badge variant={config.variant as "default" | "secondary" | "outline"}>
          {config.label}
        </Badge>
      )
    },
  },
  {
    id: "actions",
    cell: ({ row }) => (
      <PatientActionsCell 
        patient={row.original}
        onActionComplete={config.onActionComplete}
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
]