/** An unset value. It reads quieter than any real value beside it: the label's
 *  own size in the tertiary label color, and the words rather than a dash. */
export function NotSet() {
  return <span className="mc-unset">Not set</span>;
}
