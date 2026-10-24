import {
  File, FileArchive, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, FileVideo, Folder, Presentation,
} from "lucide-react";
import { cn } from "@/lib/format";

const MAP: Record<string, { icon: typeof File; color: string }> = {
  folder: { icon: Folder, color: "text-amber-500" },
  image: { icon: FileImage, color: "text-pink-500" },
  video: { icon: FileVideo, color: "text-violet-500" },
  audio: { icon: FileAudio, color: "text-teal-500" },
  pdf: { icon: FileText, color: "text-red-500" },
  archive: { icon: FileArchive, color: "text-orange-600" },
  spreadsheet: { icon: FileSpreadsheet, color: "text-emerald-600" },
  document: { icon: FileText, color: "text-blue-600" },
  presentation: { icon: Presentation, color: "text-orange-500" },
  code: { icon: FileCode, color: "text-slate-500" },
  text: { icon: FileText, color: "text-slate-500" },
  file: { icon: File, color: "text-muted-foreground" },
};

export function FileIcon({ kind, className }: { kind: string; className?: string }) {
  const m = MAP[kind] || MAP.file;
  const Icon = m.icon;
  return <Icon className={cn("h-5 w-5 shrink-0", m.color, className)} />;
}

export function kindOf(name: string, isDir = false): string {
  if (isDir) return "folder";
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "avif"].includes(ext)) return "image";
  if (["mp4", "mkv", "mov", "webm", "avi", "m4v"].includes(ext)) return "video";
  if (["mp3", "wav", "flac", "m4a", "ogg", "aac"].includes(ext)) return "audio";
  if (ext === "pdf") return "pdf";
  if (["zip", "7z", "rar", "tar", "gz"].includes(ext)) return "archive";
  if (["xlsx", "xls", "csv", "tsv", "ods"].includes(ext)) return "spreadsheet";
  if (["docx", "doc", "odt", "rtf"].includes(ext)) return "document";
  if (["md", "txt", "log"].includes(ext)) return "text";
  return "file";
}
