import ExcelJS from 'exceljs';
import { Patient, SchemaField } from '@/types';

export async function exportToCSV(
  patients: Patient[],
  fields: SchemaField[],
  filename: string
): Promise<void> {
  // Build header row from field labels
  const headers = fields.map(f => f.label);

  // Create workbook and worksheet
  const workbook = new ExcelJS.Workbook();
  const worksheet = workbook.addWorksheet('Patients');

  // Add header row
  worksheet.addRow(headers);

  // Add data rows
  for (const patient of patients) {
    const row = fields.map(f => {
      const value = patient[f.key];
      if (value === null || value === undefined) return '';
      return String(value);
    });
    worksheet.addRow(row);
  }

  // Generate CSV buffer and download
  const buffer = await workbook.csv.writeBuffer();
  const blob = new Blob([buffer], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = url;
  link.download = `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
