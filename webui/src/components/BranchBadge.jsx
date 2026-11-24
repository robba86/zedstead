// Marks an Experimental server wherever it could be mistaken for the stable
// one (plans/66): on its Instances row and in the status panel. A stable server shows nothing. The sidebar has no badge: its instance picker
// says Experimental and takes the warning colour (InstanceSwitch, Layout.jsx).
export default function BranchBadge({ branch }) {
  if (branch !== 'experimental') return null
  return <span className="status-pill status-pill-warning branch-badge">Experimental</span>
}
