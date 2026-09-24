// A long-nosed rat in profile with a trailing tail and magic-wand sparkles.
// Keep strokes at the same weight as the surrounding Lucide controls.
export default function RemActionIcon({ size = 20, className = "" }) {
  return <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M7.5 18.5C4 21 1 19.5 1.5 16.5c.2-1.2.9-2 1.7-2.5" />
    <path d="M7.5 18.5C5.8 16 7.2 12 11 12h2.2c-.8-1.1-1-2.6.1-3.3 1.3-.8 2.9.3 2.8 1.7 0 .7-.3 1.2-.7 1.6l6.2 3.3c.7.4.5 1.2-.2 1.4l-5.1 1.1-1.1 2.7h2.3" />
    <path d="M16.3 17.8c-2 1-3.5 1.3-5.3 1.2m-.5-3.5c-1.5 0-2.4 1.4-1.7 2.8l1.2 2.2H7.5" />
    <circle cx="17.5" cy="15.1" r=".6" fill="currentColor" stroke="none" />
    <path className="rem-action-sparkle" d="m8 2 .8 2.2L11 5l-2.2.8L8 8l-.8-2.2L5 5l2.2-.8Z" />
    <path className="rem-action-sparkle" d="M20 3v4m-2-2h4" />
  </svg>;
}
