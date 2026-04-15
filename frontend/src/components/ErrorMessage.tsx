interface ErrorMessageProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

export default function ErrorMessage({
  message,
  onRetry,
  className = "",
}: ErrorMessageProps) {
  return (
    <div
      className={`p-4 rounded-lg bg-red-900/20 border border-red-700 ${className}`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <p className="text-red-400 text-sm">{message}</p>
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="text-red-400 hover:text-red-300 text-sm font-medium transition-colors"
          >
            重试
          </button>
        )}
      </div>
    </div>
  );
}
