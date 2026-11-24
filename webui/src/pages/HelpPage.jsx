import PageIntro from '../components/PageIntro.jsx'
import { DOCS_URL, PAGES_URL } from '../routes.js'

// The help moved to zedstead.com in 2026 Q4: it was app/data/HELP.md, fetched
// from /api/help and rendered here. The sidebar's Help entry links straight to
// the site; since 2026 Q4 the site has no Help page either (its parts are
// The web UI page and Troubleshooting). This route stays so a bookmark to /help says where it went rather
// than landing on an unknown page.
export default function HelpPage() {
  return (
    <div className="page help-page">
      <div className="page-header">
        <h1>Docs</h1>
      </div>

      <PageIntro page="/help" />

      <div className="card">
        <p>
          <a href={PAGES_URL} target="_blank" rel="noopener noreferrer">Every page of the app</a>
          {' '}and the order to do things in the first time are on zedstead.com.
        </p>
        <p>
          The rest of the documentation (install, troubleshooting, reverse proxy, every command) is at{' '}
          <a href={DOCS_URL} target="_blank" rel="noopener noreferrer">zedstead.com/docs</a>.
        </p>
      </div>
    </div>
  )
}
