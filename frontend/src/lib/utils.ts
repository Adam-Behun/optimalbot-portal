import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Parse ISO datetime string to Date object, treating timestamps without timezone as UTC.
 */
export function parseDateTime(value: string): Date {
  // If the string has timezone info (+00:00 or Z), parse directly
  // Otherwise, assume UTC by appending 'Z'
  if (value.includes('+') || value.includes('Z') || value.includes('-', 10)) {
    return new Date(value);
  }
  return new Date(value + 'Z');
}

/**
 * Format ISO date (YYYY-MM-DD) to localized date string.
 * Returns original value if parsing fails.
 */
export function formatDate(value: string): string {
  if (!value) return '-';
  // Handle ISO date format (YYYY-MM-DD) - display as local date
  const date = new Date(value + 'T00:00:00');
  if (isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

/**
 * Format ISO datetime to localized datetime string in user's timezone.
 * Returns original value if parsing fails.
 */
export function formatDatetime(value: string): string {
  if (!value) return '-';
  const date = parseDateTime(value);
  if (isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/**
 * Format 24-hour time (HH:MM) to 12-hour format with AM/PM.
 * Returns original value if not in expected format.
 */
export function formatTime(value: string): string {
  if (!value) return '-';
  if (value.match(/^\d{2}:\d{2}$/)) {
    const [hours, minutes] = value.split(':').map(Number);
    const period = hours >= 12 ? 'PM' : 'AM';
    const displayHours = hours % 12 || 12;
    return `${displayHours}:${minutes.toString().padStart(2, '0')} ${period}`;
  }
  return value;
}
