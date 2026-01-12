import * as XLSX from 'xlsx';
import { Patient, SchemaField } from '@/types';

export function exportToCSV(
  patients: Patient[],
  fields: SchemaField[],
  filename: string
): void {
  // Build header row from field labels
  const headers = fields.map(f => f.label);

  // Build data rows
  const data = patients.map(p =>
    fields.map(f => {
      const value = p[f.key];
      if (value === null || value === undefined) return '';
      return String(value);
    })
  );

  // Create worksheet and workbook
  const ws = XLSX.utils.aoa_to_sheet([headers, ...data]);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Patients');

  // Download as CSV
  XLSX.writeFile(wb, `${filename}.csv`);
}
