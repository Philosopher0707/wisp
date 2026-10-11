import type { ComponentType } from 'react';

/**
 * The canonical type of an icon component.
 *
 * Lucide icons are `ForwardRefExoticComponent`s, which are **not** assignable to
 * `React.FC`. Declaring `icon: React.FC<{ size?: number }>` at each use site
 * therefore rejects every lucide icon — and five files each had their own copy
 * of that declaration, so the same mistake had to be fixed five times.
 *
 * `ComponentType` accepts both lucide icons and hand-written function
 * components, so this is the one type to use.
 */
export type IconComponent = ComponentType<{ size?: number }>;

export {
  Pencil,
  Search,
  Grid3x3,
  Bot,
  Pin,
  Folder,
  Sparkles,
  Shield,
  Mic,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Plus,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Settings,
  User,
  Square,
  CheckSquare,
  SlidersHorizontal,
  Paperclip,
  Trash2,
  X,
  File,
  FolderOpen,
  Code2,
  Copy,
  RefreshCw,
  Download,
  FileText,
  CornerDownLeft,
  GitBranch,
  History,
  Keyboard,
  Moon,
  Sun,
  ClipboardList,
  Pause,
  Play,
  AlertTriangle,
  AlertCircle,
  Lightbulb,
  Info,
  Terminal,
  Globe,
  FileDiff,
  PanelLeft,
  Check,
  ArrowLeft,
  ArrowRight,
  Cpu,
  Circle,
} from 'lucide-react';
