import { AlertTriangle, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  /** what failed, for the message ("the details panel") */
  name: string;
  children: ReactNode;
  /** called when the user dismisses the error (e.g. close the panel) */
  onReset?: () => void;
}

/**
 * Keeps a crash in one part of the UI (a panel, the chat) from blanking the whole app. Shows a small card
 * with the error and a retry button instead; details go to the console for debugging.
 */
export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.name}]`, error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash" role="alert">
        <AlertTriangle size={16} />
        <div className="grow">
          <b>Something went wrong in {this.props.name}.</b>
          <p className="muted small">{this.state.error.message}</p>
        </div>
        <button className="btn btn--sm" onClick={this.reset}>
          <RotateCcw size={13} /> Retry
        </button>
      </div>
    );
  }
}
