import { Component, ErrorInfo, ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('CADia UI error', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="boot-screen">
        <div className="brand-mark large">C</div>
        <h2>Could not load the CAD workspace</h2>
        <p>{this.state.error.message}</p>
        <button className="primary" onClick={() => window.location.reload()}>Reload</button>
      </main>
    )
  }
}
