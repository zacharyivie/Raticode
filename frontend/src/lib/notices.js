// How long a notice stays, in milliseconds; 0 keeps it until it is dismissed. Errors stay
// until dismissed, a notice with a button (such as Undo) stays long enough to use it, and
// confirmations get out of the way quickly.
export const noticeDuration = ({ tone, action } = {}) => tone === "danger" ? 0 : action ? 10000 : 4000;
