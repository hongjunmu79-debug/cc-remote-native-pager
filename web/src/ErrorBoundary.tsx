import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

// Keep render failures recoverable without exposing stack traces or internal
// paths on a remotely accessible screen.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("React error boundary:", error, info);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <main style={{
          display: "grid", placeItems: "center", alignContent: "center", gap: 12,
          padding: 24, minHeight: "100%", textAlign: "center",
        }}>
          <strong>页面需要重新载入</strong>
          <span>会话记录已保存在本机，重新载入后会自动恢复。</span>
          <button type="button" onClick={() => window.location.reload()}>
            重新载入
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
