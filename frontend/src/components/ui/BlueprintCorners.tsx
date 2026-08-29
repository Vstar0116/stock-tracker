/** The 4 corner-tick decorations used on every `.blueprint`-styled button
 * and card. Was copy-pasted as this same 4-line snippet in 14 places across
 * 6 page files before this. */
export function BlueprintCorners() {
  return (
    <>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
    </>
  )
}
