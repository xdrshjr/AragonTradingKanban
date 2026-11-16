/** Download helper: the export endpoints answer with `Content-Disposition`. */

export function triggerDownload(url: string, filename: string): void {
  if (typeof document === 'undefined') return;
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener noreferrer';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

export function exportFilenameHint(title: string, version: number, format: 'html' | 'md'): string {
  const safe = (title || 'report').replace(/[^\w\u4e00-\u9fa5.-]+/g, '-').slice(0, 60);
  return `${safe}-v${version}.${format}`;
}
