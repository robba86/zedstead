// Catches render/effect crashes so a broken page shows a message instead of a
// blank screen. React has no hook equivalent — this has to be a class.
//
// Without one, any uncaught throw unmounts the entire tree and the user sees
// white. That is much worse than the error itself, and it hides the stack.

import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Unhandled error in', this.props.name || 'component', error, info)
  }

  handleRetry = () => {
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="error-boundary">
        <h2>{this.props.title || 'Something went wrong'}</h2>
        <p className="error-boundary-message">{String(error.message || error)}</p>
        <p className="error-boundary-hint">
          The browser console has the full stack trace.
        </p>
        <div className="inline-form">
          <button className="btn btn-primary btn-small" onClick={this.handleRetry}>
            Try again
          </button>
          <button
            className="btn btn-ghost btn-small"
            onClick={() => window.location.reload()}
          >
            Reload page
          </button>
        </div>
      </div>
    )
  }
}
