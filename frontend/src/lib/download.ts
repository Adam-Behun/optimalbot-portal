/**
 * Download utility functions.
 */

/**
 * Trigger a file download from a Blob.
 *
 * @param blob - The blob data to download
 * @param filename - The filename for the downloaded file
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
